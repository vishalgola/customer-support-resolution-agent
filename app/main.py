from fastapi import FastAPI

from app.api import auth, orders, tickets

app = FastAPI(title="Customer Support Resolution Agent")
app.include_router(auth.router)
app.include_router(orders.router)
app.include_router(tickets.router)


@app.get("/health")
def health():
    return {"status": "ok"}