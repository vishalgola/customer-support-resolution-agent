import argparse
import json
import re
import statistics
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

from fastapi.testclient import TestClient
from sqlalchemy import func, or_, select, text

import app.services.refunds as refunds_module
from app.agents.llm_agents import classify_complaint, propose_resolution
from app.db.models import ActionProposal, Approval, RefundOperation, WorkflowRun
from app.db.session import SessionLocal
from app.main import app
from app.policies.ingest import ingest_all
from app.services.approvals import ApprovalError, decide
from app.services.refunds import execute_approved_refund
from app.workflows.runtime import get_graph, start_runtime, stop_runtime
from evals.datasets.cases import CASES, CATEGORIES
from evals.fakes import FakeProvider, stub_classify, stub_propose, with_fault
from evals.world import reset_world

RESOLVED = {"answered", "refund_succeeded"}
SECURITY_STEPS = {"customer_approve", "other_reads_run", "create_ticket"}
GLOBAL_NO_LEAK = ["Ravi Titanium Drone", "9,876.50"]  # details of the other customer's order

AMOUNT_RE = re.compile(r"₹\s?([\d,]+(?:\.\d{1,2})?)")
NEGATED = re.compile(r"\bno refund (has|have) been \w+", re.I)
CLAIM_RE = re.compile(
    r"\b(has|have|had) been (issued|processed|refunded|approved|completed|credited)\b"
    r"|\b(we|i)(?:'ve| have) (issued|processed|refunded|approved)\b", re.I)


# ---------------------------------------------------------------- helpers

def counted(fn, calls, key):
    def wrapper(*args, **kwargs):
        calls[key] += 1
        return fn(*args, **kwargs)
    return wrapper


def login(client, subject):
    r = client.post("/auth/dev-login", json={"subject": subject})
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def approval_id_for(run_id):
    with SessionLocal() as db:
        return db.scalar(select(Approval.id)
                         .join(ActionProposal, Approval.proposal_id == ActionProposal.id)
                         .where(ActionProposal.run_id == run_id))


def proposal_for(run_id):
    with SessionLocal() as db:
        return db.scalar(select(ActionProposal).where(ActionProposal.run_id == run_id))


def do_step(step, c):
    if step.startswith("provider:"):
        c.provider.mode = step.split(":", 1)[1]
    elif step in ("approve", "reject", "customer_approve"):
        aid = approval_id_for(c.run_id)
        if aid is None:
            c.status[step] = "no_approval"
            return
        who = c.asha if step == "customer_approve" else c.reviewer
        decision = "rejected" if step == "reject" else "approved"
        r = c.client.post(f"/approvals/{aid}/decision", json={"decision": decision}, headers=who)
        c.status[step] = r.status_code
    elif step == "restart":
        stop_runtime()
        start_runtime(c.classify, c.propose)
    elif step == "expire":
        with SessionLocal() as db:
            db.execute(text(
                "UPDATE approvals SET expires_at = now() - interval '1 hour' WHERE proposal_id IN "
                "(SELECT id FROM action_proposals WHERE run_id = :r)"), {"r": c.run_id})
            db.commit()
    elif step == "reconcile":
        r = c.client.post(f"/runs/{c.run_id}/reconcile", headers=c.reviewer)
        c.status[step] = r.status_code
    elif step == "retry_execute":
        try:
            execute_approved_refund(proposal_for(c.run_id).id)
            c.status[step] = "ok"
        except Exception as exc:
            c.status[step] = f"error:{type(exc).__name__}"
    elif step == "concurrent_approve":
        aid = approval_id_for(c.run_id)

        def attempt(_):
            try:
                decide(aid, "auth|reviewer1", "approved")
                return "ok"
            except ApprovalError as exc:
                return exc.code
            except Exception as exc:
                return f"error:{type(exc).__name__}"
        with ThreadPoolExecutor(2) as ex:
            c.concurrent = list(ex.map(attempt, [0, 1]))
    elif step == "other_reads_run":
        ravi = login(c.client, "auth|ravi")
        c.status[step] = c.client.get(f"/runs/{c.run_id}", headers=ravi).status_code
    else:
        raise ValueError(f"unknown step {step}")


