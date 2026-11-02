"""Background scoring worker.

The loop recovers stale claims, atomically claims one queued job at a time,
transcribes audio when needed, asks the LLM for a score, and records the
outcome. Transient failures are requeued with exponential backoff. Invalid
model output gets one immediate formatting retry before a human is asked to
review it.
"""

import argparse
import logging
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import delete, func, select, update

from .config import get_settings
from .db import SessionLocal
from .llm import DeepSeekClient
from .models import Answer, Question, ScoreJob, SuggestedScore, Transcript
from .scoring import MAX_ANSWER_CHARS, InvalidOutput, build_messages, parse_output
from .transcribe import MockTranscriber, WhisperTranscriber

# Transient failures (network, 429, 5xx) are retried this many times in total.
MAX_ATTEMPTS = 3
# A job still "running" after this long is treated as abandoned by its worker.
STALE_AFTER = timedelta(minutes=10)
# Appended once when the model's first reply is not valid JSON in the required shape.
INVALID_JSON_RETRY = (
    "Your last reply was not valid JSON in the required shape. "
    "Reply again with only the JSON object."
)
log = logging.getLogger("worker")


def now() -> datetime:
    """Return the current UTC time."""
    return datetime.now(timezone.utc)


def as_utc(dt: datetime) -> datetime:
    """Attach UTC to naive datetimes returned by SQLite."""
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def backoff(attempts: int) -> timedelta:
    """Exponential delay before retrying: 5s, 10s, 20s for attempts 1..3."""
    return timedelta(seconds=5 * 2 ** (attempts - 1))


def recover_stale(session_factory) -> None:
    """Requeue or fail jobs whose worker died while they were 'running'."""
    with session_factory() as db:
        for job in db.scalars(select(ScoreJob).where(ScoreJob.status == "running")):
            if job.locked_at and now() - as_utc(job.locked_at) > STALE_AFTER:
                if job.attempts >= MAX_ATTEMPTS:
                    # No attempts left: fail instead of looping forever.
                    job.status, job.last_error = "failed", "Worker stopped while processing (timed out)"
                else:
                    # Requeue; claim_next_job() applies the normal backoff.
                    job.status, job.last_error = "queued", "Worker stopped while processing; requeued"
                job.locked_at = None
        db.commit()


def pending_count(session_factory) -> int:
    """Count queued/running jobs that belong to current answers."""
    with session_factory() as db:
        return db.scalar(select(func.count()).select_from(ScoreJob)
                         .join(Answer, Answer.id == ScoreJob.answer_id)
                         .where(ScoreJob.status.in_(["queued", "running"]), Answer.is_current.is_(True)))


def claim_next_job(session_factory) -> int | None:
    """Atomically claim the next eligible job, or return None."""
    with session_factory() as db:
        rows = db.execute(
            select(ScoreJob.id, ScoreJob.attempts, ScoreJob.updated_at)
            .join(Answer, Answer.id == ScoreJob.answer_id)
            .where(ScoreJob.status == "queued", Answer.is_current.is_(True))   # skip superseded answers
            .order_by(ScoreJob.id)).all()
        for job_id, attempts, updated_at in rows:
            if attempts > 0 and now() < as_utc(updated_at) + backoff(attempts):
                continue                                                      # still backing off
            # Atomic claim: only one worker can flip queued -> running.
            res = db.execute(update(ScoreJob)
                             .where(ScoreJob.id == job_id, ScoreJob.status == "queued")
                             .values(status="running", attempts=ScoreJob.attempts + 1, locked_at=now()))
            db.commit()
            if res.rowcount == 1:
                return job_id
    return None


def _end(job: ScoreJob, status: str, *, error: str | None = None, reason: str | None = None) -> None:
    """Finish a job and clear the lock so stale recovery ignores it."""
    job.status, job.last_error, job.needs_person_reason, job.locked_at = status, error, reason, None


def _complete_json(llm, system: str, user: str):
    """Ask for JSON, retrying once with a correction if parsing/validation fails.

    Returns ``(raw, result, invalid)``. On success ``invalid`` is None; after a
    second failure ``result`` is None and ``invalid`` holds the validation error.
    """
    raw = llm.complete(system, user)
    try:
        return raw, parse_output(raw), None
    except InvalidOutput:
        # Keep the scoring prompt intact; only add a short formatting correction.
        raw = llm.complete(system, f"{user}\n\n{INVALID_JSON_RETRY}")
        try:
            return raw, parse_output(raw), None
        except InvalidOutput as e:
            return raw, None, e


