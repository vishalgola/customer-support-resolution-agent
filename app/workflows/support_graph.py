from dataclasses import asdict

from datetime import datetime, timedelta, timezone

from langgraph.types import interrupt
from sqlalchemy import select

from app.db.models import ActionProposal, Approval
from app.services.payload import hash_payload
from app.services.refunds import ApprovalInvalid, describe_outcome, execute_approved_refund

from langgraph.graph import END, START, StateGraph

from app.agents.validation import validate_proposal
from app.config import settings
from app.db.models import Ticket
from app.db.session import SessionLocal
from app.policies.retrieval import search_policy
from app.schemas.workflow import ResolutionProposal, SupportState
from app.services.audit import add_audit
from app.services.eligibility import get_refund_eligibility
from app.tools.read_tools import get_order, get_payment_status, get_return_status

CATEGORY_QUERIES = {
    "refund_request": "refund eligibility, return window, inspection and approval",
    "order_status": "shipping delay and late delivery",
    "damaged_item": "damaged product reporting window and resolution options",
}

ESCALATE_MSG = ("I've passed your case to our support team for review. "
                "No changes have been made to your order or payment.")


def _rupees(minor: int) -> str:
    return f"₹{minor / 100:,.2f}"


def _finish(state: SupportState, *, ticket_status: str | None, event: str,
            outcome: str, response: str) -> dict:
    with SessionLocal() as db:
        ticket = db.get(Ticket, state["ticket_id"])
        if ticket_status:
            ticket.status = ticket_status
        add_audit(db, state["ticket_id"], "system", event, {
            "outcome": outcome,
            "proposal": state.get("proposal"),
            "validation_errors": state.get("validation_errors", []),
            "errors": state.get("errors", []),
        })
        db.commit()
    return {"outcome": outcome, "final_response": response}


# ---------- nodes ----------

def load_ticket(state: SupportState) -> dict:
    with SessionLocal() as db:
        ticket = db.get(Ticket, state["ticket_id"])
        if ticket is None:
            raise ValueError("Ticket not found")
        return {
            "customer_id": ticket.customer_id,  # identity comes from the ticket, never the model
            "customer_message": ticket.summary,
            "order_id": ticket.order_id,
            "evidence_refs": [], "errors": [], "validation_errors": [],
        }


def make_classify_node(classify_fn):
    def classify(state: SupportState) -> dict:
        result = classify_fn(state["customer_message"])
        updates = {"category": result.category, "missing_information": result.missing_information}
        if not state.get("order_id") and result.order_id:
            updates["order_id"] = result.order_id  # a hint only; ownership is checked by the tools
        with SessionLocal() as db:
            ticket = db.get(Ticket, state["ticket_id"])
            ticket.category = result.category
            add_audit(db, state["ticket_id"], "system", "classified", {"category": result.category})
            db.commit()
        return updates
    return classify


def investigate(state: SupportState) -> dict:
    cid, oid = state["customer_id"], state["order_id"]
    with SessionLocal() as db:
        order = get_order(db, cid, oid)
        if not order.success:
            return {"order_id": None, "errors": ["ORDER_NOT_FOUND"]}
        ret = get_return_status(db, cid, oid)
        pay = get_payment_status(db, cid, oid)

        evidence = [f"order_{oid}"]
        latest = ret.data["latest_return"]
        if latest:
            evidence.append(f"return_{latest['return_id']}")
        evidence += [f"refund_op_{r['id']}" for r in pay.data["refunds"]]

        eligibility = None
        if state["category"] == "refund_request":
            r = get_refund_eligibility(db, cid, oid)
            eligibility = {"decision": r.decision.value, "reason_code": r.reason_code,
                           "message": r.message,
                           "refundable_amount_minor": r.refundable_amount_minor}

    return {
        "verified_facts": {"order": order.data, "return": latest, "payment": pay.data},
        "evidence_refs": evidence,
        "eligibility": eligibility,
    }


