"""Load the six interview questions from the data pack into SQLite.

Run from the backend directory:

    .venv/bin/python -m app.seed_questions
    .venv/bin/python -m app.seed_questions ../data/business-systems-pack/questions.md

Expected terminal output:

    Loaded 6 questions

Expected rows after the first run:

    sqlite> SELECT code, position, text FROM questions ORDER BY position;
    Q1|1|Tell us about a piece of work you are proud of. What was your part in it?
    Q2|2|Describe a time you had to meet a tight deadline. What did you do?
    Q3|3|Tell us about a disagreement with a colleague. How did you resolve it?
    Q4|4|Tell us about a mistake you made at work. What did you learn?
    Q5|5|How do you decide what to work on first when everything feels urgent?
    Q6|6|What have you learned recently, and how did you learn it?

Each grading_guide stores the four bullets for that question, for example Q1:

    - **1:** Cannot name a piece of work, or describes the team's work with no personal part.
    - **2:** Names a piece of work but the personal contribution stays vague.
    - **3:** Clear personal contribution and a result someone could check.
    - **4:** Clear contribution, a measured result, and what they would do differently.

The loader is idempotent: running it again updates the existing six rows and
prints "Loaded 6 questions" instead of inserting duplicates.
"""

import argparse
import re
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import REPO_ROOT
from .db import Base, SessionLocal, engine
from .models import Question

# Default input: the synthetic data pack copied into the repo.
DEFAULT_QUESTIONS_PATH = REPO_ROOT / "data" / "business-systems-pack" / "questions.md"

# Regex for each question heading in questions.md.
#
# Raw heading example:
#   "## Q1. Tell us about a piece of work you are proud of. What was your part in it?\n"
#
# Captures:
#   group(1) -> "1"
#   group(2) -> "Tell us about a piece of work you are proud of. What was your part in it?"
#
# Pattern pieces:
#   ^            start of a line because re.MULTILINE is set
#   ##\s+        literal "##" followed by whitespace
#   Q(\d+)       literal "Q" followed by digits; capture the digits
#   \.\s+        literal "." followed by whitespace
#   (.+?)        capture the question text, non-greedy
#   \s*$         allow trailing spaces and stop at the end of the line
QUESTION_HEADING = re.compile(r"^##\s+Q(\d+)\.\s+(.+?)\s*$", re.MULTILINE)


def parse_questions_md(raw: str) -> list[dict[str, str | int]]:
    """Parse `## Qn. text` headings and the bullet list that follows each one.

    Returns six dicts, in file order, shaped like:

        {"code": "Q1", "position": 1, "text": "...", "grading_guide": "- **1:** ...\n..."}
    """
    # finditer() scans the whole file and returns one match per question heading.
    # Each match exposes .start(), .end(), .group(1) and .group(2).
    matches = list(QUESTION_HEADING.finditer(raw))

    # Accumulates one dict per question; this is what the function returns.
    questions: list[dict[str, str | int]] = []

    # index is needed to find where the next question starts.
    for index, match in enumerate(matches):
        # Index just after the current heading. For Q1 the raw slice starts with
        # "\n- **1:** ..." and ends just before "## Q2...".
        start = match.end()

        # The next heading marks the end of this guide. The last question has no
        # next heading, so it runs to the end of the file.
        end = matches[index + 1].start() if index + 1 < len(matches) else len(raw)

        # Slice out the body and remove surrounding blank lines. The result is
        # one string containing the four guide bullets:
        # "- **1:** ...\n- **2:** ...\n- **3:** ...\n- **4:** ..."
        grading_guide = raw[start:end].strip()

        # group(1) is the captured number string, e.g. "1"; int() makes position 1.
        number = int(match.group(1))

        # A heading with no bullets would leave an empty guide; fail loudly.
        if not grading_guide:
            raise ValueError(f"Q{number} has no grading guide")

        # Append the normalized record for this question.
        questions.append(
            {
                # Stable data-pack id, e.g. "Q1".
                "code": f"Q{number}",
                # Numeric order, e.g. 1.
                "position": number,
                # group(2) is the heading text after "Q1. ", e.g.
                # "Tell us about a piece of work you are proud of. What was your part in it?"
                "text": match.group(2).strip(),
                # Four bullets as one newline-separated string.
                "grading_guide": grading_guide,
            }
        )

    # Example return value:
    # [
    #   {
    #     "code": "Q1",
    #     "position": 1,
    #     "text": "Tell us about a piece of work you are proud of. What was your part in it?",
    #     "grading_guide": "- **1:** ...\n- **2:** ...\n- **3:** ...\n- **4:** ...",
    #   },
    #   {"code": "Q2", "position": 2, ...},
    #   ...
    # ]
    return questions


def load_questions(db: Session, items: list[dict[str, str | int]]) -> None:
    """Upsert questions by their stable data-pack code (Q1..Q6)."""
    # Sort by position so validation and updates happen in Q1..Q6 order.
    items = sorted(items, key=lambda item: int(item["position"]))

    # Require exactly six continuous positions: 1, 2, 3, 4, 5, 6.
    if [int(item["position"]) for item in items] != [1, 2, 3, 4, 5, 6]:
        raise ValueError("Expected exactly Q1..Q6")

    # Validate and upsert one question at a time.
    for item in items:
        # Both the question text and the grading guide must be present.
        if not item["text"] or not item["grading_guide"]:
            raise ValueError(f"{item['code']} has empty text or guide")

        # Every guide must contain all four score levels.
        for level in range(1, 5):
            if f"**{level}:**" not in str(item["grading_guide"]):
                raise ValueError(f"{item['code']} is missing grade {level}")

        # Look up the existing row by stable code (Q1..Q6), not by database id.
        row = db.scalar(select(Question).where(Question.code == item["code"]))

        if row is None:
            # First run: insert a new question row.
            db.add(Question(**item))
        else:
            # Later runs: update in place so ids stay stable and no duplicates appear.
            row.position = int(item["position"])
            row.text = str(item["text"])
            row.grading_guide = str(item["grading_guide"])

    # One transaction for all six questions.
    db.commit()


def main() -> None:
    # Small CLI: optional path argument, default is the data-pack file.
    parser = argparse.ArgumentParser(
        description="Load the six interview questions into the database."
    )
    parser.add_argument(
        "path",
        nargs="?",
        type=Path,
        default=DEFAULT_QUESTIONS_PATH,
        help=f"Path to questions.md (default: {DEFAULT_QUESTIONS_PATH})",
    )
    args = parser.parse_args()

    # Read UTF-8 and turn the markdown into six normalized dicts.
    items = parse_questions_md(args.path.read_text(encoding="utf-8"))

    # Standalone runs should work even before the API has created the tables.
    Base.metadata.create_all(engine)

    # SessionLocal opens the connection; the context manager closes it afterwards.
    with SessionLocal() as db:
        load_questions(db, items)

    # Expected output: "Loaded 6 questions".
    print(f"Loaded {len(items)} questions")


if __name__ == "__main__":
    # Entry point for: python -m app.seed_questions
    main()
