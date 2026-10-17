import pytest

from app.db import repository


def _contact(email="alex@example.com", **changes):
    result = {
        "email": email,
        "name": "Alex",
        "company": "Example",
        "title": "AI Engineer Recruiter",
        "linkedin_url": None,
        "source": "test",
        "source_row": 2,
        "consent_status": "unknown",
        "opt_out": False,
    }
    result.update(changes)
    return result


def test_contact_import_is_idempotent_and_does_not_overwrite_conflicts(tmp_path):
    database = tmp_path / "outreach.sqlite3"
    repository.initialize(database)
    assert repository.import_contacts(database, [_contact()]) == {"inserted": 1, "unchanged": 0, "conflicts": 0}
    assert repository.import_contacts(database, [_contact()]) == {"inserted": 0, "unchanged": 1, "conflicts": 0}
    assert repository.import_contacts(database, [_contact(company="Different")]) == {
        "inserted": 0, "unchanged": 0, "conflicts": 1
    }
    assert repository.get_contact(database, 1)["company"] == "Example"


def test_draft_edits_create_a_new_pending_version(tmp_path):
    database = tmp_path / "outreach.sqlite3"
    repository.initialize(database)
    repository.import_contacts(database, [_contact()])
    first = repository.create_draft(database, 1, {
        "subject": "First", "body": "Draft", "candidate_facts_used": ["verified"]
    })
    second = repository.edit_draft(database, first["id"], "Edited", "Updated draft")
    assert first["version"] == 1
    assert second["version"] == 2
    assert repository.get_draft(database, first["id"])["review_status"] == "superseded"
    assert second["review_status"] == "pending"


def test_suppression_prevents_new_drafts_and_approval(tmp_path):
    database = tmp_path / "outreach.sqlite3"
    repository.initialize(database)
    repository.import_contacts(database, [_contact()])
    draft = repository.create_draft(database, 1, {
        "subject": "First", "body": "Draft", "candidate_facts_used": ["verified"]
    })
    repository.add_suppression(database, "alex@example.com", "requested")
    with pytest.raises(ValueError, match="suppressed"):
        repository.create_draft(database, 1, {"subject": "Second", "body": "Draft", "candidate_facts_used": []})
    with pytest.raises(ValueError, match="already been reviewed"):
        repository.review_draft(database, draft["id"], "approved")


def test_contact_deletion_clears_contact_and_checkpoint_records(tmp_path):
    database = tmp_path / "outreach.sqlite3"
    repository.initialize(database)
    repository.import_contacts(database, [_contact()])
    with repository.connect(database) as connection:
        connection.execute("CREATE TABLE checkpoints(thread_id TEXT, checkpoint_id TEXT)")
        connection.execute("INSERT INTO checkpoints VALUES ('contact-1', 'private-state')")
        connection.execute("CREATE TABLE writes(thread_id TEXT, checkpoint_id TEXT)")
        connection.execute("INSERT INTO writes VALUES ('contact-1', 'private-write')")
    assert repository.delete_contact(database, 1)
    assert repository.get_contact(database, 1) is None
    with repository.connect(database) as connection:
        assert connection.execute("SELECT COUNT(*) FROM checkpoints").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM writes").fetchone()[0] == 0