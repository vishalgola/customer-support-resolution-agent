from app.agents.llm_agents import classify_complaint, propose_resolution
from app.db.models import Ticket
from app.db.session import SessionLocal
from app.llm import get_llm
from app.workflows.support_graph import build_graph

llm = get_llm()
graph = build_graph(
    lambda message: classify_complaint(llm, message),
    lambda state: propose_resolution(llm, state),
)

SCENARIOS = [  # (customer_id, order_id, message, what we expect)
    (1, 1, "I returned my headphones last week but I still haven't received my refund.", "awaiting_approval"),
    (1, 2, "Please refund my speaker.", "answered"),
    (1, 4, "Where is my keyboard order?", "answered"),
    (1, None, "I want a refund for order 5", "needs_info"),
    (1, 1, "The headphones I received were cracked.", "escalated"),
    (1, None, "Tell me a joke", "escalated"),
]

for cust, order, message, expected in SCENARIOS:
    with SessionLocal() as db:
        t = Ticket(customer_id=cust, order_id=order, summary=message)
        db.add(t)
        db.commit()
        tid = t.id
    out = graph.invoke({"ticket_id": tid})
    flag = "OK " if out["outcome"] == expected else "DIFF"
    print(f"\n[{flag}] {message}\n  outcome: {out['outcome']}  (expected {expected})")
    print(f"  validation_errors: {out.get('validation_errors')}")
    print(f"  reply: {out['final_response']}")