"""Candidate-facing interview endpoints.

Routes (mounted by ``app.main``):

    GET  /api/v1/interview/{token}
    POST /api/v1/interview/{token}/answers

The ``token`` is the raw invite token from the candidate's link. Only its
SHA-256 hash is stored, so a leaked database does not expose usable links.
Candidate responses never include a score, grading guide, reason or job status.

Read the current state:

    curl http://127.0.0.1:8000/api/v1/interview/<token>

    {
      "candidate_name": "Alice",
      "total_questions": 6,
      "answered": 0,
      "complete": false,
      "current_question": {
        "id": 1,
        "position": 1,
        "text": "Tell us about a piece of work you are proud of. What was your part in it?"
      }
    }

Submit a typed answer:

    curl -X POST http://127.0.0.1:8000/api/v1/interview/<token>/answers \
      -F question_id=1 \
      -F "text=I led the payments retry fix."

    {
      "status": "received",
      "answer_id": 1,
      "duplicate": false,
      "answered": 1,
      "total_questions": 6,
      "complete": false
    }

Submit an audio answer:

    curl -X POST http://127.0.0.1:8000/api/v1/interview/<token>/answers \
      -F question_id=2 \
      -F "audio=@answer.m4a"

Submitting the same content again returns the same ``answer_id`` with
``"duplicate": true`` and does not create another score job.

Once every question has a current answer:

    {
      "candidate_name": "Alice",
      "total_questions": 6,
      "answered": 6,
      "complete": true,
      "current_question": null
    }

Error responses use FastAPI's ``{"detail": "..."}`` shape:

    404 - unknown or invalid token
    413 - text too long, or audio file too large
    415 - unsupported audio file type
    422 - unknown question, or not exactly one of text/audio
"""

from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..answers import (ALLOWED_AUDIO_EXT, EXTRA_AUDIO_TYPES, MAX_AUDIO_BYTES,
                       MAX_TEXT_CHARS, get_progress, submit_answer)
from ..config import Settings, get_settings
from ..db import get_db
from ..models import Candidate, Question
from ..schemas import InterviewState, QuestionOut, SubmitResult
from ..security import hash_token


router = APIRouter(prefix="/api/v1/interview", tags=["candidate"])


def get_candidate(token: str, db: Session = Depends(get_db)) -> Candidate:
    """Resolve the invite token to a candidate.

    Only the hash is compared, so the raw token never has to be stored. Every
    bad token gets the same 404, which avoids telling a caller whether a token
    belongs to someone else.
    """
    cand = db.scalar(select(Candidate).where(Candidate.token_hash == hash_token(token)))
    if cand is None:
        raise HTTPException(404, "Interview not found")
    return cand


def build_state(db: Session, candidate: Candidate) -> InterviewState:
    """Turn the candidate's progress into the public interview state.

    ``get_progress`` returns all questions, the ids that already have a current
    answer, and the first unanswered question. The response deliberately has
    no grading guide, score, reason or job status.
    """
    questions, answered_ids, current = get_progress(db, candidate.id)
    return InterviewState(
        candidate_name=candidate.name,
        total_questions=len(questions),
        answered=len(answered_ids),
        complete=current is None,
        current_question=(QuestionOut(id=current.id, position=current.position, text=current.text)
                          if current else None),
    )


@router.get("/{token}", response_model=InterviewState)
def get_interview(candidate: Candidate = Depends(get_candidate),
                  db: Session = Depends(get_db)):
    """Return the candidate's progress and current question.

    ``db`` is the same session that ``get_candidate`` used because FastAPI
    caches dependency results within one request.
    """
    return build_state(db, candidate)


@router.post("/{token}/answers", response_model=SubmitResult)
def post_answer(
    question_id: int = Form(...),
    text: str | None = Form(None),
    audio: UploadFile | None = File(None),
    candidate: Candidate = Depends(get_candidate),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    """Accept one typed or audio answer and return the updated progress.

    Questions may be answered in any order. Re-submitting unchanged content is
    a duplicate: ``submit_answer`` returns the existing row and creates no
    another score job.
    """
    question = db.get(Question, question_id)
    if question is None:
        raise HTTPException(422, "Unknown question")

    # The form must carry exactly one payload: text or audio.
    has_text = text is not None and text.strip() != ""
    has_audio = audio is not None and bool(audio.filename)
    if has_text == has_audio:
        raise HTTPException(422, "Send either a typed answer or an audio file, not both or neither.")

    if has_text:
        if len(text) > MAX_TEXT_CHARS:
            raise HTTPException(413, f"Answer is too long (max {MAX_TEXT_CHARS} characters).")
        answer, duplicate = submit_answer(db, candidate, question, text=text,
                                          upload_dir=settings.upload_dir)
    else:
        # The client filename is only used to check the extension. The stored
        # file gets a server-generated name inside submit_answer.
        filename = audio.filename or ""
        ext = Path(filename).suffix.lower()
        ctype = (audio.content_type or "").lower()
        if ext not in ALLOWED_AUDIO_EXT or not (ctype.startswith("audio/") or ctype in EXTRA_AUDIO_TYPES):
            raise HTTPException(415, "Unsupported audio file type.")

        # Read one byte past the limit so an oversized file is rejected without
        # loading an unbounded amount into memory.
        data = audio.file.read(MAX_AUDIO_BYTES + 1)
        if len(data) > MAX_AUDIO_BYTES:
            raise HTTPException(413, "Audio file is too large.")
        if not data:
            raise HTTPException(422, "Audio file is empty.")
        answer, duplicate = submit_answer(db, candidate, question, audio=data, audio_ext=ext,
                                          upload_dir=settings.upload_dir)

    # Re-read progress after the write so the response reflects the new state.
    questions, answered_ids, current = get_progress(db, candidate.id)
    return SubmitResult(status="received", answer_id=answer.id, duplicate=duplicate,
                        answered=len(answered_ids), total_questions=len(questions),
                        complete=current is None)
