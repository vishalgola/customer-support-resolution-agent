import re

from app.agents.validation import expected_action
from app.schemas.workflow import Classification, ResolutionProposal


class FakeProvider:
    """Stands in for the payment provider. Modes: ok | timeout_after_accept | down | reject."""

    def __init__(self, mode: str = "ok"):
        self.mode = mode
        self.calls = 0
        self.records: dict[str, dict] = {}  # idempotency key -> record

    def __call__(self, order_id, amount_minor, key):
        self.calls += 1
        if key in self.records:  # the real client reconciles by key first
            return "succeeded", self.records[key]["ref"]
        if self.mode == "down":
            return "unknown", None  # nothing recorded, outcome unconfirmed
        if self.mode == "reject":
            return "failed", None
        self.records[key] = {"ref": f"ref-{len(self.records) + 1}",
                             "order_id": order_id, "amount_minor": amount_minor}
        if self.mode == "timeout_after_accept":
            return "unknown", None  # money moved, but we never saw the response
        return "succeeded", self.records[key]["ref"]


def stub_classify(message: str) -> Classification:
    m = message.lower()
    found = re.search(r"(?:order\s*#?|#)(\d+)", m)
    if re.search(r"broken|cracked|damaged|defective|dent", m):
        category = "damaged_item"
    elif re.search(r"refund|money back|return", m):
        category = "refund_request"
    elif re.search(r"where is|track|status|ship|deliver|arrive|when will|update", m):
        category = "order_status"
    else:
        category = "other"
    return Classification(category=category, order_id=int(found.group(1)) if found else None)


def stub_propose(state: dict) -> ResolutionProposal:
    action = expected_action(state["category"], state.get("eligibility"))
    elig = state.get("eligibility") or {}
    status = state["verified_facts"]["order"]["status"]
    return ResolutionProposal(
        action=action,
        order_id=state["order_id"],
        amount_minor=elig.get("refundable_amount_minor") if action == "request_refund_approval" else None,
        reason="stub",
        evidence_refs=list(state["evidence_refs"]),
        policy_refs=state["policy_refs"][:1],
        explanation=elig.get("message") or f"Your order is currently {status}.",
    )


def with_fault(propose_fn, fault: str | None):
    """Wrap a proposer so it misbehaves in a specific way. The validator must catch it."""
    if fault is None:
        return propose_fn
    if fault == "proposer_raises":
        def raises(state):
            raise ValueError("simulated model failure")
        return raises

    def faulty(state):
        p = propose_fn(state)
        if fault == "fake_citation":
            return p.model_copy(update={"policy_refs": ["refund_policy_v9:section_1"]})
        if fault == "wrong_amount":
            return p.model_copy(update={"amount_minor": 999999})
        if fault == "approves_ineligible":
            return p.model_copy(update={
                "action": "request_refund_approval",
                "amount_minor": state["verified_facts"]["order"]["paid_amount_minor"]})
        raise ValueError(f"unknown fault {fault}")
    return faulty