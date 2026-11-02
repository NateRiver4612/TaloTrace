from typing import Literal
from pydantic import BaseModel
from datetime import datetime
from pydantic import ConfigDict, Field, StrictInt, field_validator

class QuestionOut(BaseModel):
    id: int
    position: int
    text: str                      # note: NO grading_guide field exists here

class InterviewState(BaseModel):
    candidate_name: str
    total_questions: int
    answered: int
    complete: bool
    current_question: QuestionOut | None


class SubmitResult(BaseModel):
    status: Literal["received"]
    answer_id: int
    duplicate: bool
    answered: int
    total_questions: int
    complete: bool

class SuggestionOut(BaseModel):
    score: int
    reason: str
    injection_flag: bool
    model: str                      # raw_output is deliberately NOT exposed

class OverrideOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    score: int
    reason: str
    hr_user: str
    created_at: datetime

class TranscriptOut(BaseModel):
    text: str
    engine: str

class JobOut(BaseModel):
    id: int
    status: Literal["queued", "scored", "failed", "needs_person"]
    attempts: int
    error: str | None
    needs_person_reason: str | None

class HrAnswerOut(BaseModel):
    id: int
    question_id: int
    question_position: int
    question_text: str
    kind: Literal["text", "audio"]
    text: str | None
    has_audio: bool
    transcript: TranscriptOut | None
    counts: bool                    # True = this is the version that counts
    submitted_at: datetime
    job: JobOut
    suggestion: SuggestionOut | None
    overrides: list[OverrideOut]    # newest first
    final_score: int | None

class HrCandidateOut(BaseModel):
    id: int
    name: str
    answered: int
    total_questions: int
    answers: list[HrAnswerOut]

class HrRoleOut(BaseModel):
    id: int
    title: str
    candidate_count: int

class OverrideIn(BaseModel):
    score: StrictInt = Field(ge=1, le=4)
    reason: str = Field(max_length=2000)

    @field_validator("reason")
    @classmethod
    def reason_required(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("A reason is required")
        return v