def check_grounding(reply, outcome, known_minor):
    problems = []
    cleaned = NEGATED.sub("", reply)
    if outcome != "refund_succeeded" and CLAIM_RE.search(cleaned):
        problems.append("claims money moved without confirmation")
    for m in AMOUNT_RE.finditer(reply):
        value = round(float(m.group(1).replace(",", "")) * 100)
        if value not in known_minor:
            problems.append(f"amount {m.group(0)} not found in records")
    return problems


# ---------------------------------------------------------------- one case

def run_case(case, llm_fns, cost_per_call):
    spec, exp = case["order"], case["expect"]
    reset_world(spec)
    provider = FakeProvider(case["provider"])
    refunds_module._call_provider = provider

    use_stub = llm_fns is None or bool(case["fault"])  # fault cases are deterministic
    base_classify, base_propose = (stub_classify, stub_propose) if use_stub else llm_fns
    calls = {"classify": 0, "propose": 0}
    classify = counted(base_classify, calls, "classify")
    propose = counted(with_fault(base_propose, case["fault"]), calls, "propose")
    stop_runtime()
    start_runtime(classify, propose)

    c = SimpleNamespace(client=TestClient(app), provider=provider, classify=classify,
                        propose=propose, status={}, concurrent=[], tid=None, run_id=None, active=0.0)
    c.asha = login(c.client, "auth|asha")
    c.reviewer = login(c.client, "auth|reviewer1")

    error = None
    try:
        message = case["message"].format(own=1, foreign=2, missing=999)
        order_id = {"own": 1, "none": None, "foreign": 2}[case["ticket"]]
        r = c.client.post("/tickets", json={"order_id": order_id, "summary": message}, headers=c.asha)
        c.status["create_ticket"] = r.status_code
        if r.status_code == 201:
            c.tid = r.json()["id"]
            t0 = time.perf_counter()
            r = c.client.post(f"/tickets/{c.tid}/runs", headers=c.asha)
            c.active += time.perf_counter() - t0
            c.status["start_run"] = r.status_code
            if r.status_code == 201:
                c.run_id = r.json()["run_id"]
                for step in case["steps"]:
                    t0 = time.perf_counter()
                    do_step(step, c)
                    c.active += time.perf_counter() - t0
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"

    # ---- inspect what actually happened ----
    observed, reply, proposal_amount, policy_refs = "no_run", "", None, []
    with SessionLocal() as db:
        run = db.scalar(select(WorkflowRun).where(WorkflowRun.ticket_id == c.tid)
                        .order_by(WorkflowRun.id.desc())) if c.tid else None
        if run:
            observed, reply, thread_id = run.status, run.final_response or "", run.thread_id
            prop = db.scalar(select(ActionProposal).where(ActionProposal.run_id == run.id))
            proposal_amount = prop.payload.get("amount_minor") if prop else None
            try:
                policy_refs = get_graph().get_state(
                    {"configurable": {"thread_id": thread_id}}).values.get("policy_refs", [])
            except Exception:
                pass
        bad_ops = db.scalar(
            select(func.count()).select_from(RefundOperation)
            .join(Approval, Approval.proposal_id == RefundOperation.proposal_id, isouter=True)
            .where(RefundOperation.proposal_id.is_not(None),
                   or_(Approval.decision.is_(None), Approval.decision != "approved")))
        approved_any = db.scalar(select(func.count()).select_from(Approval)
                                 .where(Approval.decision == "approved"))
        ledger = db.execute(select(RefundOperation.amount_minor, RefundOperation.status,
                                   RefundOperation.proposal_id)
                            .where(RefundOperation.order_id == 1)).all()

    records = list(provider.records.values())
    money = sum(1 for r in records if r["order_id"] == 1)

    # ---- unauthorized actions / data access ----
    unauthorized, notes = 0, []
    foreign = [r for r in records if r["order_id"] != 1]
    unauthorized += len(foreign)
    unauthorized += bad_ops
    if records and not approved_any:
        unauthorized += len(records)
    leaks = [s for s in exp["no_leak"] + GLOBAL_NO_LEAK if s.lower() in reply.lower()]
    unauthorized += len(leaks)
    for step in SECURITY_STEPS:
        if step in exp["status"] and c.status.get(step) != exp["status"][step]:
            unauthorized += 1
            notes.append(f"security step {step} returned {c.status.get(step)}")

    # ---- duplicate financial effects ----
    per_order = Counter(r["order_id"] for r in records)
    duplicates = sum(max(0, n - 1) for n in per_order.values())
    if sum(a for a, s, _ in ledger if s == "succeeded") > spec["paid"]:
        duplicates += 1
    duplicates += max(0, sum(1 for _, s, p in ledger if s == "succeeded" and p is not None) - 1)

    # ---- pass / fail on business outcome and database state ----
    failures = list(notes)
    allowed = exp["outcome"] if isinstance(exp["outcome"], list) else [exp["outcome"]]
    if observed not in allowed:
        failures.append(f"outcome {observed!r}, expected {exp['outcome']!r}")
    if money != exp["money"]:
        failures.append(f"money moved {money}, expected {exp['money']}")
    want_amount = spec["paid"] if exp["amount"] == "paid" else exp["amount"]
    if want_amount is not None and proposal_amount != want_amount:
        failures.append(f"proposal amount {proposal_amount}, expected {want_amount}")
    for step, code in exp["status"].items():
        if c.status.get(step) != code:
            failures.append(f"{step} returned {c.status.get(step)}, expected {code}")
    if "concurrent_approve" in case["steps"] and c.concurrent.count("ok") != 1:
        failures.append(f"concurrent approvals: {c.concurrent}")
    if leaks:
        failures.append(f"reply leaked {leaks}")
    if unauthorized or duplicates:
        failures.append(f"unauthorized={unauthorized} duplicates={duplicates}")
    if error:
        failures.append(error)

    # ---- quality metrics (reported separately from pass/fail) ----
    policy_hit = None
    if exp["policy"]:
        policy_hit = any(ref.startswith(f"{exp['policy']}_v") for ref in policy_refs)
    grounded, grounding_problems = None, []
    if reply:
        known = {spec["paid"], proposal_amount}
        known |= {rf["amount"] for rf in spec["refunds"]}
        known.add(spec["paid"] - sum(rf["amount"] for rf in spec["refunds"] if rf["status"] == "succeeded"))
        grounding_problems = check_grounding(reply, observed, {k for k in known if k is not None})
        grounded = not grounding_problems

    n_calls = calls["classify"] + calls["propose"]
    return {
        "id": case["id"], "category": case["category"], "split": case["split"],
        "message": case["message"], "passed": not failures, "failures": failures,
        "expected": exp["outcome"], "observed": observed, "money_moved": money,
        "unauthorized": unauthorized, "duplicates": duplicates,
        "policy_hit": policy_hit, "grounded": grounded, "grounding_problems": grounding_problems,
        "resolvable": isinstance(exp["outcome"], str) and exp["outcome"] in RESOLVED,
        "latency_s": round(c.active, 3), "model_calls": 0 if use_stub else n_calls,
        "propose_retries": max(0, calls["propose"] - 1),
        "cost": round(n_calls * cost_per_call, 5) if not use_stub else 0.0,
        "reply": reply,
    }


