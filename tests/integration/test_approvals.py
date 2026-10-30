import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select, text

from app.db.models import RefundOperation, Ticket
from app.db.session import SessionLocal
from app.main import app
from app.policies.ingest import ingest_all
from app.schemas.workflow import Classification, ResolutionProposal
from app.workflows.runtime import start_runtime, stop_runtime
from scripts.seed_data import seed

client = TestClient(app)  # no "with": lifespan is skipped; the fixture starts the runtime


class FakeProvider:
    def __init__(self):
        self.calls = 0
        self.records = {}
        self.mode = "ok"

    def __call__(self, order_id, amount_minor, key):
        self.calls += 1
        if key in self.records:  # the real client reconciles by key first
            return "succeeded", self.records[key]
        self.records[key] = f"ref-{len(self.records) + 1}"
        if self.mode == "timeout_after_accept":
            return "unknown", None
        return "succeeded", self.records[key]


def honest_proposer(state):
    elig = state.get("eligibility") or {}
    base = dict(order_id=state["order_id"], reason="test",
                evidence_refs=state["evidence_refs"], policy_refs=state["policy_refs"][:1])
    if elig.get("decision") == "eligible":
        return ResolutionProposal(action="request_refund_approval",
                                  amount_minor=elig["refundable_amount_minor"],
                                  explanation="Your return qualifies.", **base)
    return ResolutionProposal(action="answer_only", explanation="Status.", **base)


@pytest.fixture(scope="module", autouse=True)
def runtime():
    with SessionLocal() as db:
        ingest_all(db)
    start_runtime(lambda message: Classification(category="refund_request"), honest_proposer)
    yield
    stop_runtime()


@pytest.fixture(autouse=True)
def provider(monkeypatch):
    seed()
    fake = FakeProvider()
    monkeypatch.setattr("app.services.refunds._call_provider", fake)
    return fake


def login(subject):
    r = client.post("/auth/dev-login", json={"subject": subject})
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def start_run():
    h = login("auth|asha")
    t = client.post("/tickets", json={"order_id": 1, "summary": "Returned my headphones, no refund"},
                    headers=h).json()
    r = client.post(f"/tickets/{t['id']}/runs", headers=h)
    assert r.status_code == 201, r.text
    return t["id"], r.json()


def pending_approval_id():
    items = client.get("/approvals", headers=login("auth|reviewer1")).json()
    assert len(items) == 1
    return items[0]["approval_id"]


def decide(approval_id, decision, who="auth|reviewer1"):
    return client.post(f"/approvals/{approval_id}/decision",
                       json={"decision": decision}, headers=login(who))


def ticket_status(tid):
    with SessionLocal() as db:
        return db.get(Ticket, tid).status


def op_count(order_id: int = 1) -> int:
    with SessionLocal() as db:
        return db.scalar(select(func.count()).select_from(RefundOperation)
                         .where(RefundOperation.order_id == order_id))


def test_run_pauses_for_approval_and_moves_no_money(provider):
    tid, run = start_run()
    assert run["status"] == "awaiting_approval"
    assert "No refund has been issued" in run["final_response"]
    pending = client.get("/approvals", headers=login("auth|reviewer1")).json()
    assert pending[0]["proposal"]["amount_minor"] == 249900
    assert provider.calls == 0
    assert ticket_status(tid) == "awaiting_approval"


def test_second_run_on_same_ticket_is_blocked():
    tid, _ = start_run()
    r = client.post(f"/tickets/{tid}/runs", headers=login("auth|asha"))
    assert r.status_code == 409


def test_approval_executes_exactly_once(provider):
    tid, run = start_run()
    aid = pending_approval_id()
    r = decide(aid, "approved")
    assert r.status_code == 200, r.text
    assert r.json()["run"]["status"] == "refund_succeeded"
    assert provider.calls == 1 and op_count() == 1
    assert ticket_status(tid) == "resolved"
    assert decide(aid, "approved").status_code == 409  # cannot decide twice
    assert provider.calls == 1


def test_rejection_moves_no_money(provider):
    tid, _ = start_run()
    r = decide(pending_approval_id(), "rejected")
    assert r.json()["run"]["status"] == "rejected"
    assert provider.calls == 0 and op_count() == 0
    assert ticket_status(tid) == "escalated"


def test_customer_cannot_decide():
    start_run()
    assert decide(pending_approval_id(), "approved", who="auth|asha").status_code == 403


def test_expired_approval_cannot_be_used(provider):
    _, run = start_run()
    aid = pending_approval_id()
    with SessionLocal() as db:
        db.execute(text("UPDATE approvals SET expires_at = now() - interval '1 hour'"))
        db.commit()
    assert decide(aid, "approved").status_code == 409
    got = client.get(f"/runs/{run['run_id']}", headers=login("auth|asha")).json()
    assert got["status"] == "expired"
    assert provider.calls == 0


def test_changed_conditions_invalidate_the_approval(provider):
    _, run = start_run()
    aid = pending_approval_id()
    with SessionLocal() as db:  # someone else refunded the order after the proposal was made
        db.add(RefundOperation(order_id=1, amount_minor=249900, idempotency_key="external",
                               provider_reference="x", status="succeeded"))
        db.commit()
    r = decide(aid, "approved")
    assert r.json()["run"]["status"] == "approval_invalid"
    assert provider.calls == 0


def test_tampered_proposal_is_rejected(provider):
    start_run()
    aid = pending_approval_id()
    with SessionLocal() as db:
        db.execute(text("UPDATE action_proposals SET payload = "
                        "jsonb_set(payload, '{amount_minor}', '1')"))
        db.commit()
    assert decide(aid, "approved").status_code == 409
    assert provider.calls == 0


def test_unknown_outcome_is_reconciled_without_a_duplicate(provider):
    tid, run = start_run()
    provider.mode = "timeout_after_accept"
    r = decide(pending_approval_id(), "approved")
    assert r.json()["run"]["status"] == "refund_unconfirmed"
    assert "couldn't confirm" in r.json()["run"]["final_response"]
    assert ticket_status(tid) == "refund_pending"

    provider.mode = "ok"
    fixed = client.post(f"/runs/{run['run_id']}/reconcile", headers=login("auth|reviewer1"))
    assert fixed.json()["status"] == "refund_succeeded"
    assert len(provider.records) == 1 and op_count() == 1  # one refund at the provider, one in our ledger
    assert ticket_status(tid) == "resolved"

def test_reviewer_sees_evidence_and_policy_text():
    start_run()
    aid = pending_approval_id()
    d = client.get(f"/approvals/{aid}", headers=login("auth|reviewer1")).json()
    assert d["evidence"]["order"]["paid_amount_minor"] == 249900
    assert d["evidence"]["return"]["latest_return"]["inspection_status"] == "passed"
    assert d["policies"] and all(p["text"] for p in d["policies"])
    assert client.get(f"/approvals/{aid}", headers=login("auth|asha")).status_code == 403


def test_customer_lists_only_own_orders():
    orders = client.get("/orders", headers=login("auth|asha")).json()
    assert {o["id"] for o in orders} == {1, 2, 3, 4}  # order 5 belongs to Ravi