from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.services.ingestion import normalize_email


class HealthResponse(BaseModel):
    status: str
    sending_enabled: bool = False


class ContactPage(BaseModel):
    items: list[dict]
    total: int
    page: int
    page_size: int


class ImportSummary(BaseModel):
    input_rows: int
    accepted: int
    rejected: int
    duplicate: int
    conflicting_duplicate: int
    invalid_email: int
    missing_email: int
    opted_out: int
    suppressed: int
    inserted: int
    unchanged: int
    database_conflicts: int


class DraftRecord(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: int
    contact_id: int
    version: int
    subject: str
    body: str
    candidate_facts_used: list[str]
    review_status: str
    delivery_status: str = "not_sent"


class SuppressionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: str = Field(min_length=3, max_length=320)
    reason: str | None = Field(default=None, max_length=500)

    @field_validator("email")
    @classmethod
    def validate_email(cls, value: str) -> str:
        normalized = normalize_email(value)
        if not normalized:
            raise ValueError("email address has invalid syntax")
        return normalized