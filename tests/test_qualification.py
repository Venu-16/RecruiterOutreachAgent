from app.services.qualification import qualify_contact


def test_technology_company_does_not_establish_recruiter_relevance():
    result = qualify_contact({"title": "Software Engineer", "company": "AI Systems"})
    assert result.status == "insufficient_evidence"
    assert result.score == 0


def test_recruiter_without_target_role_needs_research():
    result = qualify_contact({"title": "Technical Recruiter", "company": "Example"})
    assert result.status == "needs_research"
    assert result.recruiter_relevance_score > 0
    assert result.target_role_relevance_score == 0


def test_explicit_target_recruiter_is_qualified_only_for_review():
    result = qualify_contact({"title": "AI Engineer Recruiter", "company": "Example"})
    assert result.status == "qualified_for_review"
    assert result.target_roles == ["AI Engineer"]
    assert "human verification" in result.explanation