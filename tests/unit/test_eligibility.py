from datetime import datetime, timedelta, timezone

from app.services.eligibility import (
    Decision, OrderFacts, RefundFacts, ReturnFacts, check_refund_eligibility,
)

NOW = datetime(2026, 10, 1, tzinfo=timezone.utc)
CUSTOMER = 1


def order(**kw):
    base = dict(id=1, customer_id=CUSTOMER, status="delivered",
                paid_amount_minor=249900, delivered_at=NOW - timedelta(days=10))
    return OrderFacts(**{**base, **kw})


def ret(**kw):
    base = dict(id=1, status="received", received_at=NOW - timedelta(days=3),
                inspection_status="passed")
    return ReturnFacts(**{**base, **kw})


def check(o=None, returns=None, refunds=None, customer=CUSTOMER):
    return check_refund_eligibility(
        o if o is not None else order(),
        returns if returns is not None else [ret()],
        refunds or [],
        customer,
    )


def test_eligible_full_amount():
    r = check()
    assert r.decision == Decision.ELIGIBLE
    assert r.refundable_amount_minor == 249900


def test_missing_order():
    r = check_refund_eligibility(None, [], [], CUSTOMER)
    assert r.reason_code == "ORDER_NOT_FOUND"


def test_other_customers_order_looks_like_not_found():
    r = check(customer=2)
    assert r.reason_code == "ORDER_NOT_FOUND"
    assert r.refundable_amount_minor == 0


def test_not_delivered():
    r = check(o=order(status="shipped"))
    assert r.reason_code == "ORDER_NOT_DELIVERED"


def test_no_return():
    r = check(returns=[])
    assert r.reason_code == "NO_RETURN"


def test_return_not_received():
    r = check(returns=[ret(status="in_transit", received_at=None, inspection_status="pending")])
    assert r.reason_code == "RETURN_NOT_RECEIVED"


def test_inspection_pending():
    r = check(returns=[ret(inspection_status="pending")])
    assert r.reason_code == "INSPECTION_PENDING"


def test_inspection_failed():
    r = check(returns=[ret(inspection_status="failed")])
    assert r.reason_code == "INSPECTION_FAILED"


def test_return_window_expired():
    r = check(returns=[ret(received_at=NOW)], o=order(delivered_at=NOW - timedelta(days=45)))
    assert r.reason_code == "RETURN_WINDOW_EXPIRED"


def test_missing_dates_needs_review():
    r = check(o=order(delivered_at=None))
    assert r.decision == Decision.NEEDS_REVIEW
    assert r.reason_code == "MISSING_DATES"


def test_already_fully_refunded():
    r = check(refunds=[RefundFacts(249900, "succeeded")])
    assert r.reason_code == "ALREADY_REFUNDED"


def test_partial_refund_leaves_remaining_balance():
    r = check(refunds=[RefundFacts(50000, "succeeded")])
    assert r.decision == Decision.ELIGIBLE
    assert r.refundable_amount_minor == 199900


def test_refund_in_progress_blocks_new_one():
    r = check(refunds=[RefundFacts(249900, "processing")])
    assert r.reason_code == "REFUND_IN_PROGRESS"


def test_unknown_refund_outcome_needs_review():
    r = check(refunds=[RefundFacts(249900, "unknown")])
    assert r.decision == Decision.NEEDS_REVIEW
    assert r.reason_code == "REFUND_OUTCOME_UNKNOWN"


def test_failed_refund_does_not_reduce_balance():
    r = check(refunds=[RefundFacts(249900, "failed")])
    assert r.decision == Decision.ELIGIBLE
    assert r.refundable_amount_minor == 249900


def test_over_refunded_is_flagged():
    r = check(refunds=[RefundFacts(300000, "succeeded")])
    assert r.reason_code == "DATA_INCONSISTENT"