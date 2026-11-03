"""Load the synthetic data pack into the app through the real candidate API.

Run from the backend directory while the API and worker are running:

    .venv/bin/python -m app.seed_data
    .venv/bin/python -m app.seed_data ../data/business-systems-pack --api http://127.0.0.1:8000

Expected terminal output (one line per candidate, then a summary):

    Phuong Bao Hoang (Customer Success Associate): 2/6 answered
    ...
    Submitted 33 new, 0 already there, 0 rejected.
    Skipped/rejected 2 record(s); details in .../seed_errors.json
    Invite links are in .../seed_links.json (never commit it): http://localhost:3000/interview/<token>

A record the API cannot accept (empty text, a missing audio file, a duplicate)
is skipped and written to `seed_errors.json`; the rest of the pack still loads.

Flow, exactly as a real candidate:

1. `pipeline_log.csv` gives candidate_id -> name, role and email.
2. Each `answers/A###.json` is one answer. When it names an `audio_file`, the
   matching `.m4a` is uploaded; otherwise its `transcript` is the typed answer.
3. Each candidate is created once, reusing tokens from `seed_links.json` so a
   re-run never creates a second link.
4. Every answer is POSTed to `/api/v1/interview/{token}/answers`.

The raw invite tokens are written to `seed_links.json` (gitignored); the
database stores only their hashes.
"""

import argparse
import csv
import json
import mimetypes
import sys
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

import httpx
from sqlalchemy import select

from .candidates import create_candidate
from .config import REPO_ROOT
from .db import Base, SessionLocal, engine
from .models import Candidate, Question, Role

# Default input: the synthetic data pack copied into the repo.
DEFAULT_PACK = REPO_ROOT / "data" / "business-systems-pack"

# Reused across runs so re-running never mints a new invite link. Gitignored
# because it holds the raw tokens that the database only keeps as hashes.
LINKS_FILE = REPO_ROOT / "seed_links.json"

# Records answers that could not be submitted, so one bad record never blocks
# the rest of the seed. Gitignored; rewritten on every run.
ERRORS_FILE = REPO_ROOT / "seed_errors.json"


@dataclass
class Entry:
    """One answer to submit: who, which question, and the text or audio payload."""

    answer_id: str              # data-pack answer id, e.g. "A001"
    candidate_id: str           # stable data-pack key, e.g. "C2041"
    name: str                   # display name from pipeline_log.csv
    email: str | None           # from pipeline_log.csv, if present
    role: str                   # role title, e.g. "Backend Engineer"
    position: int               # question number, 1..6
    submitted_at: str           # ISO timestamp, used to pick the latest duplicate
    text: str | None = None     # typed answer (mutually exclusive with audio)
    audio: Path | None = None   # .m4a file to upload instead of text


