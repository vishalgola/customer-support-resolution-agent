from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api import approvals, auth, orders, runs, tickets
from app.workflows.runtime import start_runtime, stop_runtime

from pathlib import Path

from fastapi.responses import FileResponse

UI_FILE = Path(__file__).parent / "static" / "index.html"



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


@app.get("/ui", include_in_schema=False)
def ui():
    return FileResponse(UI_FILE)

@app.get("/health")
def health():
    return {"status": "ok"}