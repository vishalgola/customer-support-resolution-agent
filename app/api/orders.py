from dataclasses import asdict

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.auth.deps import Principal, require_customer
from app.db.models import Order
from app.db.session import get_db
from app.schemas.orders import OrderOut
from app.services.eligibility import get_refund_eligibility

router = APIRouter(tags=["orders"])


def _own_order(db: Session, order_id: int, p: Principal) -> Order:
    order = db.get(Order, order_id)
    if order is None or order.customer_id != p.customer_id:
        raise HTTPException(404, "Order not found")  # same answer for missing and not yours
    return order


@router.get("/orders/{order_id}", response_model=OrderOut)
def get_order(order_id: int, p: Principal = Depends(require_customer),
              db: Session = Depends(get_db)):
    return OrderOut.model_validate(_own_order(db, order_id, p))


@router.get("/orders/{order_id}/refund-eligibility")
def refund_eligibility(order_id: int, p: Principal = Depends(require_customer),
                       db: Session = Depends(get_db)):
    _own_order(db, order_id, p)
    return asdict(get_refund_eligibility(db, p.customer_id, order_id))