from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.deps import Principal, require_reviewer
from app.db.models import ActionProposal, Approval, Customer, Ticket, WorkflowRun
from app.db.session import get_db
from app.schemas.approvals import DecisionRequest
from app.services.approvals import ApprovalError, decide

router = APIRouter(tags=["approvals"])


@router.get("/approvals")
def list_approvals(p: Principal = Depends(require_reviewer), db: Session = Depends(get_db)):
    now = datetime.now(timezone.utc)
    rows = db.execute(
        select(Approval, ActionProposal, Ticket, Customer)
        .join(ActionProposal, Approval.proposal_id == ActionProposal.id)
        .join(WorkflowRun, ActionProposal.run_id == WorkflowRun.id)
        .join(Ticket, WorkflowRun.ticket_id == Ticket.id)
        .join(Customer, Ticket.customer_id == Customer.id)
        .where(Approval.decision.is_(None), Approval.expires_at > now)
        .order_by(Approval.id)
    ).all()
    return [{
        "approval_id": a.id, "ticket_id": t.id, "customer": c.name,
        "complaint": t.summary, "proposal": pr.payload, "payload_hash": pr.payload_hash,
        "expires_at": a.expires_at,
    } for a, pr, t, c in rows]


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