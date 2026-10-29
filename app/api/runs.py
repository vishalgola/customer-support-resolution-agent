from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.deps import Principal, get_principal, require_customer, require_reviewer
from app.db.models import AuditEvent, Ticket, WorkflowRun
from app.db.session import get_db
from app.services.runs import RunError, reconcile_run, start_run

router = APIRouter(tags=["runs"])


def _visible_run(db: Session, run_id: int, p: Principal) -> tuple[WorkflowRun, Ticket]:
    run = db.get(WorkflowRun, run_id)
    ticket = db.get(Ticket, run.ticket_id) if run else None
    if ticket is None or (p.role == "customer" and ticket.customer_id != p.customer_id):
        raise HTTPException(404, "Run not found")
    return run, ticket


@router.post("/tickets/{ticket_id}/runs", status_code=201)
def create_run(ticket_id: int, p: Principal = Depends(require_customer),
               db: Session = Depends(get_db)):
    ticket = db.get(Ticket, ticket_id)
    if ticket is None or ticket.customer_id != p.customer_id:
        raise HTTPException(404, "Ticket not found")
    try:
        return start_run(ticket_id)
    except RunError as e:
        raise HTTPException(409, {"code": e.code, "message": e.message})


@router.get("/runs/{run_id}")
def get_run(run_id: int, p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    run, _ = _visible_run(db, run_id, p)
    return {"run_id": run.id, "ticket_id": run.ticket_id, "status": run.status,
            "final_response": run.final_response}


@router.get("/runs/{run_id}/events")
def get_run_events(run_id: int, p: Principal = Depends(get_principal),
                   db: Session = Depends(get_db)):
    run, _ = _visible_run(db, run_id, p)
    events = db.scalars(select(AuditEvent).where(AuditEvent.ticket_id == run.ticket_id)
                        .order_by(AuditEvent.id))
    return [{"actor": e.actor, "event_type": e.event_type,
             "payload": e.redacted_payload, "created_at": e.created_at} for e in events]


@router.post("/runs/{run_id}/reconcile")
def reconcile(run_id: int, p: Principal = Depends(require_reviewer)):
    try:
        return reconcile_run(run_id)
    except RunError as e:
        raise HTTPException(404 if e.code == "NOT_FOUND" else 409,
                            {"code": e.code, "message": e.message})