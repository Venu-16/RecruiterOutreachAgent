from app.db import repository
from app.schemas import CandidateProfile
from app.workflows.recruiter_graph import resume_review, run_workflow


def _setup(database, title="AI Engineer Recruiter", opted_out=False):
    repository.initialize(database)
    repository.import_contacts(database, [{
        "email": "demo@example.com", "name": "Demo", "company": "Fictional Co",
        "title": title, "source_row": 2, "consent_status": "unknown", "opt_out": opted_out,
    }])


def test_graph_checkpoints_for_human_review_and_resumes_once(tmp_path):
    database = tmp_path / "outreach.sqlite3"
    _setup(database)
    profile = CandidateProfile(name="Fictional Candidate", verified_facts=["created a sample project"])
    paused = run_workflow(database, "demo-thread", 1, profile)
    assert paused["status"] == "awaiting_human_review"
    assert paused["__interrupt__"]
    draft_id = paused["draft_id"]
    assert repository.get_draft(database, draft_id)["delivery_status"] == "not_sent"
    resumed = resume_review(database, "demo-thread", 1, "rejected", draft_id=draft_id)
    assert resumed["status"] == "rejected"
    assert repository.get_draft(database, draft_id)["review_status"] == "rejected"
    replay = run_workflow(database, "another-thread", 1, profile)
    assert replay["idempotent_replay"] is True
    repository.add_suppression(database, "demo@example.com", "opt-out after draft")
    suppressed_replay = run_workflow(database, "suppressed-replay", 1, profile)
    assert suppressed_replay["status"] == "suppressed"


def test_graph_routes_uncertain_and_opted_out_contacts_away_from_drafts(tmp_path):
    uncertain_db = tmp_path / "uncertain.sqlite3"
    _setup(uncertain_db, title="Talent Acquisition Partner")
    uncertain = run_workflow(uncertain_db, "uncertain", 1, CandidateProfile(verified_facts=["sample"]))
    assert uncertain["status"] == "needs_research"
    assert repository.get_latest_draft_for_contact(uncertain_db, 1) is None

    opted_out_db = tmp_path / "opted-out.sqlite3"
    _setup(opted_out_db, opted_out=True)
    opted_out = run_workflow(opted_out_db, "opted-out", 1, CandidateProfile(verified_facts=["sample"]))
    assert opted_out["status"] == "suppressed"
    assert repository.get_latest_draft_for_contact(opted_out_db, 1) is None