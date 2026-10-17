import pytest

from app.schemas import CandidateProfile
from app.services.draft_generation import DraftGenerationError, generate_draft
from app.services.qualification import qualify_contact


def test_draft_uses_only_verified_candidate_facts_and_is_review_only():
    contact = {"name": None, "title": "AI Engineer Recruiter", "company": "Example"}
    profile = CandidateProfile(name="Candidate", verified_facts=["built a local document classifier"])
    draft = generate_draft(contact, qualify_contact(contact), profile)
    assert draft.body.startswith("Hello,")
    assert "built a local document classifier" in draft.body
    assert draft.candidate_facts_used == ["built a local document classifier"]
    assert draft.review_status == "pending"
    assert draft.delivery_status == "not_sent"


def test_draft_refuses_unqualified_or_suppressed_contacts():
    profile = CandidateProfile(verified_facts=["verified fact"])
    with pytest.raises(DraftGenerationError, match="sufficient role evidence"):
        generate_draft({"title": "Engineer"}, qualify_contact({"title": "Engineer"}), profile)
    contact = {"title": "AI Engineer Recruiter", "suppressed": True}
    with pytest.raises(DraftGenerationError, match="suppressed"):
        generate_draft(contact, qualify_contact(contact), profile)


def test_draft_refuses_missing_profile_facts_and_enforces_length():
    contact = {"title": "AI Engineer Recruiter"}
    qualification = qualify_contact(contact)
    with pytest.raises(DraftGenerationError, match="no verified facts"):
        generate_draft(contact, qualification, CandidateProfile())
    profile = CandidateProfile(verified_facts=["x" * 100])
    with pytest.raises(DraftGenerationError, match="maximum length"):
        generate_draft(contact, qualification, profile, max_length=20)