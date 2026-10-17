from __future__ import annotations

import json
import tempfile
from contextlib import asynccontextmanager
import hmac
from pathlib import Path

from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, Query, UploadFile
from pydantic import ValidationError

from app import config
from app.api.models import ContactPage, DraftRecord, HealthResponse, ImportSummary, SuppressionRequest
from app.db import repository
from app.schemas import CandidateProfile, DraftEdit, ReviewDecision
from app.services.ingestion import IngestionError, ingest_file, write_outputs
from app.services.evaluation import draft_policy_check
from app.services.qualification import qualify_contact
from app.workflows.recruiter_graph import resume_review, run_workflow

DATABASE_PATH = config.DATABASE_PATH
MAX_UPLOAD_BYTES = 25 * 1024 * 1024


def require_api_token(authorization: str | None = Header(default=None)) -> None:
    expected = config.API_TOKEN
    if not expected:
        return
    scheme, _, token = (authorization or "").partition(" ")
    if scheme.casefold() != "bearer" or not hmac.compare_digest(token, expected):
        raise HTTPException(status_code=401, detail="Valid bearer token required")


@asynccontextmanager
async def lifespan(_: FastAPI):
    repository.initialize(DATABASE_PATH)
    yield


app = FastAPI(title="Recruiter Outreach Local API", version="0.1.0", lifespan=lifespan,
              dependencies=[Depends(require_api_token)])


@app.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    return HealthResponse(status="ok", sending_enabled=False)


@app.post("/imports", response_model=ImportSummary)
async def import_dataset(file: UploadFile = File(...), worksheet: str | None = Form(default=None)) -> ImportSummary:
    suffix = Path(file.filename or "").suffix.casefold()
    if suffix not in {".csv", ".xlsx"}:
        raise HTTPException(status_code=400, detail="Upload a .csv or .xlsx file")
    payload = await file.read(MAX_UPLOAD_BYTES + 1)
    if len(payload) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="Upload exceeds the 25 MB limit")
    config.DATA_INPUT_DIR.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(suffix=suffix, dir=config.DATA_INPUT_DIR, delete=False) as temporary:
            temporary.write(payload)
            temporary_path = Path(temporary.name)
        result = ingest_file(temporary_path, worksheet, repository.get_suppressed_emails(DATABASE_PATH))
    except IngestionError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    finally:
        if temporary_path:
            temporary_path.unlink(missing_ok=True)

    write_outputs(result, config.DATA_OUTPUT_DIR)
    persistence = repository.import_contacts(DATABASE_PATH, result.clean)
    for row in result.clean:
        contact = repository.get_contact_by_email(DATABASE_PATH, row["email"])
        if contact:
                    repository.save_qualification(
                        DATABASE_PATH, contact["id"],
                        qualify_contact(row, config.TARGET_ROLES, config.QUALIFIED_THRESHOLD).to_dict(),
                    )
    counts = result.stats
    return ImportSummary(
        **counts, inserted=persistence["inserted"], unchanged=persistence["unchanged"],
        database_conflicts=persistence["conflicts"],
    )


@app.get("/contacts", response_model=ContactPage)
def contacts(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
    query: str | None = None,
    status: str | None = Query(default=None, pattern="^(qualified_for_review|needs_research|insufficient_evidence)$"),
    score_min: int | None = Query(default=None, ge=0, le=100),
    score_max: int | None = Query(default=None, ge=0, le=100),
) -> ContactPage:
    if score_min is not None and score_max is not None and score_min > score_max:
        raise HTTPException(status_code=422, detail="score_min cannot exceed score_max")
    items, total = repository.list_contacts(DATABASE_PATH, page, page_size, query, status, score_min, score_max)
    return ContactPage(items=items, total=total, page=page, page_size=page_size)


@app.get("/contacts/{contact_id}")
def contact_detail(contact_id: int) -> dict:
    contact = repository.get_contact_detail(DATABASE_PATH, contact_id)
    if not contact:
        raise HTTPException(status_code=404, detail="Contact not found")
    return contact