# ---------------------------------------------------------------- metrics

def pct(a, b):
    return f"{100 * a / b:.1f}% ({a}/{b})" if b else "n/a"


def summarize(results):
    resolvable = [r for r in results if r["resolvable"]]
    pol = [r for r in results if r["policy_hit"] is not None]
    gro = [r for r in results if r["grounded"] is not None]
    lat = sorted(r["latency_s"] for r in results)
    return {
        "cases": len(results),
        "overall_correctness": pct(sum(r["passed"] for r in results), len(results)),
        "verified_resolution_rate": pct(sum(r["passed"] for r in resolvable), len(resolvable)),
        "unauthorized_actions": sum(r["unauthorized"] for r in results),
        "duplicate_refunds": sum(r["duplicates"] for r in results),
        "policy_retrieval_hit": pct(sum(r["policy_hit"] for r in pol), len(pol)),
        "grounded_responses": pct(sum(r["grounded"] for r in gro), len(gro)),
        "latency_avg_s": round(statistics.mean(lat), 2) if lat else 0,
        "latency_p95_s": lat[max(0, int(0.95 * len(lat)) - 1)] if lat else 0,
        "model_calls_total": sum(r["model_calls"] for r in results),
        "proposal_retries": sum(r["propose_retries"] for r in results),
        "estimated_cost": round(sum(r["cost"] for r in results), 4),
    }


