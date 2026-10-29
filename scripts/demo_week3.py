import httpx

API, PROVIDER = "http://localhost:8000", "http://localhost:8001"


def login(subject):
    token = httpx.post(f"{API}/auth/dev-login", json={"subject": subject}).json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


httpx.post(f"{PROVIDER}/admin/reset")
asha, reviewer = login("auth|asha"), login("auth|reviewer1")

ticket = httpx.post(f"{API}/tickets", headers=asha, json={
    "order_id": 1, "summary": "I returned my headphones but still haven't got my refund."}).json()
run = httpx.post(f"{API}/tickets/{ticket['id']}/runs", headers=asha, timeout=180).json()
print("1. customer sees:", run["status"], "|", run["final_response"])

pending = httpx.get(f"{API}/approvals", headers=reviewer).json()
print("2. reviewer queue:", pending)

input("3. Optional: stop and restart the API server now, then press Enter...")

done = httpx.post(f"{API}/approvals/{pending[0]['approval_id']}/decision",
                  headers=reviewer, json={"decision": "approved"}, timeout=60).json()
print("4. after approval:", done["run"]["status"], "|", done["run"]["final_response"])

for e in httpx.get(f"{API}/runs/{run['run_id']}/events", headers=asha).json():
    print("  -", e["actor"], e["event_type"])