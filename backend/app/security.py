import hashlib
import secrets


def new_token() -> str:
    return secrets.token_urlsafe(32)          # 256 bits of randomness, unguessable


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()