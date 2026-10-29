from datetime import date

import pytest

from app.db.session import SessionLocal
from app.policies.ingest import ingest_all
from app.policies.retrieval import search_policy


@pytest.fixture(scope="module")
def db():
    with SessionLocal() as s:
        ingest_all(s)
        yield s


def refs(hits):
    return [h.ref for h in hits]


def test_refund_question_finds_refund_policy(db):
    hits = search_policy(db, "I returned my headphones, when do I get my refund?", top_k=3)
    assert any(r.startswith("refund_policy_v1") for r in refs(hits))


def test_damage_question_finds_damage_policy(db):
    hits = search_policy(db, "My item arrived broken and cracked", top_k=3)
    assert any(r.startswith("damaged_products_v1") for r in refs(hits))


def test_delay_question_finds_shipping_policy(db):
    hits = search_policy(db, "My package is late and has not arrived", top_k=3)
    assert any(r.startswith("shipping_delays_v1") for r in refs(hits))


def test_expired_policy_never_returned_today(db):
    hits = search_policy(db, "return window days", top_k=10)
    assert not any(r.startswith("refund_policy_v0") for r in refs(hits))


def test_historical_date_returns_old_version(db):
    hits = search_policy(db, "return window days", as_of=date(2025, 6, 1), top_k=10)
    assert any(r.startswith("refund_policy_v0") for r in refs(hits))
    assert not any(r.startswith("refund_policy_v1") for r in refs(hits))


def test_date_before_any_policy_returns_nothing(db):
    assert search_policy(db, "refund", as_of=date(2020, 1, 1)) == []


def test_doc_key_filter(db):
    hits = search_policy(db, "what happens next", doc_key="exceptions", top_k=5)
    assert hits and all(h.ref.startswith("exceptions_v1") for h in hits)