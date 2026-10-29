from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def auth(subject: str) -> dict:
    r = client.post("/auth/dev-login", json={"subject": subject})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def test_no_token_rejected():
    assert client.get("/orders/1").status_code in (401, 403)


def test_unknown_user_cannot_login():
    assert client.post("/auth/dev-login", json={"subject": "auth|nobody"}).status_code == 401


def test_customer_reads_own_order():
    r = client.get("/orders/1", headers=auth("auth|asha"))
    assert r.status_code == 200
    assert r.json()["items"][0]["product_name"] == "Wireless Headphones"


def test_customer_cannot_read_other_customers_order():
    assert client.get("/orders/5", headers=auth("auth|asha")).status_code == 404


def test_eligibility_endpoint_blocks_unreceived_return():
    r = client.get("/orders/2/refund-eligibility", headers=auth("auth|asha"))
    assert r.json()["reason_code"] == "RETURN_NOT_RECEIVED"


def test_customer_cannot_create_ticket_for_other_customers_order():
    r = client.post("/tickets", json={"order_id": 5, "summary": "x"}, headers=auth("auth|asha"))
    assert r.status_code == 404


def test_customer_can_create_ticket_for_own_order():
    r = client.post("/tickets", json={"order_id": 2, "summary": "Where is my refund?"},
                    headers=auth("auth|asha"))
    assert r.status_code == 201
    assert r.json()["status"] == "open"

