import hashlib
import time
import uuid
from threading import Lock

from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel, Field

app = FastAPI(title="Mock Payment Provider")


class RefundRequest(BaseModel):
    order_id: int
    amount_minor: int = Field(gt=0)
    currency: str = "INR"


class ModeRequest(BaseModel):
    mode: str = "none"  # none | error_500 | timeout_after_accept
    delay_seconds: float = 0.0


_lock = Lock()
_refunds: dict[str, dict] = {}                 # provider_reference -> refund record
_by_key: dict[str, tuple[str, str]] = {}       # idempotency key -> (payload_hash, reference)
_state = {"mode": "none", "delay_seconds": 0.0}


def _payload_hash(req: RefundRequest) -> str:
    return hashlib.sha256(req.model_dump_json().encode()).hexdigest()


@app.post("/refunds")
def create_refund(req: RefundRequest, idempotency_key: str = Header(...)):
    payload_hash = _payload_hash(req)

    with _lock:
        existing = _by_key.get(idempotency_key)
        if existing:
            saved_hash, ref = existing
            if saved_hash != payload_hash:
                raise HTTPException(422, "Idempotency key reused with a different payload")
            return _refunds[ref]  # same key + same payload -> same refund

        if _state["mode"] == "error_500":
            raise HTTPException(500, "Provider internal error")  # nothing is recorded

        ref = f"mock_ref_{uuid.uuid4().hex[:10]}"
        record = {
            "provider_reference": ref,
            "order_id": req.order_id,
            "amount_minor": req.amount_minor,
            "currency": req.currency,
            "status": "succeeded",
        }
        _refunds[ref] = record
        _by_key[idempotency_key] = (payload_hash, ref)

    # Refund is already recorded; now simulate a response that arrives too late.
    if _state["mode"] == "timeout_after_accept":
        time.sleep(_state["delay_seconds"])

    return record


@app.get("/refunds/by-key/{key}")
def get_refund_by_key(key: str):
    entry = _by_key.get(key)
    if not entry:
        raise HTTPException(404, "No refund for this idempotency key")
    return _refunds[entry[1]]


@app.get("/refunds/{reference}")
def get_refund(reference: str):
    record = _refunds.get(reference)
    if not record:
        raise HTTPException(404, "Refund not found")
    return record


@app.post("/admin/mode")
def set_mode(req: ModeRequest):
    _state["mode"] = req.mode
    _state["delay_seconds"] = req.delay_seconds
    return _state


@app.post("/admin/reset")
def reset():
    with _lock:
        _refunds.clear()
        _by_key.clear()
        _state.update(mode="none", delay_seconds=0.0)
    return {"ok": True}