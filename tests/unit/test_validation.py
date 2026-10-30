from app.agents.validation import validate_proposal
from app.schemas.workflow import ResolutionProposal


def proposal(**kw):
    base = dict(action="answer_only", order_id=1, reason="x",
                evidence_refs=["order_1"], policy_refs=[], explanation="ok")
    return ResolutionProposal(**{**base, **kw})


def check(p, category="order_status", eligibility=None):
    return validate_proposal(p, category=category, order_id=1, eligibility=eligibility,
                             known_evidence={"order_1"},
                             retrieved_refs={"shipping_delays_v1:section_2"})


def test_order_status_answer_needs_no_policy_citation():
    assert check(proposal()) == []


def test_refund_answer_still_needs_a_policy_citation():
    errors = check(proposal(), category="refund_request", eligibility={"decision": "ineligible"})
    assert "MISSING_POLICY_CITATION" in errors


def test_invented_citation_is_still_blocked_for_order_status():
    assert "UNKNOWN_POLICY_REF" in check(proposal(policy_refs=["refund_policy_v9:section_1"]))