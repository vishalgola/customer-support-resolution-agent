from datetime import datetime
from datetime import date
from pgvector.sqlalchemy import Vector
from sqlalchemy import Date

from sqlalchemy import (
    BigInteger, CheckConstraint, DateTime, ForeignKey, Integer,
    String, Text, UniqueConstraint, func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class Customer(Base):
    __tablename__ = "customers"

    id: Mapped[int] = mapped_column(primary_key=True)
    external_auth_subject: Mapped[str] = mapped_column(String(100), unique=True)
    name: Mapped[str] = mapped_column(String(100))
    email: Mapped[str] = mapped_column(String(200), unique=True)

    orders: Mapped[list["Order"]] = relationship(back_populates="customer")


class Order(Base):
    __tablename__ = "orders"
    __table_args__ = (
        CheckConstraint("paid_amount_minor >= 0", name="ck_order_amount_nonneg"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    customer_id: Mapped[int] = mapped_column(ForeignKey("customers.id"), index=True)
    # placed | shipped | delivered | cancelled
    status: Mapped[str] = mapped_column(String(30))
    currency: Mapped[str] = mapped_column(String(3), default="INR")
    paid_amount_minor: Mapped[int] = mapped_column(BigInteger)  # paise
    placed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    customer: Mapped[Customer] = relationship(back_populates="orders")
    items: Mapped[list["OrderItem"]] = relationship(back_populates="order")
    returns: Mapped[list["Return"]] = relationship(back_populates="order")


class OrderItem(Base):
    __tablename__ = "order_items"

    id: Mapped[int] = mapped_column(primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id"), index=True)
    product_name: Mapped[str] = mapped_column(String(200))
    quantity: Mapped[int] = mapped_column(Integer, default=1)
    paid_amount_minor: Mapped[int] = mapped_column(BigInteger)

    order: Mapped[Order] = relationship(back_populates="items")


class Return(Base):
    __tablename__ = "returns"

    id: Mapped[int] = mapped_column(primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id"), index=True)
    # requested | in_transit | received
    status: Mapped[str] = mapped_column(String(30))
    received_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # pending | passed | failed
    inspection_status: Mapped[str] = mapped_column(String(30), default="pending")

    order: Mapped[Order] = relationship(back_populates="returns")


class Ticket(Base):
    __tablename__ = "tickets"

    id: Mapped[int] = mapped_column(primary_key=True)
    customer_id: Mapped[int] = mapped_column(ForeignKey("customers.id"), index=True)
    order_id: Mapped[int | None] = mapped_column(ForeignKey("orders.id"))
    # order_status | refund_request | damaged_item
    category: Mapped[str | None] = mapped_column(String(30))
    # open | awaiting_approval | resolved | escalated
    status: Mapped[str] = mapped_column(String(30), default="open")
    summary: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class RefundOperation(Base):
    __tablename__ = "refund_operations"
    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_refund_idempotency_key"),
        CheckConstraint("amount_minor > 0", name="ck_refund_amount_positive"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id"), index=True)
    proposal_id: Mapped[int | None] = mapped_column(ForeignKey("action_proposals.id"))
    amount_minor: Mapped[int] = mapped_column(BigInteger)
    idempotency_key: Mapped[str] = mapped_column(String(100))
    provider_reference: Mapped[str | None] = mapped_column(String(100))
    # reserved | submitted | processing | succeeded | failed | unknown
    status: Mapped[str] = mapped_column(String(30), default="reserved")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class AuditEvent(Base):
    __tablename__ = "audit_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    ticket_id: Mapped[int | None] = mapped_column(ForeignKey("tickets.id"), index=True)
    actor: Mapped[str] = mapped_column(String(100))  # customer:1, system, reviewer:2
    event_type: Mapped[str] = mapped_column(String(100))
    redacted_payload: Mapped[dict] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

EMBEDDING_DIM = 384


class PolicyDocument(Base):
    __tablename__ = "policy_documents"
    __table_args__ = (
        UniqueConstraint("doc_key", "version", name="uq_policy_doc_version"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    doc_key: Mapped[str] = mapped_column(String(100))
    title: Mapped[str] = mapped_column(String(200))
    version: Mapped[int] = mapped_column(Integer)
    effective_from: Mapped[date] = mapped_column(Date)
    effective_to: Mapped[date | None] = mapped_column(Date)
    source_uri: Mapped[str] = mapped_column(String(300))

    chunks: Mapped[list["PolicyChunk"]] = relationship(
        back_populates="document", cascade="all, delete-orphan"
    )


class PolicyChunk(Base):
    __tablename__ = "policy_chunks"

    id: Mapped[int] = mapped_column(primary_key=True)
    document_id: Mapped[int] = mapped_column(
        ForeignKey("policy_documents.id", ondelete="CASCADE"), index=True
    )
    ref: Mapped[str] = mapped_column(String(200), unique=True)  # e.g. refund_policy_v1:section_3
    section: Mapped[int] = mapped_column(Integer)
    heading: Mapped[str] = mapped_column(String(200))
    content: Mapped[str] = mapped_column(Text)
    embedding: Mapped[list[float]] = mapped_column(Vector(EMBEDDING_DIM))

    document: Mapped[PolicyDocument] = relationship(back_populates="chunks")

class WorkflowRun(Base):
    __tablename__ = "workflow_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    ticket_id: Mapped[int] = mapped_column(ForeignKey("tickets.id"), index=True)
    thread_id: Mapped[str] = mapped_column(String(100), unique=True)  # LangGraph thread
    # running | awaiting_approval | answered | escalated | needs_info | rejected | expired
    # | approval_invalid | refund_succeeded | refund_unconfirmed | refund_failed | failed
    status: Mapped[str] = mapped_column(String(40))
    final_response: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ActionProposal(Base):
    __tablename__ = "action_proposals"

    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("workflow_runs.id"), index=True)
    action_type: Mapped[str] = mapped_column(String(50))
    payload: Mapped[dict] = mapped_column(JSONB)
    payload_hash: Mapped[str] = mapped_column(String(64))
    # pending | approved | rejected | expired | invalidated | executed
    status: Mapped[str] = mapped_column(String(30), default="pending")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Approval(Base):
    __tablename__ = "approvals"

    id: Mapped[int] = mapped_column(primary_key=True)
    proposal_id: Mapped[int] = mapped_column(ForeignKey("action_proposals.id"), unique=True)
    reviewer_id: Mapped[str | None] = mapped_column(String(100))
    decision: Mapped[str | None] = mapped_column(String(20))  # NULL = pending | approved | rejected | expired
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))