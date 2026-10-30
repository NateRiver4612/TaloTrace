from typing import Literal

from pydantic import BaseModel


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