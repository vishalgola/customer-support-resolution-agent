from datetime import datetime, timedelta, timezone

import jwt

from app.config import settings

# Demo only: real systems get roles from an identity provider.
DEV_REVIEWERS = {"auth|reviewer1"}


def create_access_token(subject: str, role: str, minutes: int = 60) -> str:
    payload = {
        "sub": subject,
        "role": role,
        "exp": datetime.now(timezone.utc) + timedelta(minutes=minutes),
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def decode_access_token(token: str) -> dict:
    return jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])