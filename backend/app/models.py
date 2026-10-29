from datetime import datetime, timezone

from sqlalchemy import (
    Boolean, CheckConstraint, ForeignKey, Index, Integer, String, Text,
    UniqueConstraint, text as sql_text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy import DateTime

from .db import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Role(Base):
    __tablename__ = "roles"
    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(200), unique=True)


class Candidate(Base):
    __tablename__ = "candidates"
    id: Mapped[int] = mapped_column(primary_key=True)
    external_id: Mapped[str | None] = mapped_column(String(50), unique=True, nullable=True)  # data pack C2041
    role_id: Mapped[int] = mapped_column(ForeignKey("roles.id"))
    name: Mapped[str] = mapped_column(String(200))
    email: Mapped[str | None] = mapped_column(String(320), nullable=True)  # data pack candidate_email
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)  # sha256 hex, never the raw token
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Question(Base):
    __tablename__ = "questions"
    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(20), unique=True)  # data pack Q1..Q6
    position: Mapped[int] = mapped_column(unique=True)
    text: Mapped[str] = mapped_column(Text)
    grading_guide: Mapped[str] = mapped_column(Text)


class Answer(Base):
    __tablename__ = "answers"
    id: Mapped[int] = mapped_column(primary_key=True)
    external_id: Mapped[str | None] = mapped_column(String(50), unique=True, nullable=True)  # data pack A001
    candidate_id: Mapped[int] = mapped_column(ForeignKey("candidates.id"))
    question_id: Mapped[int] = mapped_column(ForeignKey("questions.id"))
    kind: Mapped[str] = mapped_column(String(10))
    text: Mapped[str | None] = mapped_column(Text, nullable=True)
    audio_path: Mapped[str | None] = mapped_column(String(500), nullable=True)
    duration_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)  # data pack duration_seconds
    content_hash: Mapped[str] = mapped_column(String(64))
    is_current: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    __table_args__ = (
        UniqueConstraint("candidate_id", "question_id", "content_hash", name="uq_answer_content"),
        # Only ONE current answer per candidate+question:
        Index("uq_one_current_answer", "candidate_id", "question_id",
              unique=True, sqlite_where=sql_text("is_current = 1")),
        CheckConstraint("kind IN ('text','audio')", name="ck_answer_kind"),
        CheckConstraint(
            "(kind = 'text' AND text IS NOT NULL) OR (kind = 'audio' AND audio_path IS NOT NULL)",
            name="ck_answer_payload",
        ),
        CheckConstraint(
            "duration_seconds IS NULL OR duration_seconds >= 0",
            name="ck_answer_duration_nonnegative",
        ),
    )


class Transcript(Base):
    __tablename__ = "transcripts"
    id: Mapped[int] = mapped_column(primary_key=True)
    answer_id: Mapped[int] = mapped_column(ForeignKey("answers.id"), unique=True)
    text: Mapped[str] = mapped_column(Text)
    engine: Mapped[str] = mapped_column(String(20))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    __table_args__ = (CheckConstraint("engine IN ('whisper','mock')", name="ck_transcript_engine"),)


class ScoreJob(Base):
    __tablename__ = "score_jobs"
    id: Mapped[int] = mapped_column(primary_key=True)
    answer_id: Mapped[int] = mapped_column(ForeignKey("answers.id"), unique=True)
    status: Mapped[str] = mapped_column(String(20), default="queued")
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    needs_person_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    locked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
    __table_args__ = (
        CheckConstraint("status IN ('queued','running','scored','failed','needs_person')", name="ck_job_status"),
    )


class SuggestedScore(Base):
    __tablename__ = "suggested_scores"
    id: Mapped[int] = mapped_column(primary_key=True)
    answer_id: Mapped[int] = mapped_column(ForeignKey("answers.id"), unique=True)
    score: Mapped[int] = mapped_column(Integer)
    reason: Mapped[str] = mapped_column(Text)
    injection_flag: Mapped[bool] = mapped_column(Boolean, default=False)
    model: Mapped[str] = mapped_column(String(100))
    raw_output: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    __table_args__ = (CheckConstraint("score BETWEEN 1 AND 4", name="ck_suggested_score_range"),)


class HrOverride(Base):
    __tablename__ = "hr_overrides"
    id: Mapped[int] = mapped_column(primary_key=True)
    answer_id: Mapped[int] = mapped_column(ForeignKey("answers.id"))   # NOT unique: append-only history
    score: Mapped[int] = mapped_column(Integer)
    reason: Mapped[str] = mapped_column(Text)
    hr_user: Mapped[str] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    __table_args__ = (
        CheckConstraint("score BETWEEN 1 AND 4", name="ck_override_score_range"),
        CheckConstraint("length(trim(reason)) > 0", name="ck_override_reason_nonempty"),
    )