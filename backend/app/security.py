import hashlib
import secrets
from itsdangerous import BadSignature, URLSafeTimedSerializer
from fastapi import Cookie, Depends, HTTPException

from .config import Settings, get_settings

SESSION_COOKIE = "hr_session"
SESSION_MAX_AGE = 8 * 3600

def new_token() -> str:
    return secrets.token_urlsafe(32)          # 256 bits of randomness, unguessable

def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()

def _serializer(secret: str) -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(secret, salt="hr-session")

def make_session(secret: str, username: str) -> str:
    return _serializer(secret).dumps({"u": username})

def read_session(secret: str, value: str) -> str | None:
    try:
        return _serializer(secret).loads(value, max_age=SESSION_MAX_AGE)["u"]
    except (BadSignature, KeyError, TypeError):      # expired is a BadSignature subclass
        return None

def require_hr(hr_session: str | None = Cookie(None, alias=SESSION_COOKIE),
               settings: Settings = Depends(get_settings)) -> str:
    user = read_session(settings.session_secret, hr_session) if hr_session else None
    if not user:
        raise HTTPException(401, "Sign in required")
    return user