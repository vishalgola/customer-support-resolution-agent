import httpx
from sqlalchemy import select

from app.config import settings
from app.db.models import AuditEvent, Order, RefundOperation, Ticket
from app.db.session import SessionLocal
from app.services.eligibility import Decision, EligibilityResult, get_refund_eligibility


class TicketNotFound(Exception):
    pass


class RefundBlocked(Exception):
    def __init__(self, result: EligibilityResult):
        self.result = result


def _blocked(code: str, message: str) -> RefundBlocked:
    return RefundBlocked(EligibilityResult(Decision.INELIGIBLE, code, message))


def _audit(s, ticket_id: int, actor: str, event_type: str, payload: dict):
    s.add(AuditEvent(ticket_id=ticket_id, actor=actor, event_type=event_type,
                     redacted_payload=payload))


def _summary(op: RefundOperation) -> dict:
    return {
        "operation_id": op.id,
        "status": op.status,
        "amount_minor": op.amount_minor,
        "provider_reference": op.provider_reference,
    }


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


def execute_refund(ticket_id: int, reviewer_subject: str) -> dict:
    key = f"ticket-{ticket_id}-refund"  # stable, so retries reuse it
    actor = f"reviewer:{reviewer_subject}"

    # Phase 1: validate and reserve. Short transaction, committed before any network call.
    with SessionLocal() as s, s.begin():
        ticket = s.get(Ticket, ticket_id)
        if ticket is None:
            raise TicketNotFound()
        if ticket.order_id is None:
            raise _blocked("NO_ORDER_ON_TICKET", "This ticket has no order attached.")

        # Row lock: concurrent refund attempts on the same order wait here.
        order = s.execute(
            select(Order).where(Order.id == ticket.order_id).with_for_update()
        ).scalar_one()

        op = s.scalar(select(RefundOperation).where(RefundOperation.idempotency_key == key))
        if op is None:
            result = get_refund_eligibility(s, order.customer_id, order.id)
            if result.decision != Decision.ELIGIBLE:
                raise RefundBlocked(result)
            op = RefundOperation(
                order_id=order.id,
                amount_minor=result.refundable_amount_minor,  # amount comes from code
                idempotency_key=key,
                status="reserved",
            )
            s.add(op)
            s.flush()
            _audit(s, ticket_id, actor, "refund_reserved", {"amount_minor": op.amount_minor})
        elif op.status == "succeeded":
            return _summary(op)  # already done: idempotent
        elif op.status == "failed":
            raise _blocked("PREVIOUS_ATTEMPT_FAILED", "A previous attempt failed; needs review.")

        op_id, order_id, amount = op.id, order.id, op.amount_minor

    # Phase 2: external call, outside any DB transaction.
    status, provider_ref = _call_provider(order_id, amount, key)

    # Phase 3: record what we actually learned.
    with SessionLocal() as s, s.begin():
        op = s.get(RefundOperation, op_id)
        op.status = status
        op.provider_reference = provider_ref
        if status == "succeeded":
            s.get(Ticket, ticket_id).status = "resolved"
        _audit(s, ticket_id, actor, f"refund_{status}",
               {"provider_reference": provider_ref})
        return _summary(op)