def _load_candidate_profile() -> CandidateProfile:
    path = config.LOCAL_CANDIDATE_PROFILE
    if not path.is_file():
        raise HTTPException(status_code=409, detail="Configure data/input/candidate_profile.json before drafting")
    try:
        return CandidateProfile.model_validate(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, json.JSONDecodeError, ValidationError) as exc:
        raise HTTPException(status_code=422, detail="Candidate profile is invalid; check its local JSON structure") from exc


@app.post("/contacts/{contact_id}/drafts", response_model=DraftRecord)
def create_contact_draft(contact_id: int) -> DraftRecord:
    contact = repository.get_contact(DATABASE_PATH, contact_id)
    if not contact:
        raise HTTPException(status_code=404, detail="Contact not found")
    candidate = _load_candidate_profile()
    result = run_workflow(DATABASE_PATH, f"contact-{contact_id}", contact_id, candidate)
    if result.get("status") in {"insufficient_evidence", "needs_research", "suppressed", "draft_blocked"}:
        raise HTTPException(status_code=409, detail=f"Draft not created: {result['status']}")
    if not result.get("draft_id"):
        raise HTTPException(status_code=409, detail="Workflow did not produce a draft")
    return repository.get_draft(DATABASE_PATH, result["draft_id"])


@app.get("/drafts", response_model=list[DraftRecord])
def drafts(review_status: str | None = Query(default=None, pattern="^(pending|approved|rejected)$")) -> list[dict]:
    return repository.list_drafts(DATABASE_PATH, review_status)


@app.get("/drafts/{draft_id}", response_model=DraftRecord)
def draft_detail(draft_id: int) -> dict:
    draft = repository.get_draft(DATABASE_PATH, draft_id)
    if not draft:
        raise HTTPException(status_code=404, detail="Draft not found")
    return draft


@app.put("/drafts/{draft_id}", response_model=DraftRecord)
def edit_draft(draft_id: int, body: DraftEdit) -> dict:
    try:
        return repository.edit_draft(DATABASE_PATH, draft_id, body.subject, body.body)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.post("/drafts/{draft_id}/review", response_model=DraftRecord)
def decide_draft(draft_id: int, body: ReviewDecision) -> dict:
    draft = repository.get_draft(DATABASE_PATH, draft_id)
    if not draft:
        raise HTTPException(status_code=404, detail="Draft not found")
    if body.decision == "approved":
        candidate = _load_candidate_profile()
        contact = repository.get_contact_detail(DATABASE_PATH, draft["contact_id"])
        qualification = contact.get("qualification") if contact else None
        policy = draft_policy_check(
            draft, candidate.model_dump(), contact or {}, qualification or {},
            repository.is_suppressed(DATABASE_PATH, draft["email"]),
        )
        if not policy["passed"]:
            raise HTTPException(status_code=409, detail="Draft failed safety checks and cannot be approved")
    try:
        resume_review(DATABASE_PATH, f"contact-{draft['contact_id']}", draft["contact_id"],
                      body.decision, body.note, draft_id=draft_id)
    except (ValueError, KeyError) as exc:
        raise HTTPException(status_code=409, detail="Workflow review could not be recorded; draft may already be reviewed") from exc
    return repository.get_draft(DATABASE_PATH, draft_id)


@app.post("/suppressions", status_code=201)
def add_suppression(body: SuppressionRequest) -> dict[str, bool]:
    repository.add_suppression(DATABASE_PATH, body.email, body.reason)
    return {"suppressed": True}


@app.get("/suppressions")
def suppressions() -> list[dict]:
    return repository.list_suppressions(DATABASE_PATH)


@app.get("/stats")
def stats() -> dict:
    return repository.statistics(DATABASE_PATH)


@app.get("/audit")
def audit(limit: int = Query(default=100, ge=1, le=500)) -> list[dict]:
    return repository.audit_history(DATABASE_PATH, limit)


@app.get("/contacts/{contact_id}/export")
def export_contact(contact_id: int) -> dict:
    result = repository.export_contact(DATABASE_PATH, contact_id)
    if result is None:
        raise HTTPException(status_code=404, detail="Contact not found")
    return result


@app.delete("/contacts/{contact_id}", status_code=204)
def delete_contact(contact_id: int) -> None:
    if not repository.delete_contact(DATABASE_PATH, contact_id):
        raise HTTPException(status_code=404, detail="Contact not found")