def retrieve_policy(state: SupportState) -> dict:
    queries = [state["customer_message"],
               CATEGORY_QUERIES.get(state["category"], state["customer_message"])]
    best = {}
    with SessionLocal() as db:
        for q in queries:
            for hit in search_policy(db, q, top_k=4, min_score=settings.policy_min_score):
                if hit.ref not in best or hit.score > best[hit.ref].score:
                    best[hit.ref] = hit
    hits = sorted(best.values(), key=lambda h: -h.score)[:5]
    updates = {"policy_hits": [asdict(h) for h in hits], "policy_refs": [h.ref for h in hits]}
    if not hits:
        updates["errors"] = state.get("errors", []) + ["NO_POLICY_FOUND"]
    return updates


def make_propose_node(propose_fn):
    def propose(state: SupportState) -> dict:
        last_error = None
        for _ in range(2):  # bounded repair: one retry, then give up
            try:
                proposal = propose_fn(state).model_dump()
                proposal["order_id"] = state["order_id"]  # server-controlled, never the model's choice
                return {"proposal": proposal}
            except Exception as exc:  # invalid or failed structured output
                last_error = str(exc)[:300]
        return {"proposal": None,
                "errors": state.get("errors", []) + [f"PROPOSAL_FAILED: {last_error}"]}
    return propose


def validate(state: SupportState) -> dict:
    if not state.get("proposal"):
        return {"outcome": "escalated", "validation_errors": ["NO_PROPOSAL"]}
    proposal = ResolutionProposal(**state["proposal"])
    errors = validate_proposal(
        proposal,
        category=state["category"],
        order_id=state["order_id"],
        eligibility=state.get("eligibility"),
        known_evidence=set(state["evidence_refs"]),
        retrieved_refs=set(state["policy_refs"]),
    )
    if errors:
        return {"outcome": "escalated", "validation_errors": errors}
    outcome = {"answer_only": "answered",
               "request_refund_approval": "awaiting_approval",
               "escalate": "escalated"}[proposal.action]
    return {"outcome": outcome, "validation_errors": []}


# ---------- finishing nodes (templates, so wording always matches the real state) ----------

def finish_answer(state: SupportState) -> dict:
    return _finish(state, ticket_status="resolved", event="answered", outcome="answered",
                   response=state["proposal"]["explanation"])


APPROVAL_TTL = timedelta(hours=24)
PAYLOAD_FIELDS = ("action", "order_id", "amount_minor", "currency",
                  "reason", "evidence_refs", "policy_refs")


def create_approval(state: SupportState) -> dict:
    """Persist an immutable proposal and a pending approval. Safe to re-run."""
    proposal = state["proposal"]
    payload = {k: proposal[k] for k in PAYLOAD_FIELDS}
    with SessionLocal() as db:
        ap = db.scalar(select(ActionProposal).where(ActionProposal.run_id == state["run_id"]))
        if ap is None:
            ap = ActionProposal(run_id=state["run_id"], action_type=payload["action"],
                                payload=payload, payload_hash=hash_payload(payload))
            db.add(ap)
            db.flush()
            db.add(Approval(proposal_id=ap.id,
                            expires_at=datetime.now(timezone.utc) + APPROVAL_TTL))
            db.get(Ticket, state["ticket_id"]).status = "awaiting_approval"
            add_audit(db, state["ticket_id"], "system", "approval_requested",
                      {"proposal_id": ap.id, "payload_hash": ap.payload_hash})
            db.commit()
        approval = db.scalar(select(Approval).where(Approval.proposal_id == ap.id))
        proposal_id, approval_id = ap.id, approval.id
    return {
        "proposal_id": proposal_id,
        "approval_id": approval_id,
        "final_response": (f"Your refund request for {_rupees(payload['amount_minor'])} is awaiting "
                           "approval by our support team. No refund has been issued yet."),
    }


def wait_for_decision(state: SupportState) -> dict:
    # Only the pause lives here. LangGraph re-runs an interrupted node from its start on
    # resume, so nothing with side effects may sit in this node.
    interrupt({"approval_id": state["approval_id"], "proposal_id": state["proposal_id"]})
    return {}


