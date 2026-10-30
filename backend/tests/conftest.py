"""Shared pytest fixtures for the API tests.

Every test gets a temporary SQLite database and upload directory, so the suite
never touches the real ``data/hr.db`` or ``data/uploads`` files.
"""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from app.config import Settings, get_settings
from app.db import Base, get_db, make_engine
from app.main import app
from app.models import Candidate, Question, Role
from app.security import hash_token


@pytest.fixture
def session_factory(tmp_path):
    """Create a throwaway SQLite database and return its session factory."""
    engine = make_engine(f"sqlite:///{tmp_path / 'test.db'}")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    yield Session
    engine.dispose()


@pytest.fixture
def settings(tmp_path):
    """Point uploads and the database URL at this test's temporary folder."""
    return Settings(
        upload_dir=str(tmp_path / "uploads"),
        database_url=f"sqlite:///{tmp_path / 'test.db'}",
    )


@pytest.fixture
def client(session_factory, settings):
    """``TestClient`` wired to the temporary database.

    We deliberately do not use ``with TestClient(app)``: that runs the app
    lifespan, which would create tables in the real ``data/hr.db``.
    """

    def override_db():
        with session_factory() as db:
            yield db

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_settings] = lambda: settings
    test_client = TestClient(app)
    try:
        yield test_client
    finally:
        test_client.close()
        app.dependency_overrides.clear()


@pytest.fixture
def seeded(session_factory):
    """Insert two candidates and six questions, then return their identifiers."""
    with session_factory() as db:
        role = Role(title="Backend Engineer")
        db.add(role)
        db.flush()

        alice = Candidate(role_id=role.id, name="Alice", token_hash=hash_token("alice-token"))
        bob = Candidate(role_id=role.id, name="Bob", token_hash=hash_token("bob-token"))
        db.add_all([alice, bob])

        questions = [
            Question(
                code=f"Q{i}",
                position=i,
                text=f"Question {i}",
                grading_guide=(
                    f"SECRET GUIDE for Q{i}\n"
                    "- **1:** ...\n- **2:** ...\n- **3:** ...\n- **4:** ..."
                ),
            )
            for i in range(1, 7)
        ]
        db.add_all(questions)
        db.commit()

        return {
            "token": "alice-token",
            "token2": "bob-token",
            "q_ids": [q.id for q in questions],
        }
