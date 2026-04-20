import base64
import hashlib
import hmac
import os
import time
from fastapi import Depends, Header, HTTPException
from sqlalchemy.orm import Session
from db import get_db
import models

SESSION_SECRET = os.environ.get("SESSION_SECRET", "dev-secret-change-me")
TOKEN_TTL = 60 * 60 * 24 * 30


def hash_pin(pin: str) -> str:
    return hashlib.sha256(f"{SESSION_SECRET}.{pin}".encode()).hexdigest()


def verify_pin(pin: str, pin_hash: str) -> bool:
    return hmac.compare_digest(hash_pin(pin), pin_hash or "")


def make_token(user_id: str, ttl: int = TOKEN_TTL) -> str:
    expiry = int(time.time()) + ttl
    payload = f"{user_id}.{expiry}"
    sig = hmac.new(SESSION_SECRET.encode(), payload.encode(), hashlib.sha256).hexdigest()
    return base64.urlsafe_b64encode(f"{payload}.{sig}".encode()).decode().rstrip("=")


def parse_token(token: str):
    try:
        padded = token + "=" * (-len(token) % 4)
        decoded = base64.urlsafe_b64decode(padded).decode()
        user_id, expiry, sig = decoded.rsplit(".", 2)
        expected = hmac.new(SESSION_SECRET.encode(), f"{user_id}.{expiry}".encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(sig, expected):
            return None
        if int(expiry) < int(time.time()):
            return None
        return user_id
    except Exception:
        return None


def current_user(
    authorization: str = Header(default=""),
    db: Session = Depends(get_db),
) -> models.User:
    if not authorization.startswith("Bearer "):
        raise HTTPException(401, "missing bearer token")
    token = authorization[7:].strip()
    user_id = parse_token(token)
    if not user_id:
        raise HTTPException(401, "invalid or expired token")
    user = db.query(models.User).get(user_id)
    if not user:
        raise HTTPException(401, "user not found")
    return user
