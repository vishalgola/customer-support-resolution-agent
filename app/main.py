from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api import approvals, auth, orders, runs, tickets
from app.workflows.runtime import start_runtime, stop_runtime


@asynccontextmanager
async def lifespan(app: FastAPI):
    start_runtime()  # real LLM + Postgres checkpointer
    yield
    stop_runtime()


app = FastAPI(title="Customer Support Resolution Agent", lifespan=lifespan)
app.include_router(auth.router)
app.include_router(orders.router)
app.include_router(tickets.router)
app.include_router(runs.router)
app.include_router(approvals.router)


@app.get("/health")
def health():
    return {"status": "ok"}