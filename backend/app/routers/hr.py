import mimetypes
from collections import defaultdict
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from ..config import Settings, get_settings
from ..db import get_db
from ..deps import require_hr
from ..models import (Answer, Candidate, HrOverride, Question, Role, ScoreJob,
                      SuggestedScore, Transcript)
from ..schemas import (HrAnswerOut, HrCandidateOut, HrRoleOut, JobOut, OverrideIn,
                       OverrideOut, SuggestionOut, TranscriptOut)

# Every route in this router needs a signed-in HR user. One line, applied to all of them.
router = APIRouter(prefix="/api/hr", tags=["hr"], dependencies=[Depends(require_hr)])


# JobOut: {"id", "status", "attempts", "error", "needs_person_reason"}
def job_out(job: ScoreJob) -> JobOut:
    status = "queued" if job.status == "running" else job.status     # 'running' is internal
    return JobOut(id=job.id, status=status, attempts=job.attempts,
                  error=job.last_error, needs_person_reason=job.needs_person_reason)


@router.get("/roles", response_model=list[HrRoleOut])
def list_roles(db: Session = Depends(get_db)):
    rows = db.execute(
        select(Role.id, Role.title, func.count(Candidate.id))
        .outerjoin(Candidate, Candidate.role_id == Role.id)
        .group_by(Role.id).order_by(Role.title)).all()
    # HrRoleOut: {"id", "title", "candidate_count"}
    return [HrRoleOut(id=i, title=t, candidate_count=n) for i, t, n in rows]


@router.get("/roles/{role_id}/candidates", response_model=list[HrCandidateOut])
def role_candidates(role_id: int, db: Session = Depends(get_db)):
    if db.get(Role, role_id) is None:
        raise HTTPException(404, "Role not found")
    questions = {q.id: q for q in db.scalars(select(Question))}
    candidates = list(db.scalars(
        select(Candidate).where(Candidate.role_id == role_id).order_by(Candidate.name)))
    cids = [c.id for c in candidates]
    answers = list(db.scalars(select(Answer).where(Answer.candidate_id.in_(cids))))
    aids = [a.id for a in answers]

    # A handful of bulk queries instead of one query per answer.
    transcripts = {t.answer_id: t for t in db.scalars(
        select(Transcript).where(Transcript.answer_id.in_(aids)))}
    jobs = {j.answer_id: j for j in db.scalars(
        select(ScoreJob).where(ScoreJob.answer_id.in_(aids)))}
    suggestions = {s.answer_id: s for s in db.scalars(
        select(SuggestedScore).where(SuggestedScore.answer_id.in_(aids)))}
    # newest first, so ovs[0] is the latest
    overrides = defaultdict(list)
    for o in db.scalars(select(HrOverride).where(HrOverride.answer_id.in_(aids))
                        .order_by(HrOverride.id.desc())):
        overrides[o.answer_id].append(o)

    # answers grouped by candidate id
    by_candidate = defaultdict(list)
    for a in answers:
        by_candidate[a.candidate_id].append(a)

    # one HrCandidateOut per candidate: {"id", "name", "answered", "total_questions", "answers"}
    out = []
    for c in candidates:
        mine = sorted(by_candidate[c.id],
                      key=lambda a: (questions[a.question_id].position, not a.is_current, -a.id))
        # each answer becomes one HrAnswerOut row
        items = []
        for a in mine:
            q, job = questions[a.question_id], jobs[a.id]
            tr, ovs = transcripts.get(a.id), overrides[a.id]
            # Only show a suggestion when the job ended in a state where one exists.
            sug = suggestions.get(a.id) if job.status in ("scored", "needs_person") else None
            # Final = HR's latest override, else the model's score ONLY if the job is cleanly scored.
            final = ovs[0].score if ovs else (sug.score if sug and job.status == "scored" else None)
            # fields: question, kind/text/audio, transcript, job, suggestion, overrides, final_score
            items.append(HrAnswerOut(
                id=a.id, question_id=q.id, question_position=q.position, question_text=q.text,
                kind=a.kind, text=a.text, has_audio=a.kind == "audio",
                transcript=TranscriptOut(text=tr.text, engine=tr.engine) if tr else None,
                counts=a.is_current, submitted_at=a.created_at, job=job_out(job),
                suggestion=(SuggestionOut(score=sug.score, reason=sug.reason,
                                          injection_flag=sug.injection_flag, model=sug.model)
                            if sug else None),
                overrides=[OverrideOut.model_validate(o) for o in ovs], final_score=final))
        # answered = current versions only
        out.append(HrCandidateOut(id=c.id, name=c.name, total_questions=len(questions),
                                  answered=sum(1 for a in mine if a.is_current), answers=items))
    return out


# returns the raw audio bytes (FileResponse), not JSON
@router.get("/answers/{answer_id}/audio")
def get_audio(answer_id: int, db: Session = Depends(get_db),
              settings: Settings = Depends(get_settings)):
    a = db.get(Answer, answer_id)
    if a is None or a.kind != "audio" or not a.audio_path:
        raise HTTPException(404, "Audio not found")
    base = Path(settings.upload_dir).resolve()
    path = (base / a.audio_path).resolve()
    if base not in path.parents or not path.is_file():       # never serve outside the upload dir
        raise HTTPException(404, "Audio not found")
    return FileResponse(path, media_type=mimetypes.guess_type(path.name)[0] or "application/octet-stream")


# body OverrideIn: {"score": 1-4, "reason": "..."}
# returns OverrideOut: {"id", "score", "reason", "hr_user", "created_at"}
@router.post("/answers/{answer_id}/override", response_model=OverrideOut)
def override(answer_id: int, body: OverrideIn, hr_user: str = Depends(require_hr),
             db: Session = Depends(get_db)):
    a = db.get(Answer, answer_id)
    if a is None:
        raise HTTPException(404, "Answer not found")
    if not a.is_current:
        raise HTTPException(409, "This version was superseded by a newer answer. Review the current one.")
    last = db.scalar(select(HrOverride).where(HrOverride.answer_id == answer_id)
                     .order_by(HrOverride.id.desc()))
    if last and last.score == body.score and last.reason == body.reason:
        return last                                          # repeated click: no duplicate row
    ov = HrOverride(answer_id=answer_id, score=body.score, reason=body.reason, hr_user=hr_user)
    db.add(ov)
    db.commit()
    return ov


# returns JobOut: {"id", "status", "attempts", "error", "needs_person_reason"}
@router.post("/jobs/{job_id}/retry", response_model=JobOut)
def retry(job_id: int, db: Session = Depends(get_db)):
    job = db.get(ScoreJob, job_id)
    if job is None:
        raise HTTPException(404, "Job not found")
    if not db.get(Answer, job.answer_id).is_current:
        raise HTTPException(409, "This version was superseded; there is nothing to retry.")
    res = db.execute(update(ScoreJob)
                     .where(ScoreJob.id == job_id, ScoreJob.status.in_(["failed", "needs_person"]))
                     .values(status="queued", attempts=0, last_error=None,
                             needs_person_reason=None, locked_at=None))
    db.commit()
    if res.rowcount != 1:                                    # already queued/running/scored
        raise HTTPException(409, "Only failed or needs-a-person jobs can be retried.")
    db.refresh(job)
    return job_out(job)