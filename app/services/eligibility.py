from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Order, RefundOperation, Return

RETURN_WINDOW_DAYS = 30  # synthetic policy: return must be received within 30 days of delivery

# refund operation statuses
IN_PROGRESS = {"reserved", "submitted", "processing"}
SUCCEEDED = {"succeeded"}
UNCERTAIN = {"unknown"}  # needs reconciliation, not a decision
# "failed" refunds don't count toward the committed amount


class Decision(str, Enum):
    ELIGIBLE = "eligible"
    INELIGIBLE = "ineligible"
    NEEDS_REVIEW = "needs_review"


@dataclass(frozen=True)
class OrderFacts:
    id: int
    customer_id: int
    status: str
    paid_amount_minor: int
    delivered_at: datetime | None


@dataclass(frozen=True)
class ReturnFacts:
    id: int
    status: str
    received_at: datetime | None
    inspection_status: str


@dataclass(frozen=True)
class RefundFacts:
    amount_minor: int
    status: str


@dataclass(frozen=True)
class EligibilityResult:
    decision: Decision
    reason_code: str
    message: str
    refundable_amount_minor: int = 0


def _no(code: str, message: str) -> EligibilityResult:
    return EligibilityResult(Decision.INELIGIBLE, code, message)


def _review(code: str, message: str) -> EligibilityResult:
    return EligibilityResult(Decision.NEEDS_REVIEW, code, message)


def check_refund_eligibility(
    order: OrderFacts | None,
    returns: list[ReturnFacts],
    refunds: list[RefundFacts],
    requesting_customer_id: int,
) -> EligibilityResult:
    # 1. Ownership: same answer for "missing" and "not yours" so nothing leaks
    if order is None or order.customer_id != requesting_customer_id:
        return _no("ORDER_NOT_FOUND", "Order not found.")

    # 2. Order must be delivered
    if order.status != "delivered":
        return _no("ORDER_NOT_DELIVERED", "The order has not been delivered.")

    # 3. Return checks (use the most recent return)
    if not returns:
        return _no("NO_RETURN", "No return exists for this order.")
    ret = max(returns, key=lambda r: r.id)

    if ret.status != "received":
        return _no("RETURN_NOT_RECEIVED", "The return has not been marked as received.")
    if ret.inspection_status == "pending":
        return _no("INSPECTION_PENDING", "The returned item has not been inspected yet.")
    if ret.inspection_status != "passed":
        return _no("INSPECTION_FAILED", "The returned item did not pass inspection.")

    # 4. Return window
    if order.delivered_at is None or ret.received_at is None:
        return _review("MISSING_DATES", "Delivery or return dates are missing.")
    if ret.received_at - order.delivered_at > timedelta(days=RETURN_WINDOW_DAYS):
        return _no("RETURN_WINDOW_EXPIRED", "The return was received after the return window.")

    # 5. Existing refunds
    if any(r.status in UNCERTAIN for r in refunds):
        return _review("REFUND_OUTCOME_UNKNOWN", "A previous refund has an unconfirmed outcome.")
    if any(r.status in IN_PROGRESS for r in refunds):
        return _no("REFUND_IN_PROGRESS", "A refund for this order is already being processed.")

    committed = sum(r.amount_minor for r in refunds if r.status in SUCCEEDED)
    remaining = order.paid_amount_minor - committed

    if remaining < 0:
        return _review("DATA_INCONSISTENT", "Refunded amount exceeds the paid amount.")
    if remaining == 0:
        return _no("ALREADY_REFUNDED", "This order has already been fully refunded.")

    return EligibilityResult(
        Decision.ELIGIBLE,
        "ELIGIBLE",
        "Return received and inspection passed.",
        refundable_amount_minor=remaining,
    )


def get_refund_eligibility(
    session: Session, customer_id: int, order_id: int
) -> EligibilityResult:
    """Load facts from the database, then apply the pure rules."""
    o = session.get(Order, order_id)
    order = (
        OrderFacts(o.id, o.customer_id, o.status, o.paid_amount_minor, o.delivered_at)
        if o else None
    )

    returns, refunds = [], []
    if o:
        returns = [
            ReturnFacts(r.id, r.status, r.received_at, r.inspection_status)
            for r in session.scalars(select(Return).where(Return.order_id == order_id))
        ]
        refunds = [
            RefundFacts(r.amount_minor, r.status)
            for r in session.scalars(
                select(RefundOperation).where(RefundOperation.order_id == order_id)
            )
        ]

    return check_refund_eligibility(order, returns, refunds, customer_id)
    