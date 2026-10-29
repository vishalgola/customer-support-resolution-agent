from app.db.session import SessionLocal
from app.policies.ingest import ingest_all

with SessionLocal() as s:
    print(f"Ingested {ingest_all(s)} policy chunks.")