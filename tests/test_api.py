import json

from fastapi.testclient import TestClient

from app import main
from app.db import repository


def _configure(tmp_path, monkeypatch):
    database = tmp_path / "outreach.sqlite3"
    monkeypatch.setattr(main, "DATABASE_PATH", database)
    monkeypatch.setattr(main.config, "LOCAL_CANDIDATE_PROFILE", tmp_path / "candidate.json")
    monkeypatch.setattr(main.config, "DATA_OUTPUT_DIR", tmp_path / "output")
    main.config.LOCAL_CANDIDATE_PROFILE.write_text(json.dumps({
        "name": "Fictional Candidate",
        "verified_facts": ["completed a fictional sample project"],
        "target_roles": ["AI Engineer"],
    }), encoding="utf-8")
    repository.initialize(database)
    return database


def test_api_upload_list_create_and_approve_are_review_only(tmp_path, monkeypatch):
    _configure(tmp_path, monkeypatch)
    with TestClient(main.app) as client:
        assert client.get("/health").json()["sending_enabled"] is False
        uploaded = client.post("/imports", files={
            "file": ("fictional.csv", "Email,Name,Company,Job Title\nalex@example.com,Alex,Example,AI Engineer Recruiter\n", "text/csv")
        })
        assert uploaded.status_code == 200, uploaded.text
        assert uploaded.json()["inserted"] == 1
        listed = client.get("/contacts", params={"page": 1, "page_size": 1, "score_min": 70})
        assert listed.status_code == 200
        assert listed.json()["total"] == 1
        detail = client.get("/contacts/1").json()
        assert detail["qualification"]["status"] == "qualified_for_review"
        created = client.post("/contacts/1/drafts")
        assert created.status_code == 200, created.text
        draft = created.json()
        assert draft["delivery_status"] == "not_sent"
        decision = client.post(f"/drafts/{draft['id']}/review", json={"decision": "approved"})
        assert decision.status_code == 200, decision.text
        assert decision.json()["review_status"] == "approved"
        assert decision.json()["delivery_status"] == "not_sent"
        assert client.get("/stats").json()["sending_enabled"] is False


def test_api_validation_and_suppression_block_review(tmp_path, monkeypatch):
    database = _configure(tmp_path, monkeypatch)
    with TestClient(main.app) as client:
        invalid = client.post("/imports", files={"file": ("wrong.txt", b"secret", "text/plain")})
        assert invalid.status_code == 400
        uploaded = client.post("/imports", files={
            "file": ("fictional.csv", "Email,Job Title\nalex@example.com,AI Engineer Recruiter\n", "text/csv")
        })
        assert uploaded.status_code == 200
        draft = client.post("/contacts/1/drafts").json()
        assert client.post("/suppressions", json={"email": "ALEX@example.com", "reason": "opt-out"}).status_code == 201
        blocked = client.post(f"/drafts/{draft['id']}/review", json={"decision": "approved"})
        assert blocked.status_code == 409
        assert repository.get_draft(database, draft["id"])["review_status"] == "pending"


def test_contact_list_pagination_and_score_range_validation(tmp_path, monkeypatch):
    _configure(tmp_path, monkeypatch)
    with TestClient(main.app) as client:
        response = client.get("/contacts", params={"score_min": 80, "score_max": 20})
        assert response.status_code == 422
        invalid_page = client.get("/contacts", params={"page": 0})
        assert invalid_page.status_code == 422


def test_api_requires_bearer_token_when_configured(tmp_path, monkeypatch):
    _configure(tmp_path, monkeypatch)
    monkeypatch.setattr(main.config, "API_TOKEN", "local-test-token")
    with TestClient(main.app) as client:
        assert client.get("/health").status_code == 401
        response = client.get("/health", headers={"Authorization": "Bearer local-test-token"})
        assert response.status_code == 200