def process_decision(state: SupportState) -> dict:
    with SessionLocal() as db:
        decision = db.get(Approval, state["approval_id"]).decision  # the stored record is the authority

    if decision == "approved":
        try:
            result = execute_approved_refund(state["proposal_id"])
        except ApprovalInvalid as exc:
            state = {**state, "errors": state.get("errors", []) + [exc.code]}
            return _finish(state, ticket_status="escalated", event="approval_invalid",
                           outcome="approval_invalid",
                           response="The details of your case changed after review, so the approval "
                                    "is no longer valid. The case has been passed to our support team.")
        outcome, ticket_status, message = describe_outcome(result)
        return _finish(state, ticket_status=ticket_status, event=outcome,
                       outcome=outcome, response=message)
    if decision == "rejected":
        return _finish(state, ticket_status="escalated", event="approval_rejected",
                       outcome="rejected",
                       response="Our support team reviewed your request and could not approve the "
                                "refund. A team member will follow up. No refund has been issued.")
    if decision == "expired":
        return _finish(state, ticket_status="escalated", event="approval_expired",
                       outcome="expired",
                       response="Your request wasn't reviewed in time, so it has been passed to our "
                                "support team. No refund has been issued.")
    raise RuntimeError("Resumed without a stored decision")


def escalate(state: SupportState) -> dict:
    return _finish(state, ticket_status="escalated", event="escalated",
                   outcome="escalated", response=ESCALATE_MSG)


def ask_customer(state: SupportState) -> dict:
    if "ORDER_NOT_FOUND" in state.get("errors", []):
        text = ("I couldn't find that order on your account. "
                "Could you double-check the order number?")
    elif state.get("missing_information"):
        text = "To continue, I need: " + "; ".join(state["missing_information"]) + "."
    else:
        text = "Could you share your order number so I can look into this?"
    return _finish(state, ticket_status=None, event="information_requested",
                   outcome="needs_info", response=text)


# ---------- routing ----------

def route_after_classify(state: SupportState) -> str:
    if state["category"] == "other":
        return "escalate"
    return "investigate" if state.get("order_id") else "ask_customer"


def route_after_investigate(state: SupportState) -> str:
    return "retrieve_policy" if state.get("order_id") else "ask_customer"


def route_after_policy(state: SupportState) -> str:
    return "propose" if state.get("policy_hits") else "escalate"


def route_after_validate(state: SupportState) -> str:
        return {"answered": "finish_answer", "awaiting_approval": "create_approval",
            "escalated": "escalate"}[state["outcome"]]


def build_graph(classify_fn, propose_fn, checkpointer=None):
    g = StateGraph[SupportState, None, SupportState, SupportState](SupportState)
    g.add_node("load_ticket", load_ticket)
    g.add_node("classify", make_classify_node(classify_fn))
    g.add_node("investigate", investigate)
    g.add_node("retrieve_policy", retrieve_policy)
    g.add_node("propose", make_propose_node(propose_fn))
    g.add_node("validate", validate)
    g.add_node("finish_answer", finish_answer)
    g.add_node("create_approval", create_approval)
    g.add_node("wait_for_decision", wait_for_decision)
    g.add_node("process_decision", process_decision)
    g.add_node("escalate", escalate)
    g.add_node("ask_customer", ask_customer)

    g.add_edge(START, "load_ticket")
    g.add_edge("load_ticket", "classify")
    g.add_conditional_edges("classify", route_after_classify,
                            ["escalate", "investigate", "ask_customer"])
    g.add_conditional_edges("investigate", route_after_investigate,
                            ["retrieve_policy", "ask_customer"])
    g.add_conditional_edges("retrieve_policy", route_after_policy, ["propose", "escalate"])
    g.add_edge("propose", "validate")
    g.add_conditional_edges("validate", route_after_validate,
                            ["finish_answer", "create_approval", "escalate"])
    g.add_edge("create_approval", "wait_for_decision")
    g.add_edge("wait_for_decision", "process_decision")
    for node in ("finish_answer", "process_decision", "escalate", "ask_customer"):
        g.add_edge(node, END)
    return g.compile(checkpointer=checkpointer)