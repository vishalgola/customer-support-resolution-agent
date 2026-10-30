from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.deps import Principal, require_reviewer
from app.db.models import (ActionProposal, Approval, Customer, PolicyChunk,
                           Ticket, WorkflowRun)
from app.db.session import get_db
from app.schemas.approvals import DecisionRequest
from app.services.approvals import ApprovalError, decide
from app.tools.read_tools import get_order, get_payment_status, get_return_status

router = APIRouter(tags=["approvals"])


def _joined():
    return (
        select(Approval, ActionProposal, WorkflowRun, Ticket, Customer)
        .join(ActionProposal, Approval.proposal_id == ActionProposal.id)
        .join(WorkflowRun, ActionProposal.run_id == WorkflowRun.id)
        .join(Ticket, WorkflowRun.ticket_id == Ticket.id)
        .join(Customer, Ticket.customer_id == Customer.id)
    )


@router.get("/approvals")
def list_approvals(p: Principal = Depends(require_reviewer), db: Session = Depends(get_db)):
    now = datetime.now(timezone.utc)
    rows = db.execute(
        _joined().where(Approval.decision.is_(None), Approval.expires_at > now)
        .order_by(Approval.id)
    ).all()
    return [{
        "approval_id": a.id, "run_id": run.id, "ticket_id": t.id, "customer": c.name,
        "complaint": t.summary, "proposal": pr.payload, "payload_hash": pr.payload_hash,
        "expires_at": a.expires_at,
    } for a, pr, run, t, c in rows]


@router.get("/approvals/{approval_id}")
def approval_detail(approval_id: int, p: Principal = Depends(require_reviewer),
                    db: Session = Depends(get_db)):
    row = db.execute(_joined().where(Approval.id == approval_id)).first()
    if row is None:
        raise HTTPException(404, "Approval not found")
    a, pr, run, t, c = row
    order_id = pr.payload["order_id"]
    policies = db.scalars(select(PolicyChunk).where(PolicyChunk.ref.in_(pr.payload["policy_refs"])))
    return {
        "approval_id": a.id, "run_id": run.id, "ticket_id": t.id, "customer": c.name,
        "complaint": t.summary, "decision": a.decision, "expires_at": a.expires_at,
        "proposal": pr.payload, "payload_hash": pr.payload_hash,
        "evidence": {
            "order": get_order(db, c.id, order_id).data,
            "return": get_return_status(db, c.id, order_id).data,
            "payment": get_payment_status(db, c.id, order_id).data,
        },
        "policies": [{"ref": x.ref, "heading": x.heading, "text": x.content} for x in policies],
    }


@router.post("/approvals/{approval_id}/decision")
def decide_approval(approval_id: int, body: DecisionRequest,
                    p: Principal = Depends(require_reviewer)):
    try:
        result = decide(approval_id, p.subject, body.decision)
    except ApprovalError as e:
        raise HTTPException(404 if e.code == "NOT_FOUND" else 409,
                            {"code": e.code, "message": e.message})
    if result["decision"] == "expired":
        raise HTTPException(409, {"code": "APPROVAL_EXPIRED",
                                  "message": "This approval expired. The case was escalated."})
    return result