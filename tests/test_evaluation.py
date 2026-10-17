from app.services.evaluation import draft_fact_coverage, draft_policy_check, qualification_metrics


def test_metrics_are_computed_from_manual_labels():
    metrics = qualification_metrics([
        {"contact": {"title": "AI Engineer Recruiter"}, "recruiter_relevant": True},
        {"contact": {"title": "Technical Recruiter"}, "recruiter_relevant": False},
    ])
    assert metrics["sample_size"] == 2
    assert metrics["true_positive"] == 1
    assert metrics["false_positive"] == 0
    assert metrics["precision"] == 1.0
    assert metrics["recall"] == 1.0


def test_draft_fact_coverage_finds_unverified_fact_ids():
    result = draft_fact_coverage({"candidate_facts_used": ["known", "unknown"]}, ["known"])
    assert result["fact_consistency_pass"] is False
    assert result["unsupported_facts"] == ["unknown"]


def test_draft_policy_requires_verified_facts_reviewable_contact_and_no_suppression():
    draft = {"body": "Listed title, AI Engineer Recruiter. My background includes known.", "candidate_facts_used": ["known"]}
    profile = {"verified_facts": ["known"]}
    contact = {"opt_out": False, "title": "AI Engineer Recruiter"}
    qualification = {"status": "qualified_for_review"}
    assert draft_policy_check(draft, profile, contact, qualification)["passed"]
    blocked = draft_policy_check(draft, profile, contact, qualification, suppressed=True)
    assert blocked["issues"] == ["contact_suppressed"]
    no_title = draft_policy_check(draft, profile, {"title": "ML Recruiter"}, qualification)
    assert "recruiter_title_evidence_missing" in no_title["issues"]