from datetime import datetime, timezone

from sqlalchemy import select

from app.db.models import ActionProposal, Approval, WorkflowRun
from app.db.session import SessionLocal
from app.services.audit import add_audit
from app.services.payload import hash_payload
from app.services.runs import resume_run


class ApprovalError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def decide(approval_id: int, reviewer_subject: str, decision: str) -> dict:
    now = datetime.now(timezone.utc)
    with SessionLocal() as s, s.begin():
        approval = s.execute(  # row lock: two reviewers can't both decide
            select(Approval).where(Approval.id == approval_id).with_for_update()
        ).scalar_one_or_none()
        if approval is None:
            raise ApprovalError("NOT_FOUND", "Approval not found.")
        if approval.decision is not None:
            raise ApprovalError("ALREADY_DECIDED", "This approval has already been decided.")

        proposal = s.get(ActionProposal, approval.proposal_id)
        run_id = proposal.run_id
        ticket_id = s.get(WorkflowRun, run_id).ticket_id
        if proposal.payload_hash != hash_payload(proposal.payload):
            raise ApprovalError("PAYLOAD_CHANGED", "The proposal changed after it was created.")

        if now > approval.expires_at:
            stored, proposal.status = "expired", "expired"
        else:
            stored = decision
            proposal.status = "approved" if decision == "approved" else "rejected"
            approval.reviewer_id = reviewer_subject
        approval.decision, approval.decided_at = stored, now
        add_audit(s, ticket_id, f"reviewer:{reviewer_subject}", f"approval_{stored}",
                  {"approval_id": approval_id, "proposal_id": proposal.id,
                   "payload_hash": proposal.payload_hash})

    # The decision is committed. Now resume the paused workflow, which reads it back from the DB.
    run = resume_run(run_id, approval_id)
    return {"approval_id": approval_id, "decision": stored, "run": run}