def process_job(session_factory, job_id: int, llm, transcriber, upload_dir: str) -> None:
    """Process one claimed job: transcribe if needed, score, and save the outcome."""
    with session_factory() as db:
        # claim_next_job() marked this job running, so these rows must exist.
        job = db.get(ScoreJob, job_id)
        answer = db.get(Answer, job.answer_id)
        question = db.get(Question, answer.question_id)

        try:
            # Audio answers are transcribed once; later runs reuse the transcript.
            is_transcript = answer.kind == "audio"
            text = answer.text
            if is_transcript:
                tr = db.scalar(select(Transcript).where(Transcript.answer_id == answer.id))
                if tr is None:
                    spoken = transcriber.transcribe(str(Path(upload_dir) / answer.audio_path))
                    tr = Transcript(answer_id=answer.id, text=spoken, engine=transcriber.name)
                    db.add(tr)
                    db.commit()                    # keep it even if the scoring step fails later
                text = tr.text

            # Scoring needs actual text; empty or oversized answers go to a human.
            if not text or not text.strip():
                _end(job, "needs_person", reason="The answer is empty (or the recording had no speech).")
                db.commit()
                return
            if len(text) > MAX_ANSWER_CHARS:
                _end(job, "needs_person", reason="The answer is too long to score automatically.")
                db.commit()
                return

            # _complete_json() retries once only for invalid model output.
            # API/transcription failures raised here are handled below.
            system, user = build_messages(question, text, is_transcript)
            raw, result, invalid = _complete_json(llm, system, user)
        except Exception as e:
            # Roll back any partial work, then requeue transient failures.
            db.rollback()
            transient = getattr(e, "transient", True)
            msg = str(e) if hasattr(e, "transient") or "Transcription" in type(e).__name__ \
                else f"Unexpected error: {type(e).__name__}"
            log.warning("job %s attempt %s failed: %s", job_id, job.attempts, msg)

            if transient and job.attempts < MAX_ATTEMPTS:
                # claim_next_job() waits out the backoff before retrying.
                job.status, job.last_error, job.locked_at = "queued", msg, None
            else:
                _end(job, "failed", error=msg)
            db.commit()
            return

        # Both model calls succeeded, but the final reply may still be invalid.
        if invalid is not None:
            db.execute(delete(SuggestedScore).where(SuggestedScore.answer_id == answer.id))
            _end(job, "needs_person", error=f"Invalid model output after retry: {raw[:300]}",
                 reason=f"The model's reply was still invalid after one retry ({invalid}).")
            db.commit()
            return

        # A re-run may re-score this answer, so replace any previous suggestion.
        db.execute(delete(SuggestedScore).where(SuggestedScore.answer_id == answer.id))
        db.add(SuggestedScore(answer_id=answer.id, score=result.score, reason=result.reason,
                              injection_flag=result.injection_detected, model=llm.model, raw_output=raw))

        # Injection attempts are never auto-accepted; a human must review them.
        if result.injection_detected:
            _end(job, "needs_person",
                 reason="The answer appears to contain instructions aimed at the scorer. Please read it yourself.")
        else:
            _end(job, "scored")
        db.commit()


def run(once: bool = False, llm=None, transcriber=None, session_factory=SessionLocal, idle: float = 2.0) -> None:
    """Run the worker loop until stopped, or drain the queue when ``once`` is true."""
    s = get_settings()
    # Build the real clients once per process; tests can inject fakes.
    if llm is None:
        if not s.deepseek_api_key:
            raise SystemExit("DEEPSEEK_API_KEY is not set. The worker needs it to score answers.")
        llm = DeepSeekClient(s.deepseek_api_key, s.deepseek_base_url, s.deepseek_model)
    if transcriber is None:
        transcriber = MockTranscriber() if s.transcriber == "mock" else WhisperTranscriber()
    log.info("worker started (model=%s, transcriber=%s)", llm.model, transcriber.name)

    while True:
        # Recover jobs abandoned by a dead worker before claiming new work.
        recover_stale(session_factory)
        job_id = claim_next_job(session_factory)
        if job_id is not None:
            process_job(session_factory, job_id, llm, transcriber, s.upload_dir)
            continue
        # --once exits only when nothing is left queued or running.
        if once and pending_count(session_factory) == 0:
            break
        time.sleep(idle)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true", help="drain the queue, then exit")
    run(once=ap.parse_args().once)