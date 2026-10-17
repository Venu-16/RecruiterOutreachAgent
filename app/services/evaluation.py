from __future__ import annotations

from typing import Any, Iterable, Mapping

from app.services.qualification import qualify_contact


def qualification_metrics(labeled_contacts: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    true_positive = false_positive = false_negative = true_negative = 0
    for item in labeled_contacts:
        expected = bool(item["recruiter_relevant"])
        predicted = qualify_contact(item["contact"]).status == "qualified_for_review"
        if expected and predicted:
            true_positive += 1
        elif not expected and predicted:
            false_positive += 1
        elif expected and not predicted:
            false_negative += 1
        else:
            true_negative += 1
    precision_denominator = true_positive + false_positive
    recall_denominator = true_positive + false_negative
    return {
        "sample_size": true_positive + false_positive + false_negative + true_negative,
        "true_positive": true_positive,
        "false_positive": false_positive,
        "false_negative": false_negative,
        "true_negative": true_negative,
        "precision": true_positive / precision_denominator if precision_denominator else None,
        "recall": true_positive / recall_denominator if recall_denominator else None,
        "limitations": "Metrics describe only the supplied manually labeled sample, not production performance.",
    }


def draft_fact_coverage(draft: Mapping[str, Any], verified_facts: Iterable[str]) -> dict[str, Any]:
    verified = set(verified_facts)
    used = set(draft.get("candidate_facts_used", []))
    unsupported = sorted(used - verified)
    return {"fact_consistency_pass": not unsupported, "unsupported_facts": unsupported,
            "facts_checked": len(used)}


def draft_policy_check(draft: Mapping[str, Any], profile: Mapping[str, Any],
                       contact: Mapping[str, Any], qualification: Mapping[str, Any],
                       suppressed: bool = False) -> dict[str, Any]:
    fact_check = draft_fact_coverage(draft, profile.get("verified_facts", []))
    issues = []
    if not fact_check["fact_consistency_pass"]:
        issues.append("candidate_fact_not_verified")
    if any(fact not in str(draft.get("body", "")) for fact in draft.get("candidate_facts_used", [])):
        issues.append("candidate_fact_missing_from_body")
    title = str(contact.get("title") or "").strip()
    if title and title.casefold() not in str(draft.get("body", "")).casefold():
        issues.append("recruiter_title_evidence_missing")
    if contact.get("opt_out") or suppressed:
        issues.append("contact_suppressed")
    if qualification.get("status") != "qualified_for_review":
        issues.append("qualification_not_reviewable")
    return {"passed": not issues, "issues": issues, "checks": fact_check}