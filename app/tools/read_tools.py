from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Order, RefundOperation, Return


class ToolResult(BaseModel):
    success: bool
    data: dict | None = None
    error_code: str | None = None
    retryable: bool = False
    message: str = ""


def _iso(dt):
    return dt.isoformat() if dt else None


def _not_found() -> ToolResult:
    # Same answer for "missing" and "belongs to someone else".
    return ToolResult(success=False, error_code="ORDER_NOT_FOUND", message="Order not found.")


def _owned(db: Session, customer_id: int, order_id: int) -> Order | None:
    order = db.get(Order, order_id)
    return order if order and order.customer_id == customer_id else None


def get_order(db: Session, customer_id: int, order_id: int) -> ToolResult:
    order = _owned(db, customer_id, order_id)
    if order is None:
        return _not_found()
    return ToolResult(success=True, data={
        "order_id": order.id,
        "status": order.status,
        "currency": order.currency,
        "paid_amount_minor": order.paid_amount_minor,
        "placed_at": _iso(order.placed_at),
        "delivered_at": _iso(order.delivered_at),
        "items": [{"product_name": i.product_name, "quantity": i.quantity} for i in order.items],
    })


def get_return_status(db: Session, customer_id: int, order_id: int) -> ToolResult:
    if _owned(db, customer_id, order_id) is None:
        return _not_found()
    latest = db.scalars(
        select(Return).where(Return.order_id == order_id).order_by(Return.id.desc())
    ).first()
    data = None if latest is None else {
        "return_id": latest.id, "status": latest.status,
        "received_at": _iso(latest.received_at), "inspection_status": latest.inspection_status,
    }
    return ToolResult(success=True, data={"latest_return": data})


def get_payment_status(db: Session, customer_id: int, order_id: int) -> ToolResult:
    order = _owned(db, customer_id, order_id)
    if order is None:
        return _not_found()
    refunds = list(db.scalars(
        select(RefundOperation).where(RefundOperation.order_id == order_id)
    ))
    return ToolResult(success=True, data={
        "paid_amount_minor": order.paid_amount_minor,
        "currency": order.currency,
        "refunded_amount_minor": sum(r.amount_minor for r in refunds if r.status == "succeeded"),
        "refunds": [{"id": r.id, "amount_minor": r.amount_minor, "status": r.status} for r in refunds],
    })