import secrets

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel

from ..config import Settings, get_settings
from ..deps import require_hr
from ..security import SESSION_COOKIE, SESSION_MAX_AGE, make_session

router = APIRouter(prefix="/api/hr", tags=["hr-auth"])


class LoginIn(BaseModel):
    username: str
    password: str


@router.post("/login")
def login(body: LoginIn, response: Response, settings: Settings = Depends(get_settings)):
    """MOCK SIGN-IN: one shared demo account from env vars. Not real authentication."""
    ok = (secrets.compare_digest(body.username.encode(), settings.hr_username.encode())
          and secrets.compare_digest(body.password.encode(), settings.hr_password.encode()))
    if not ok:
        raise HTTPException(401, "Wrong username or password")
    response.set_cookie(SESSION_COOKIE, make_session(settings.session_secret, body.username),
                        httponly=True, samesite="lax", max_age=SESSION_MAX_AGE,
                        secure=False)            # set True behind HTTPS
    return {"username": body.username, "mock": True}


@router.post("/logout")
def logout(response: Response):
    response.delete_cookie(SESSION_COOKIE)
    return {"ok": True}


@router.get("/me")
def me(user: str = Depends(require_hr)):
    return {"username": user, "mock": True}