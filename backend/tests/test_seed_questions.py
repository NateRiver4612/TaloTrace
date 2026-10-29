"""Smoke test for the question loader.

Run from the backend directory:

    .venv/bin/python -m pytest tests/test_seed_questions.py -q

Expected output:

    .                                                                    [100%]
    1 passed in 0.05s

The test uses a throwaway SQLite file under pytest's tmp_path, so it never
touches data/hr.db.
"""

from sqlalchemy import func, select
from sqlalchemy.orm import sessionmaker

from app.db import Base, make_engine
from app.models import Question
from app.seed_questions import load_questions

ITEMS = [
    {
        "code": f"Q{i}",
        "position": i,
        "text": f"Question {i}",
        "grading_guide": "\n".join(
            f"- **{level}:** Guide level {level}" for level in range(1, 5)
        ),
    }
    for i in range(1, 7)
]


def test_loader_is_idempotent(tmp_path):
    engine = make_engine(f"sqlite:///{tmp_path / 'smoke.db'}")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)

    with Session() as db:
        load_questions(db, ITEMS)
        load_questions(db, ITEMS)

        assert db.scalar(select(func.count()).select_from(Question)) == 6

        rows = list(db.scalars(select(Question).order_by(Question.position)))
        assert [row.code for row in rows] == [f"Q{i}" for i in range(1, 7)]
        assert [row.position for row in rows] == list(range(1, 7))
        assert all(row.text and row.grading_guide for row in rows)
        assert all("**4:**" in row.grading_guide for row in rows)

    engine.dispose()
