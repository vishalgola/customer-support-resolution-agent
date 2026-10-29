from typing import Literal, TypedDict

from pydantic import BaseModel, Field

Category = Literal["order_status", "refund_request", "damaged_item", "other"]
Action = Literal["answer_only", "request_refund_approval", "escalate"]


class Classification(BaseModel):
    category: Category
    order_id: int | None = Field(None, description="Order number the customer states, if any")
    missing_information: list[str] = Field(default_factory=list)


class ResolutionProposal(BaseModel):
    action: Action
    order_id: int | None = None
    amount_minor: int | None = None
    currency: str = "INR"
    reason: str
    evidence_refs: list[str] = Field(default_factory=list)
    policy_refs: list[str] = Field(default_factory=list)
    missing_information: list[str] = Field(default_factory=list)
    explanation: str = Field(description="1-3 sentence customer-facing explanation")


class SupportState(TypedDict, total=False):
    ticket_id: int
    customer_id: int
    customer_message: str
    order_id: int | None
    category: str
    missing_information: list[str]
    verified_facts: dict
    evidence_refs: list[str]
    eligibility: dict | None
    policy_hits: list[dict]
    policy_refs: list[str]
    proposal: dict | None
    validation_errors: list[str]
    errors: list[str]
    outcome: str  # answered | awaiting_approval | escalated | needs_info
    final_response: str
    run_id: int
    proposal_id: int
    approval_id: int