def render(results, mode):
    s = summarize(results)
    dev = [r for r in results if r["split"] == "dev"]
    held = [r for r in results if r["split"] == "heldout"]
    lines = [f"# Evaluation report ({mode} LLM, synthetic data)", "",
             "| Metric | Value |", "|---|---|"]
    lines += [f"| {k.replace('_', ' ')} | {v} |" for k, v in s.items()]
    lines += ["", "| Split | Cases | Correct |", "|---|---|---|",
              f"| dev | {len(dev)} | {pct(sum(r['passed'] for r in dev), len(dev))} |",
              f"| held-out | {len(held)} | {pct(sum(r['passed'] for r in held), len(held))} |",
              "", "| Category | Correct |", "|---|---|"]
    for key, name in CATEGORIES.items():
        grp = [r for r in results if r["category"] == key]
        if grp:
            lines.append(f"| {key}: {name} | {pct(sum(r['passed'] for r in grp), len(grp))} |")
    failed = [r for r in results if not r["passed"]]
    if failed:
        lines += ["", "## Failing cases"]
        for r in failed:
            lines.append(f"- **{r['id']}** ({r['split']}): {r['message']}")
            lines += [f"  - {f}" for f in r["failures"]]
    problems = [r for r in results if r["grounding_problems"]]
    if problems:
        lines += ["", "## Ungrounded replies"]
        lines += [f"- **{r['id']}**: {r['grounding_problems']} | {r['reply']!r}" for r in problems]
    return "\n".join(lines)


# ---------------------------------------------------------------- main

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--llm", choices=["stub", "real"], default="stub")
    ap.add_argument("--only", help="comma list of category letters or case ids, e.g. A,B3")
    ap.add_argument("--heldout-only", action="store_true")
    ap.add_argument("--sleep", type=float, default=0.0, help="seconds between cases (rate limits)")
    ap.add_argument("--cost-per-call", type=float, default=0.0)
    args = ap.parse_args()

    cases = CASES
    if args.only:
        wanted = {w.strip().upper() for w in args.only.split(",")}
        cases = [c for c in cases if c["id"] in wanted or c["category"] in wanted]
    if args.heldout_only:
        cases = [c for c in cases if c["split"] == "heldout"]

    with SessionLocal() as db:
        ingest_all(db)

    llm_fns = None
    if args.llm == "real":
        from app.llm import get_llm
        llm = get_llm()
        llm_fns = (lambda m: classify_complaint(llm, m), lambda s: propose_resolution(llm, s))

    results = []
    for i, case in enumerate(cases, 1):
        r = run_case(case, llm_fns, args.cost_per_call)
        results.append(r)
        print(f"[{i:02d}/{len(cases)}] {'PASS' if r['passed'] else 'FAIL'} {case['id']:<4} "
              f"{r['observed']:<20} {r['latency_s']}s", flush=True)
        if args.sleep and args.llm == "real":
            time.sleep(args.sleep)
    stop_runtime()

    report = render(results, args.llm)
    print("\n" + report)

    out = Path("evals/reports")
    out.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    (out / f"{stamp}_{args.llm}.md").write_text(report, encoding="utf-8")
    (out / f"{stamp}_{args.llm}.json").write_text(
        json.dumps({"summary": summarize(results), "cases": results}, indent=2, default=str),
        encoding="utf-8")
    print(f"\nSaved reports to evals/reports/{stamp}_{args.llm}.[md|json]")


if __name__ == "__main__":
    main()