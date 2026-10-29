from dataclasses import dataclass

import jwt
from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.security import DEV_REVIEWERS, decode_access_token
from app.db.models import Customer
from app.db.session import get_db

bearer = HTTPBearer()


@dataclass
class Principal:
    subject: str
    role: str  # "customer" | "reviewer"
    customer_id: int | None = None


def get_principal(
    creds: HTTPAuthorizationCredentials = Depends(bearer),
    db: Session = Depends(get_db),
) -> Principal:
    try:
        claims = decode_access_token(creds.credentials)
    except jwt.PyJWTError:
        raise HTTPException(401, "Invalid or expired token")

    subject, role = claims.get("sub"), claims.get("role")

    if role == "customer":
        customer = db.scalar(select(Customer).where(Customer.external_auth_subject == subject))
        if customer is None:
            raise HTTPException(401, "Unknown customer")
        return Principal(subject, "customer", customer.id)

    if role == "reviewer" and subject in DEV_REVIEWERS:
        return Principal(subject, "reviewer")

    raise HTTPException(401, "Invalid credentials")


def require_customer(p: Principal = Depends(get_principal)) -> Principal:
    if p.role != "customer":
        raise HTTPException(403, "Customer access required")
    return p


def require_reviewer(p: Principal = Depends(get_principal)) -> Principal:
    if p.role != "reviewer":
        raise HTTPException(403, "Reviewer access required")
    return p