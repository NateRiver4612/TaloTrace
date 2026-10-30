"""Answer submission and progress helpers.

This module owns the write path for a candidate answer:

1. Normalise typed text, or hash the raw audio bytes.
2. Look for an existing answer with the same candidate, question and hash.
3. If found, return it as a duplicate, reactivating it if it was an old edit.
4. Otherwise, in one transaction, mark older answers non-current, insert the
   new answer, and queue a score job.
5. Retry up to three times when a concurrent request wins the unique
   constraint or SQLite is briefly locked.

Duplicates never create another answer row or score job. An edit creates a new
row; the old row stays for history but is no longer current.

Examples:

    normalise_text("  a  b\\r\\n")      -> "a b"
    sha256_hex(b"abc")                -> "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"

    # New answer: one row, one queued job, old current row retired.
    submit_answer(db, candidate, question, text="hello", upload_dir="...")
    -> (<Answer id=1 kind="text" is_current=True>, False)

    # Same normalised text: the existing row is returned, no new job.
    submit_answer(db, candidate, question, text="  hello  ", upload_dir="...")
    -> (<Answer id=1 kind="text" is_current=True>, True)

    # Audio is hashed as bytes and stored under a server-generated name.
    submit_answer(db, candidate, question, audio=b"...", audio_ext=".m4a",
                  upload_dir="...")
    -> (<Answer id=2 kind="audio" is_current=True>, False)

The boolean is the ``duplicate`` value the router puts in ``SubmitResult``.
"""

import hashlib
import re
import unicodedata
import uuid
from pathlib import Path

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session

from .models import Answer, Candidate, Question, ScoreJob

# Limits live here so the router and the storage helper share the same values.
MAX_TEXT_CHARS = 10_000
MAX_AUDIO_BYTES = 25 * 1024 * 1024
# The data pack uses .m4a; other common browser formats are accepted too.
ALLOWED_AUDIO_EXT = {".mp3", ".wav", ".m4a", ".ogg", ".webm", ".flac"}
EXTRA_AUDIO_TYPES = {"video/webm", "application/octet-stream"}


def normalise_text(raw: str) -> str:
    """Normalise text before hashing so whitespace-only edits are duplicates.

    Example: ``"  a  b\\r\\n"`` becomes ``"a b"``.
    """
    s = unicodedata.normalize("NFC", raw).replace("\r\n", "\n").strip()
    return re.sub(r"[ \t]+", " ", s)          # collapse spaces/tabs, not newlines


def sha256_hex(data: bytes) -> str:
    """Return the hex SHA-256 digest used for duplicate detection."""
    return hashlib.sha256(data).hexdigest()


def get_progress(db: Session, candidate_id: int):
    """Return ``(questions, answered_question_ids, first_unanswered_question)``.

    ``answered_question_ids`` only counts current answers. ``first_unanswered``
    is the lowest-position question without one, or ``None`` when complete.
    """
    questions = list(db.scalars(select(Question).order_by(Question.position)))
    answered_ids = set(db.scalars(
        select(Answer.question_id).where(
            Answer.candidate_id == candidate_id, Answer.is_current.is_(True))
    ))
    current = next((q for q in questions if q.id not in answered_ids), None)
    return questions, answered_ids, current


def store_audio(upload_dir: str, candidate_id: int, data: bytes, ext: str) -> str:
    """Store audio under a server-generated name and return its relative path.

    Example return: ``"3/4f2c...e1.m4a"``. The client filename is never used.
    """
    rel = f"{candidate_id}/{uuid.uuid4().hex}{ext}"
    path = Path(upload_dir) / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return rel


def submit_answer(db: Session, candidate: Candidate, question: Question, *,
                  text: str | None = None, audio: bytes | None = None,
                  audio_ext: str | None = None, upload_dir: str) -> tuple[Answer, bool]:
    """Create or find the current answer for one candidate/question.

    Exactly one of ``text`` or ``audio`` must be given. Text is normalised
    before hashing; audio is hashed as raw bytes. Returns ``(answer, duplicate)``:

    * new content -> inserted answer, ``False``, and a queued score job
    * same content -> existing answer, ``True``, and no new score job

    Re-submitting content that was previously edited away makes that old row
    current again. Concurrent double submits are safe because of the unique
    constraint on ``(candidate, question, content_hash)`` and the partial
    unique index on the current answer: the loser rolls back and its retry
    finds the winner's row.
    """
    if (text is None) == (audio is None):
        raise ValueError("Pass exactly one of text or audio")

    # Capture ids now: db.rollback() expires ORM objects, so the retry must not
    # depend on lazy-loading them again.
    cid, qid = candidate.id, question.id

    saved_rel, created = None, False

    if text is not None:
        # Typed answers are normalised so whitespace-only edits hash the same.
        kind, stored_text = "text", normalise_text(text)
        content_hash = sha256_hex(stored_text.encode("utf-8"))
    else:
        # Audio answers hash the raw bytes. Transcription happens later in the
        # worker, so the transcript is not part of the hash.
        kind, stored_text = "audio", None
        content_hash = sha256_hex(audio)

    try:
        # A unique-constraint race or a short SQLite lock is expected under
        # concurrent double submit, so try the whole write up to three times.
        for attempt in range(3):
            try:
                existing = db.scalar(select(Answer).where(
                    Answer.candidate_id == cid, Answer.question_id == qid,
                    Answer.content_hash == content_hash))

                if existing is not None:
                    # Same content already exists. If it was edited away, make
                    # it current again without another row or job.
                    if not existing.is_current:
                        db.execute(update(Answer).where(
                            Answer.candidate_id == cid, Answer.question_id == qid,
                            Answer.id != existing.id).values(is_current=False))
                        existing.is_current = True
                        db.commit()
                    return existing, True

                # Write the audio file once, before the transaction. If the
                # transaction later fails, the finally block removes it.
                if kind == "audio" and saved_rel is None:
                    if audio_ext is None:
                        raise ValueError("audio_ext is required for audio answers")
                    saved_rel = store_audio(upload_dir, cid, audio, audio_ext)

                # One transaction: retire the old current answer, insert the
                # new answer, and queue its scoring job.
                db.execute(update(Answer).where(
                    Answer.candidate_id == cid, Answer.question_id == qid,
                    Answer.is_current.is_(True)).values(is_current=False))
                new = Answer(candidate_id=cid, question_id=qid, kind=kind, text=stored_text,
                             audio_path=saved_rel, content_hash=content_hash, is_current=True)
                db.add(new)
                db.flush()  # assigns new.id before the job row references it
                db.add(ScoreJob(answer_id=new.id, status="queued"))
                db.commit()
                created = True
                return new, False
            except (IntegrityError, OperationalError):
                # Lost a race (unique constraint) or hit a SQLite lock. Roll
                # back and retry; the next attempt should find the other row
                # and return it as a duplicate.
                db.rollback()
                if attempt == 2:
                    raise
    finally:
        # If we stored an audio file but did not create the answer that owns
        # it, remove the orphan file.
        if saved_rel and not created:
            (Path(upload_dir) / saved_rel).unlink(missing_ok=True)
