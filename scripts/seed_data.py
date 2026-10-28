from datetime import datetime, timedelta, timezone

from sqlalchemy import text

from app.db.models import Customer, Order, OrderItem, Return, RefundOperation
from app.db.session import SessionLocal

now = datetime.now(timezone.utc)


def seed():
    with SessionLocal() as s:
        # wipe in FK-safe order so the script can be re-run
        s.execute(text(
            "TRUNCATE audit_events, tickets, refund_operations, returns, "
            "order_items, orders, customers RESTART IDENTITY CASCADE"
        ))

        asha = Customer(external_auth_subject="auth|asha", name="Asha Rao", email="asha@example.com")
        ravi = Customer(external_auth_subject="auth|ravi", name="Ravi Mehta", email="ravi@example.com")
        s.add_all([asha, ravi])
        s.flush()

        def order(customer, status, amount, name, delivered_days_ago=None):
            o = Order(
                customer_id=customer.id, status=status, currency="INR",
                paid_amount_minor=amount,
                placed_at=now - timedelta(days=(delivered_days_ago or 2) + 3),
                delivered_at=(now - timedelta(days=delivered_days_ago)) if delivered_days_ago else None,
            )
            o.items.append(OrderItem(product_name=name, quantity=1, paid_amount_minor=amount))
            s.add(o)
            s.flush()
            return o

        # 1: eligible refund (return received, inspection passed)
        o1 = order(asha, "delivered", 249900, "Wireless Headphones", 10)
        s.add(Return(order_id=o1.id, status="received", received_at=now - timedelta(days=3), inspection_status="passed"))

        # 2: ineligible (return still in transit)
        o2 = order(asha, "delivered", 129900, "Bluetooth Speaker", 8)
        s.add(Return(order_id=o2.id, status="in_transit", inspection_status="pending"))

        # 3: already refunded (succeeded refund exists)
        o3 = order(asha, "delivered", 99900, "USB-C Hub", 12)
        s.add(Return(order_id=o3.id, status="received", received_at=now - timedelta(days=6), inspection_status="passed"))
        s.add(RefundOperation(order_id=o3.id, amount_minor=99900, idempotency_key="seed-refund-o3",
                              provider_reference="mock_ref_001", status="succeeded"))

        # 4: shipped, not delivered (order-status query)
        order(asha, "shipped", 349900, "Mechanical Keyboard")

        # 5: belongs to Ravi (ownership / authorization test)
        order(ravi, "delivered", 199900, "Smart Watch", 5)

        s.commit()
        print("Seeded 2 customers and 5 orders.")


if __name__ == "__main__":
    seed()