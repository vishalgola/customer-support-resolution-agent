import httpx
from sqlalchemy import select

from app.config import settings
from app.db.models import ActionProposal, Approval, Order, RefundOperation, WorkflowRun
from app.db.session import SessionLocal
from app.services.audit import add_audit
from app.services.eligibility import Decision, get_refund_eligibility
from app.services.payload import hash_payload


class ApprovalInvalid(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def _summary(op: RefundOperation) -> dict:
    return {
        "operation_id": op.id,
        "status": op.status,
        "amount_minor": op.amount_minor,
        "provider_reference": op.provider_reference,
    }


def describe_outcome(result: dict) -> tuple[str, str, str]:
    """(run outcome, ticket status, customer message). Wording matches what was confirmed."""
    amount = f"₹{result['amount_minor'] / 100:,.2f}"
    if result["status"] == "succeeded":
        return ("refund_succeeded", "resolved",
                f"The payment service confirmed your refund of {amount}. "
                "It can take 5 to 7 business days to appear in your account.")
    if result["status"] == "failed":
        return ("refund_failed", "escalated",
                "The payment service rejected the refund request. "
                "Your case has been passed to our support team.")
    return ("refund_unconfirmed", "refund_pending",
            "I couldn't confirm the refund outcome yet. The case is being checked.")


def _call_provider(order_id: int, amount_minor: int, key: str) -> tuple[str, str | None]:
    """Returns (status, provider_reference). Reconciles by key before posting."""
    body = {"order_id": order_id, "amount_minor": amount_minor, "currency": "INR"}
    try:
        with httpx.Client(
            base_url=settings.payment_provider_url,
            timeout=settings.payment_timeout_seconds,
        ) as client:
            found = client.get(f"/refunds/by-key/{key}")
            if found.status_code == 200:
                return "succeeded", found.json()["provider_reference"]
            resp = client.post("/refunds", json=body, headers={"Idempotency-Key": key})
    except httpx.HTTPError:
        return "unknown", None  # timeout or connection problem: outcome not confirmed

    if resp.status_code == 200:
        return "succeeded", resp.json()["provider_reference"]
    if 400 <= resp.status_code < 500:
        return "failed", None
    return "unknown", None


def execute_approved_refund(proposal_id: int) -> dict:
    """Safe to call repeatedly: the same proposal always maps to the same idempotency key."""
    invalid: ApprovalInvalid | None = None
    done: dict | None = None
    call: tuple | None = None

    # Phase 1: verify the stored approval, re-check the rules, reserve. Committed before any network call.
    with SessionLocal() as s, s.begin():
        proposal = s.execute(
            select(ActionProposal).where(ActionProposal.id == proposal_id).with_for_update()
        ).scalar_one()
        approval = s.scalar(select(Approval).where(Approval.proposal_id == proposal_id))
        ticket_id = s.get(WorkflowRun, proposal.run_id).ticket_id
        key = f"proposal-{proposal_id}-refund"
        actor = f"reviewer:{approval.reviewer_id}"

        if approval.decision != "approved":
            raise ApprovalInvalid("NOT_APPROVED", "No approved decision is stored for this proposal.")
        if proposal.payload_hash != hash_payload(proposal.payload):
            raise ApprovalInvalid("PAYLOAD_CHANGED", "The approved payload no longer matches its hash.")

        payload = proposal.payload
        order = s.execute(  # row lock: concurrent attempts on this order wait here
            select(Order).where(Order.id == payload["order_id"]).with_for_update()
        ).scalar_one()
        op = s.scalar(select(RefundOperation).where(RefundOperation.idempotency_key == key))

        if op is None:
            result = get_refund_eligibility(s, order.customer_id, order.id)
            if (result.decision != Decision.ELIGIBLE
                    or result.refundable_amount_minor != payload["amount_minor"]):
                proposal.status = "invalidated"
                add_audit(s, ticket_id, actor, "approval_invalidated",
                          {"reason_code": result.reason_code})
                invalid = ApprovalInvalid(
                    "CONDITIONS_CHANGED", "Eligibility or amount changed after approval.")
            else:
                op = RefundOperation(
                    order_id=order.id, proposal_id=proposal.id,
                    amount_minor=payload["amount_minor"],
                    idempotency_key=key, status="reserved",
                )
                s.add(op)
                s.flush()
                add_audit(s, ticket_id, actor, "refund_reserved",
                          {"amount_minor": op.amount_minor})

        if invalid is None:
            if op.status == "succeeded":
                done = _summary(op)  # already completed: never send it again
            elif op.status == "failed":
                raise ApprovalInvalid("PREVIOUS_ATTEMPT_FAILED",
                                      "A previous attempt was rejected by the provider.")
            else:  # reserved / submitted / processing / unknown: reconcile by key, then send if needed
                call = (op.id, order.id, op.amount_minor)

    if invalid:
        raise invalid
    if done:
        return done

    # Phase 2: external call, outside any DB transaction.
    op_id, order_id, amount = call
    status, provider_ref = _call_provider(order_id, amount, key)

    # Phase 3: record what we actually learned.
    with SessionLocal() as s, s.begin():
        op = s.get(RefundOperation, op_id)
        op.status = status
        op.provider_reference = provider_ref
        if status == "succeeded":
            s.get(ActionProposal, proposal_id).status = "executed"
        add_audit(s, ticket_id, actor, f"refund_{status}", {"provider_reference": provider_ref})
        return _summary(op)