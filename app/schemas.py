from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, field_validator


class CandidateProfile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = None
    verified_facts: list[str] = Field(default_factory=list, description="Facts personally verified by the candidate")
    target_roles: list[str] = Field(default_factory=lambda: [
        "AI Engineer", "Machine Learning Engineer", "LLM Engineer", "Python Developer"
    ])

    @field_validator("verified_facts", "target_roles")
    @classmethod
    def strip_nonempty_items(cls, values: list[str]) -> list[str]:
        return [value.strip() for value in values if value and value.strip()]


class Draft(BaseModel):
    model_config = ConfigDict(extra="forbid")

    subject: str = Field(min_length=1, max_length=180)
    body: str = Field(min_length=1, max_length=2500)
    candidate_facts_used: list[str]
    review_status: str = "pending"
    delivery_status: str = "not_sent"


class DraftEdit(BaseModel):
    model_config = ConfigDict(extra="forbid")

    subject: str = Field(min_length=1, max_length=180)
    body: str = Field(min_length=1, max_length=2500)


class ReviewDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision: str = Field(pattern="^(approved|rejected)$")
    note: str | None = Field(default=None, max_length=1000)