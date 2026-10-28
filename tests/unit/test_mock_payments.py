import pytest
from fastapi.testclient import TestClient

from mock_services.payments.main import app

client = TestClient(app)


@pytest.fixture(autouse=True)
def reset_provider():
    client.post("/admin/reset")


def body(amount=249900):
    return {"order_id": 1, "amount_minor": amount, "currency": "INR"}


def test_same_key_same_payload_returns_same_refund():
    h = {"Idempotency-Key": "k1"}
    r1 = client.post("/refunds", json=body(), headers=h)
    r2 = client.post("/refunds", json=body(), headers=h)
    assert r1.status_code == 200 and r2.status_code == 200
    assert r1.json()["provider_reference"] == r2.json()["provider_reference"]


def test_same_key_different_payload_is_rejected():
    h = {"Idempotency-Key": "k1"}
    client.post("/refunds", json=body(1000), headers=h)
    r = client.post("/refunds", json=body(2000), headers=h)
    assert r.status_code == 422


def test_different_keys_create_different_refunds():
    r1 = client.post("/refunds", json=body(), headers={"Idempotency-Key": "a"})
    r2 = client.post("/refunds", json=body(), headers={"Idempotency-Key": "b"})
    assert r1.json()["provider_reference"] != r2.json()["provider_reference"]


def test_500_error_creates_nothing():
    client.post("/admin/mode", json={"mode": "error_500"})
    r = client.post("/refunds", json=body(), headers={"Idempotency-Key": "k1"})
    assert r.status_code == 500
    client.post("/admin/mode", json={"mode": "none"})
    assert client.get("/refunds/by-key/k1").status_code == 404


def test_timeout_after_accept_still_records_refund():
    client.post("/admin/mode", json={"mode": "timeout_after_accept", "delay_seconds": 0})
    client.post("/refunds", json=body(), headers={"Idempotency-Key": "k1"})
    found = client.get("/refunds/by-key/k1")
    assert found.status_code == 200
    assert found.json()["status"] == "succeeded"


def test_invalid_amount_rejected():
    r = client.post("/refunds", json=body(0), headers={"Idempotency-Key": "k1"})
    assert r.status_code == 422