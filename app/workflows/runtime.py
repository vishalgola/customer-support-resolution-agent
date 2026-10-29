from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool
from langgraph.checkpoint.postgres import PostgresSaver

from app.agents.llm_agents import classify_complaint, propose_resolution
from app.config import settings
from app.llm import get_llm
from app.workflows.support_graph import build_graph

_state: dict = {"graph": None, "pool": None}


def start_runtime(classify_fn=None, propose_fn=None) -> None:
    """Pass fake functions in tests; with no arguments the real LLM is used."""
    conninfo = settings.database_url.replace("postgresql+psycopg://", "postgresql://")
    pool = ConnectionPool(
        conninfo=conninfo, max_size=5, open=True,
        kwargs={"autocommit": True, "prepare_threshold": 0, "row_factory": dict_row},
    )
    saver = PostgresSaver(pool)
    saver.setup()  # creates LangGraph's checkpoint tables on first run
    if classify_fn is None:
        llm = get_llm()
        classify_fn = lambda message: classify_complaint(llm, message)
        propose_fn = lambda state: propose_resolution(llm, state)
    _state["pool"] = pool
    _state["graph"] = build_graph(classify_fn, propose_fn, checkpointer=saver)


def stop_runtime() -> None:
    if _state["pool"] is not None:
        _state["pool"].close()
    _state["pool"] = _state["graph"] = None


def get_graph():
    if _state["graph"] is None:
        raise RuntimeError("Workflow runtime is not started")
    return _state["graph"]