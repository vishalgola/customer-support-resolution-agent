from datetime import datetime, timedelta, timezone

from sqlalchemy import text

from app.db.models import Customer, Order, OrderItem, RefundOperation, Return
from app.db.session import SessionLocal

DEFAULT_ORDER = dict(status="delivered", delivered_days_ago=10, paid=249900,
                     product="Wireless Headphones", ret=None, refunds=[])


def O(**overrides) -> dict:
    return {**DEFAULT_ORDER, **overrides}


def _make_order(s, customer, spec, now):
    days = spec["delivered_days_ago"]
    order = Order(
        customer_id=customer.id, status=spec["status"], currency="INR",
        paid_amount_minor=spec["paid"],
        placed_at=now - timedelta(days=(days or 2) + 3),
        delivered_at=(now - timedelta(days=days))
        if (spec["status"] == "delivered" and days is not None) else None,
    )
    order.items.append(OrderItem(product_name=spec["product"], quantity=1,
                                 paid_amount_minor=spec["paid"]))
    s.add(order)
    s.flush()

    ret = spec["ret"]
    if ret:
        got = ret["received_days_ago"]
        s.add(Return(order_id=order.id, status=ret["status"],
                     received_at=(now - timedelta(days=got)) if got is not None else None,
                     inspection_status=ret["inspection"]))
    for i, rf in enumerate(spec["refunds"]):
        s.add(RefundOperation(
            order_id=order.id, amount_minor=rf["amount"], status=rf["status"],
            idempotency_key=f"eval-seed-{order.id}-{i}",
            provider_reference=f"seed_ref_{i}" if rf["status"] == "succeeded" else None))
    return order


def reset_world(own_order_spec: dict) -> None:
    """Customer 1 (Asha) owns order 1. Customer 2 (Ravi) owns order 2."""
    now = datetime.now(timezone.utc)
    with SessionLocal() as s:
        s.execute(text(
            "TRUNCATE audit_events, tickets, refund_operations, returns, "
            "order_items, orders, customers RESTART IDENTITY CASCADE"))
        asha = Customer(external_auth_subject="auth|asha", name="Asha Rao", email="asha@example.com")
        ravi = Customer(external_auth_subject="auth|ravi", name="Ravi Mehta", email="ravi@example.com")
        s.add_all([asha, ravi])
        s.flush()
        _make_order(s, asha, own_order_spec, now)
        _make_order(s, ravi, O(paid=987650, product="Ravi Titanium Drone",
                        delivered_days_ago=5), now)
        s.commit()
        