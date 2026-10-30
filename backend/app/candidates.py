import sys

from sqlalchemy import select
from sqlalchemy.orm import Session

from .db import Base, SessionLocal, engine
from .models import Candidate, Role
from .security import hash_token, new_token


def create_candidate(db: Session, role_title: str, name: str) -> tuple[Candidate, str]:
    """Returns (candidate, raw_token). The raw token exists only in this return value."""
    role = db.scalar(select(Role).where(Role.title == role_title))
    if role is None:
        role = Role(title=role_title)
        db.add(role)
        db.flush()
    token = new_token()
    cand = Candidate(role_id=role.id, name=name, token_hash=hash_token(token))
    db.add(cand)
    db.commit()
    return cand, token


if __name__ == "__main__":
    # usage: python -m app.candidates "Backend Engineer" "Alice"
    Base.metadata.create_all(engine)
    with SessionLocal() as db:
        _, tok = create_candidate(db, sys.argv[1], sys.argv[2])
    print(f"Interview link: http://localhost:3000/interview/{tok}")