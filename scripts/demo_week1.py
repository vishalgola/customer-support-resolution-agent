import httpx

API = "http://localhost:8000"
PROVIDER = "http://localhost:8001"


def login(subject: str) -> dict:
    token = httpx.post(f"{API}/auth/dev-login", json={"subject": subject}).json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


httpx.post(f"{PROVIDER}/admin/reset")  # provider is in-memory; start clean
asha, reviewer = login("auth|asha"), login("auth|reviewer1")

print("1. eligibility:", httpx.get(f"{API}/orders/1/refund-eligibility", headers=asha).json())

ticket = httpx.post(f"{API}/tickets", headers=asha,
                    json={"order_id": 1, "summary": "Returned headphones, no refund yet"}).json()
print("2. ticket:", ticket["id"], ticket["status"])

print("3. other customer's order ->", httpx.get(f"{API}/orders/5", headers=asha).status_code)
print("4. customer tries refund ->", httpx.post(f"{API}/tickets/{ticket['id']}/refund", headers=asha).status_code)

r1 = httpx.post(f"{API}/tickets/{ticket['id']}/refund", headers=reviewer, timeout=30)
print("5. reviewer refund ->", r1.status_code, r1.json())

r2 = httpx.post(f"{API}/tickets/{ticket['id']}/refund", headers=reviewer, timeout=30)
print("6. repeat call ->", r2.status_code, r2.json(), "| same refund:", r1.json() == r2.json())

print("7. ticket now:", httpx.get(f"{API}/tickets/{ticket['id']}", headers=asha).json()["status"])
print("8. eligibility now:", httpx.get(f"{API}/orders/1/refund-eligibility", headers=asha).json()["reason_code"])