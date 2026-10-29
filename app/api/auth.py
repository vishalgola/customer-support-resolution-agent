from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.security import DEV_REVIEWERS, create_access_token
from app.db.models import Customer
from app.db.session import get_db

router = APIRouter(tags=["auth"])


class LoginRequest(BaseModel):
    subject: str


@router.post("/auth/dev-login")
def dev_login(req: LoginRequest, db: Session = Depends(get_db)):
    """DEMO ONLY: no password. Replace with a real identity provider."""
    customer = db.scalar(select(Customer).where(Customer.external_auth_subject == req.subject))
    if customer:
        role = "customer"
    elif req.subject in DEV_REVIEWERS:
        role = "reviewer"
    else:
        raise HTTPException(401, "Unknown user")
    return {"access_token": create_access_token(req.subject, role),
            "token_type": "bearer", "role": role}