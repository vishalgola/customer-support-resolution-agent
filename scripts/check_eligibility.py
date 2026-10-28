from app.db.session import SessionLocal
from app.services.eligibility import get_refund_eligibility

CASES = [  # (customer_id, order_id, what we expect)
    (1, 1, "ELIGIBLE"),
    (1, 2, "RETURN_NOT_RECEIVED"),
    (1, 3, "ALREADY_REFUNDED"),
    (1, 4, "ORDER_NOT_DELIVERED"),
    (1, 5, "ORDER_NOT_FOUND"),  # Ravi's order, requested by Asha
]

with SessionLocal() as s:
    for cust, oid, expected in CASES:
        r = get_refund_eligibility(s, cust, oid)
        flag = "OK " if r.reason_code == expected else "BAD"
        print(f"{flag} order {oid}: {r.reason_code} (amount {r.refundable_amount_minor})")