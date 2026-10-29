from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.auth.deps import Principal, get_principal, require_customer, require_reviewer
from app.db.models import AuditEvent, Order, Ticket
from app.db.session import get_db
from app.schemas.tickets import TicketCreate, TicketOut

router = APIRouter(tags=["tickets"])


@router.post("/tickets", response_model=TicketOut, status_code=201)
def create_ticket(body: TicketCreate, p: Principal = Depends(require_customer),
                  db: Session = Depends(get_db)):
    if body.order_id is not None:
        order = db.get(Order, body.order_id)
        if order is None or order.customer_id != p.customer_id:
            raise HTTPException(404, "Order not found")

    ticket = Ticket(customer_id=p.customer_id, order_id=body.order_id, summary=body.summary)
    db.add(ticket)
    db.flush()
    db.add(AuditEvent(ticket_id=ticket.id, actor=f"customer:{p.customer_id}",
                      event_type="ticket_created",
                      redacted_payload={"order_id": body.order_id}))
    db.commit()
    db.refresh(ticket)
    return ticket


@router.get("/tickets/{ticket_id}", response_model=TicketOut)
def get_ticket(ticket_id: int, p: Principal = Depends(get_principal),
               db: Session = Depends(get_db)):
    ticket = db.get(Ticket, ticket_id)
    if ticket is None or (p.role == "customer" and ticket.customer_id != p.customer_id):
        raise HTTPException(404, "Ticket not found")
    return ticket


