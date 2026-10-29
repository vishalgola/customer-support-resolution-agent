import pytest
from sqlalchemy import func, select
from uuid import uuid4

from langgraph.checkpoint.memory import MemorySaver

from app.db.models import WorkflowRun

from app.db.models import RefundOperation, Ticket
from app.db.session import SessionLocal
from app.policies.ingest import ingest_all
from app.schemas.workflow import Classification, ResolutionProposal
from app.workflows.support_graph import build_graph
from scripts.seed_data import seed

ASHA, RAVI = 1, 2  # ids from seed order


@pytest.fixture(scope="module", autouse=True)
def fresh_data():
    seed()
    with SessionLocal() as db:
        ingest_all(db)


def make_ticket(customer_id, order_id, summary):
    with SessionLocal() as db:
        t = Ticket(customer_id=customer_id, order_id=order_id, summary=summary)
        db.add(t)
        db.commit()
        return t.id


def classifier(category, order_id=None):
    return lambda message: Classification(category=category, order_id=order_id)


def honest_proposer(state):
    elig = state.get("eligibility") or {}
    base = dict(order_id=state["order_id"], reason="test",
                evidence_refs=state["evidence_refs"], policy_refs=state["policy_refs"][:1])
    if elig.get("decision") == "eligible":
        return ResolutionProposal(action="request_refund_approval",
                                  amount_minor=elig["refundable_amount_minor"],
                                  explanation="Your return qualifies.", **base)
    return ResolutionProposal(action="answer_only", explanation="Here is the status.", **base)


def run(ticket_id, classify_fn, propose_fn=honest_proposer):
    with SessionLocal() as db:
        wr = WorkflowRun(ticket_id=ticket_id, thread_id=f"test-{uuid4().hex}", status="running")
        db.add(wr)
        db.commit()
        run_id, thread_id = wr.id, wr.thread_id
    graph = build_graph(classify_fn, propose_fn, checkpointer=MemorySaver())
    return graph.invoke({"ticket_id": ticket_id, "run_id": run_id},
                        {"configurable": {"thread_id": thread_id}})


def ticket_status(tid):
    with SessionLocal() as db:
        return db.get(Ticket, tid).status


def refund_op_count(order_id):
    with SessionLocal() as db:
        return db.scalar(select(func.count()).select_from(RefundOperation)
                         .where(RefundOperation.order_id == order_id))


def test_eligible_refund_awaits_approval_and_moves_no_money():
    before = refund_op_count(1)
    tid = make_ticket(ASHA, 1, "Returned my headphones, no refund yet")
    out = run(tid, classifier("refund_request"))
    assert out["outcome"] == "awaiting_approval"
    assert "awaiting approval" in out["final_response"]
    assert "No refund has been issued" in out["final_response"]
    assert ticket_status(tid) == "awaiting_approval"
    assert refund_op_count(1) == before


def test_ineligible_refund_is_answered_not_approved():
    tid = make_ticket(ASHA, 2, "Refund my speaker")
    out = run(tid, classifier("refund_request"))
    assert out["outcome"] == "answered"
    assert out["eligibility"]["reason_code"] == "RETURN_NOT_RECEIVED"


def test_order_status_is_answered():
    tid = make_ticket(ASHA, 4, "Where is my keyboard?")
    assert run(tid, classifier("order_status"))["outcome"] == "answered"


def test_proposal_that_approves_an_ineligible_refund_is_blocked():
    def dishonest(state):
        return ResolutionProposal(
            action="request_refund_approval", order_id=state["order_id"], amount_minor=129900,
            reason="x", evidence_refs=state["evidence_refs"],
            policy_refs=state["policy_refs"][:1], explanation="Approved!")
    tid = make_ticket(ASHA, 2, "Refund my speaker")
    out = run(tid, classifier("refund_request"), dishonest)
    assert out["outcome"] == "escalated"
    assert any(e.startswith("ACTION_NOT_ALLOWED") for e in out["validation_errors"])


def test_wrong_amount_is_blocked():
    def wrong_amount(state):
        p = honest_proposer(state)
        return p.model_copy(update={"amount_minor": 999999})
    tid = make_ticket(ASHA, 1, "Refund please")
    out = run(tid, classifier("refund_request"), wrong_amount)
    assert out["outcome"] == "escalated"
    assert "AMOUNT_MISMATCH" in out["validation_errors"]


def test_invented_policy_citation_is_blocked():
    def fake_cite(state):
        p = honest_proposer(state)
        return p.model_copy(update={"policy_refs": ["refund_policy_v9:section_1"]})
    tid = make_ticket(ASHA, 1, "Refund please")
    out = run(tid, classifier("refund_request"), fake_cite)
    assert out["outcome"] == "escalated"
    assert "UNKNOWN_POLICY_REF" in out["validation_errors"]


def test_unrelated_message_escalates():
    tid = make_ticket(ASHA, None, "Tell me a joke")
    assert run(tid, classifier("other"))["outcome"] == "escalated"


def test_missing_order_asks_customer_and_keeps_ticket_open():
    tid = make_ticket(ASHA, None, "I want a refund")
    out = run(tid, classifier("refund_request"))
    assert out["outcome"] == "needs_info"
    assert ticket_status(tid) == "open"


def test_other_customers_order_reveals_nothing():
    tid = make_ticket(ASHA, None, "Refund for order 5")
    out = run(tid, classifier("refund_request", order_id=5))  # order 5 is Ravi's
    assert out["outcome"] == "needs_info"
    assert "Smart Watch" not in out["final_response"]
    assert out.get("verified_facts") is None


def test_proposer_failure_escalates_after_bounded_retries():
    calls = []

    def broken(state):
        calls.append(1)
        raise ValueError("bad structured output")
    tid = make_ticket(ASHA, 1, "Refund please")
    out = run(tid, classifier("refund_request"), broken)
    assert out["outcome"] == "escalated"
    assert len(calls) == 2

def test_model_cannot_change_the_order_id():
    def wrong_order(state):
        p = honest_proposer(state)
        return p.model_copy(update={"order_id": 999})
    tid = make_ticket(ASHA, 4, "Where is my keyboard?")
    out = run(tid, classifier("order_status"), wrong_order)
    assert out["outcome"] == "answered"
    assert out["proposal"]["order_id"] == 4