def load_entries(pack: Path) -> list[Entry]:
    """Read candidate names and answers from the data pack.

    The answer files only carry ``candidate_id`` (e.g. "C2041"), not a name, so
    ``pipeline_log.csv`` is read first to map each id to a name, role and email.
    Every ``answers/*.json`` then becomes one Entry: audio when it names an
    ``audio_file``, otherwise the ``transcript`` field is the typed answer.
    """
    # candidate_id -> (name, role, email). One row per applicant in the export.
    people: dict[str, tuple[str, str, str | None]] = {}
    with (pack / "pipeline_log.csv").open(newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            people[row["candidate_id"]] = (
                row["candidate_name"], row["role"], row.get("candidate_email") or None)

    entries: list[Entry] = []
    # Sorted so two runs produce the same order and the summary is stable.
    for path in sorted((pack / "answers").glob("*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        cid = data["candidate_id"]

        # A name is required to create the candidate; fail loudly if it is missing.
        if cid not in people:
            sys.exit(f"{path.name}: candidate {cid} is missing from pipeline_log.csv")

        name, role, email = people[cid]
        position = int(data["question_id"].lstrip("Q"))   # "Q3" -> 3

        # Five answers are audio only; the rest carry their text in `transcript`.
        audio_name = data.get("audio_file")
        if audio_name:
            entries.append(Entry(
                answer_id=data.get("answer_id", path.stem),
                candidate_id=cid, name=name, email=email, role=role, position=position,
                submitted_at=data.get("submitted_at", ""),
                audio=pack / "answers" / audio_name,
            ))
        else:
            entries.append(Entry(
                answer_id=data.get("answer_id", path.stem),
                candidate_id=cid, name=name, email=email, role=role, position=position,
                submitted_at=data.get("submitted_at", ""),
                text=data["transcript"],
            ))
    return entries


def split_entries(entries: list[Entry]) -> tuple[list[Entry], list[tuple[Entry, str]]]:
    """Separate answers we can submit from ones the API would reject.

    Never exits: a bad record is returned with its reason and skipped, so the
    rest of the pack still loads. For two answers to the same candidate and
    question, the latest ``submitted_at`` wins; the older ones are reported as
    superseded. (Submitting both would leave the older job queued forever,
    because the worker only claims jobs for current answers.)
    """
    problems: list[tuple[Entry, str]] = []
    by_question: dict[tuple[str, int], list[Entry]] = defaultdict(list)

    for e in entries:
        # The first blocking reason wins; an entry is either submitted or skipped.
        reason = None
        if not 1 <= e.position <= 6:
            reason = "question number is out of range"
        elif (e.text is None) == (e.audio is None):
            reason = "needs exactly one of text or audio"
        elif e.text is not None and not e.text.strip():
            reason = "text is empty"
        elif e.audio is not None and not e.audio.is_file():
            reason = f"audio file not found: {e.audio}"

        if reason:
            problems.append((e, reason))
        else:
            by_question[(e.candidate_id, e.position)].append(e)

    # Keep one answer per candidate and question: the latest submission.
    submittable: list[Entry] = []
    for items in by_question.values():
        items.sort(key=lambda e: e.submitted_at)          # ISO strings sort by time
        submittable.append(items[-1])
        for older in items[:-1]:
            problems.append((older, f"duplicate; superseded by {items[-1].answer_id}"))

    return submittable, problems


def ensure_candidates(entries: list[Entry]) -> dict[str, str]:
    """Create each candidate once and return candidate_id -> raw token.

    Tokens are reused from ``seed_links.json``, so re-running is safe: a
    candidate that already has a saved token is skipped instead of duplicated.
    The data-pack id and email are copied onto the row so seeded candidates
    stay traceable back to the export.
    """
    links: dict[str, str] = (
        json.loads(LINKS_FILE.read_text(encoding="utf-8")) if LINKS_FILE.exists() else {})

    with SessionLocal() as db:
        for e in entries:
            # The same candidate appears on several entries; only create once.
            if e.candidate_id in links:
                continue

            # Match by the stable data-pack id, or by role + name so rows created
            # before external_id existed are still recognised.
            exists = db.scalar(
                select(Candidate)
                .join(Role, Role.id == Candidate.role_id)
                .where(
                    ((Role.title == e.role) & (Candidate.name == e.name))
                    | (Candidate.external_id == e.candidate_id)
                )
            )

            # If they are already in the DB but we lost their token, stop: a
            # second row would split their answers across two candidates.
            if exists:
                sys.exit(f"{e.name} ({e.role}) is already in the database but {LINKS_FILE} has no "
                         "token for them. Restore the file, or delete the local database and start fresh.")

            # create_candidate() also creates the role row if it is missing.
            cand, token = create_candidate(db, e.role, e.name)
            # Copy the data-pack identifiers onto the row for traceability.
            cand.external_id = e.candidate_id
            cand.email = e.email
            db.commit()
            links[e.candidate_id] = token
            # Save after every candidate so a crash does not lose earlier tokens.
            LINKS_FILE.write_text(json.dumps(links, indent=2), encoding="utf-8")
    return links


def write_errors(problems: list[tuple[Entry, str]]) -> None:
    """Write skipped/rejected answers to ``seed_errors.json`` (or clear it)."""
    if not problems:
        # No problems this run: drop a stale report from an earlier run.
        ERRORS_FILE.unlink(missing_ok=True)
        return

    report = [
        {
            "answer_id": e.answer_id,
            "candidate_id": e.candidate_id,
            "name": e.name,
            "role": e.role,
            "position": e.position,
            "reason": reason,
        }
        for e, reason in problems
    ]
    ERRORS_FILE.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"Skipped/rejected {len(problems)} record(s); details in {ERRORS_FILE}")


def main() -> None:
    # Small CLI: optional pack path (default is the data-pack directory) and API base URL.
    parser = argparse.ArgumentParser(description="Load the data pack through the real candidate API")
    parser.add_argument("pack", nargs="?", type=Path, default=DEFAULT_PACK,
                        help=f"Path to the data pack (default: {DEFAULT_PACK})")
    parser.add_argument("--api", default="http://127.0.0.1:8000")
    args = parser.parse_args()

    # Read the pack, then split it: bad records are reported instead of aborting.
    entries = load_entries(args.pack)
    submittable, problems = split_entries(entries)
    if problems:
        print(f"Skipping {len(problems)} record(s) the API cannot accept:")
        for e, reason in problems:
            print(f"  - {e.answer_id} {e.name} Q{e.position}: {reason}")

    # Standalone runs should work even before the API has created the tables.
    Base.metadata.create_all(engine)
    with SessionLocal() as db:
        # Position -> database id, because the pack refers to questions as "Q1".."Q6".
        qmap = {q.position: q.id for q in db.scalars(select(Question))}
    if len(qmap) != 6:
        sys.exit("Questions are not loaded. Run: python -m app.seed_questions <path>/questions.md")

    # Fail early with a clear message if the API is not up.
    try:
        httpx.get(f"{args.api}/api/v1/health", timeout=5).raise_for_status()
    except httpx.HTTPError:
        sys.exit(f"The API is not reachable at {args.api}. Start it with: uvicorn app.main:app")

    # Create missing candidates first, then submit their answers.
    links = ensure_candidates(submittable)
    by_candidate = defaultdict(list)
    for e in submittable:
        by_candidate[e.candidate_id].append(e)

    sent = dups = failed = 0
    with httpx.Client(base_url=args.api, timeout=60) as http:
        for cid, items in by_candidate.items():
            token = links[cid]
            # Submit in question order so the printed progress reads naturally.
            for e in sorted(items, key=lambda x: x.position):
                data = {"question_id": str(qmap[e.position])}
                files = None
                if e.audio:
                    # Send the real MIME type; the API rejects unsupported types.
                    ctype = mimetypes.guess_type(e.audio.name)[0] or "application/octet-stream"
                    files = {"audio": (e.audio.name, e.audio.read_bytes(), ctype)}
                else:
                    data["text"] = e.text

                # A failed request is recorded and skipped; the rest still submit.
                try:
                    r = http.post(f"/api/v1/interview/{token}/answers", data=data, files=files)
                except httpx.HTTPError as exc:
                    failed += 1
                    problems.append((e, f"request failed: {exc}"))
                    print(f"  ! {e.answer_id} {e.name} Q{e.position}: request failed: {exc}")
                    continue

                if r.status_code == 200:
                    # The API answers 200 for duplicates too; the flag tells them apart.
                    if r.json()["duplicate"]:
                        dups += 1
                    else:
                        sent += 1
                else:
                    failed += 1
                    try:
                        detail = r.json().get("detail", r.text)
                    except ValueError:
                        detail = r.text
                    problems.append((e, f"API {r.status_code}: {detail}"))
                    print(f"  ! {e.answer_id} {e.name} Q{e.position}: {r.status_code} {detail}")

            # Re-read progress so the line reflects what the API actually accepted.
            try:
                answered = http.get(f"/api/v1/interview/{token}").json()["answered"]
            except (httpx.HTTPError, ValueError, KeyError):
                answered = "?"
            print(f"{items[0].name} ({items[0].role}): {answered}/{len(qmap)} answered")

    print(f"\nSubmitted {sent} new, {dups} already there, {failed} rejected.")
    write_errors(problems)
    print(f"Invite links are in {LINKS_FILE} (never commit it): http://localhost:3000/interview/<token>")


if __name__ == "__main__":
    main()
