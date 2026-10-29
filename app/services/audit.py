from sqlalchemy.orm import Session

from app.db.models import AuditEvent


def add_audit(db: Session, ticket_id: int, actor: str, event_type: str, payload: dict | None = None):
    db.add(AuditEvent(ticket_id=ticket_id, actor=actor, event_type=event_type,
                      redacted_payload=payload or {}))