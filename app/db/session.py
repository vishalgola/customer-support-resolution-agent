from sqlalchemy.orm.session import Session


from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.config import settings

engine = create_engine(settings.database_url, pool_pre_ping=True)
SessionLocal = sessionmaker[Session](bind=engine, expire_on_commit=False)


def get_db():
    with SessionLocal() as db:
        yield db