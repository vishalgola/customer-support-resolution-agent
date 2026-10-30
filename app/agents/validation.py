from app.schemas.workflow import ResolutionProposal


def expected_action(category: str, eligibility: dict | None) -> str:
    """What the business rules allow, derived without the LLM."""
    if category == "refund_request":
        decision = (eligibility or {}).get("decision")
        return {
            "eligible": "request_refund_approval",
            "ineligible": "answer_only",
            "needs_review": "escalate",
        }.get(decision, "escalate")
    if category == "order_status":
        return "answer_only"
    return "escalate"  # damaged_item and other go to a human for now


def validate_proposal(
    proposal: ResolutionProposal,
    *,
    category: str,
    order_id: int,
    eligibility: dict | None,
    known_evidence: set[str],
    retrieved_refs: set[str],
) -> list[str]:
    errors = []

    if proposal.order_id != order_id:
        errors.append("ORDER_MISMATCH")

    expected = expected_action(category, eligibility)
    if proposal.action != expected:
        errors.append(f"ACTION_NOT_ALLOWED: proposed {proposal.action}, rules allow {expected}")

    if proposal.action == "request_refund_approval":
        if proposal.amount_minor != (eligibility or {}).get("refundable_amount_minor"):
            errors.append("AMOUNT_MISMATCH")
        if proposal.currency != "INR":
            errors.append("CURRENCY_MISMATCH")
    elif proposal.amount_minor is not None:
        errors.append("AMOUNT_NOT_ALLOWED_FOR_ACTION")

    if not set(proposal.evidence_refs) <= known_evidence:
        errors.append("UNKNOWN_EVIDENCE_REF")
    if not set(proposal.policy_refs) <= retrieved_refs:
        errors.append("UNKNOWN_POLICY_REF")

    if proposal.action != "escalate":
        if not proposal.evidence_refs:
            errors.append("MISSING_EVIDENCE")
        # Citation sirf refund decisions ke liye zaroori hai.
        # Order-status ka jawab records se aata hai, policy se nahi.
        if category == "refund_request" and not proposal.policy_refs:
            errors.append("MISSING_POLICY_CITATION")

    return errors