from datetime import datetime, timezone
from uuid import uuid4

from langgraph.types import Command
from sqlalchemy import select

from app.db.models import ActionProposal, Ticket, WorkflowRun
from app.db.session import SessionLocal
from app.services.audit import add_audit
from app.services.refunds import describe_outcome, execute_approved_refund
from app.workflows.runtime import get_graph

ACTIVE = ("running", "awaiting_approval")


class RunError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def _config(thread_id: str) -> dict:
    return {"configurable": {"thread_id": thread_id}}


def _view(run: WorkflowRun) -> dict:
    return {"run_id": run.id, "ticket_id": run.ticket_id,
            "status": run.status, "final_response": run.final_response}


def sync_run(run_id: int) -> dict:
    """Copy the workflow's current state into the workflow_runs row."""
    with SessionLocal() as db:
        run = db.get(WorkflowRun, run_id)
        snapshot = get_graph().get_state(_config(run.thread_id))
        values = snapshot.values or {}
        if snapshot.next:  # paused at the approval step
            run.status = "awaiting_approval"
        else:
            run.status = values.get("outcome", "completed")
            run.completed_at = datetime.now(timezone.utc)
        run.final_response = values.get("final_response")
        db.commit()
        return _view(run)


def start_run(ticket_id: int) -> dict:
    with SessionLocal() as db:
        active = db.scalar(select(WorkflowRun).where(
            WorkflowRun.ticket_id == ticket_id, WorkflowRun.status.in_(ACTIVE)))
        if active:
            raise RunError("RUN_ALREADY_ACTIVE", "This ticket already has an active run.")
        run = WorkflowRun(ticket_id=ticket_id, thread_id=f"run-{uuid4().hex}", status="running")
        db.add(run)
        db.commit()
        run_id, thread_id = run.id, run.thread_id

    try:
        get_graph().invoke({"ticket_id": ticket_id, "run_id": run_id}, _config(thread_id))
    except Exception as exc:
        with SessionLocal() as db:
            db.get(WorkflowRun, run_id).status = "failed"
            add_audit(db, ticket_id, "system", "run_failed", {"error": str(exc)[:200]})
            db.commit()
        raise RunError("RUN_FAILED", "The workflow failed; your case has not been changed.") from exc
    return sync_run(run_id)


def resume_run(run_id: int, approval_id: int) -> dict:
    with SessionLocal() as db:
        thread_id = db.get(WorkflowRun, run_id).thread_id
    # The resume value is only a signal. What happens next is decided by the stored approval.
    get_graph().invoke(Command(resume={"approval_id": approval_id}), _config(thread_id))
    return sync_run(run_id)


def reconcile_run(run_id: int) -> dict:
    """For a refund whose outcome was unconfirmed: ask the provider by key, never blindly re-send."""
    with SessionLocal() as db:
        run = db.get(WorkflowRun, run_id)
        if run is None:
            raise RunError("NOT_FOUND", "Run not found.")
        if run.status != "refund_unconfirmed":
            raise RunError("NOT_RECONCILABLE", "This run has no unconfirmed refund.")
        proposal = db.scalar(select(ActionProposal).where(ActionProposal.run_id == run_id))
        ticket_id, proposal_id = run.ticket_id, proposal.id

    result = execute_approved_refund(proposal_id)
    outcome, ticket_status, message = describe_outcome(result)

    with SessionLocal() as db:
        run = db.get(WorkflowRun, run_id)
        run.status, run.final_response = outcome, message
        db.get(Ticket, ticket_id).status = ticket_status
        add_audit(db, ticket_id, "system", "reconciled", {"outcome": outcome})
        db.commit()
        return _view(run)