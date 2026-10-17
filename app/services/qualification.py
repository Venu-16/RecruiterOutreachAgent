from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Mapping


DEFAULT_TARGET_ROLES = (
    "AI Engineer", "Machine Learning Engineer", "LLM Engineer", "Python Developer"
)
RECRUITER_TERMS = ("recruiter", "talent acquisition", "talent partner", "technical sourcing")


@dataclass(frozen=True)
class Qualification:
    status: str
    score: int
    completeness_score: int
    recruiter_relevance_score: int
    target_role_relevance_score: int
    target_roles: list[str]
    evidence: list[str]
    explanation: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def qualify_contact(
    contact: Mapping[str, Any],
    target_roles: tuple[str, ...] = DEFAULT_TARGET_ROLES,
    qualified_threshold: int = 70,
) -> Qualification:
    title = str(contact.get("title") or "").casefold()
    complete_fields = sum(bool(str(contact.get(field) or "").strip()) for field in ("email", "name", "company", "title"))
    completeness = complete_fields * 25

    recruiter_hits = [term for term in RECRUITER_TERMS if term in title]
    recruiter_score = 60 if recruiter_hits else 0
    role_hits: list[str] = []
    for role in target_roles:
        if all(part.casefold() in title for part in role.split()):
            role_hits.append(role)
    role_score = min(40, 40 * len(role_hits))
    score = min(100, recruiter_score + role_score)
    evidence = []
    if recruiter_hits:
        evidence.append(f"Contact title contains recruiter evidence: {', '.join(recruiter_hits)}")
    if role_hits:
        evidence.append(f"Contact title explicitly references target role(s): {', '.join(role_hits)}")

    if recruiter_hits and role_hits and score >= qualified_threshold:
        status = "qualified_for_review"
        explanation = "Title evidence suggests recruiting for a target role; human verification is still required."
    elif recruiter_hits:
        status = "needs_research"
        explanation = "Recruiting-related title found, but target-role relevance is not explicit in available evidence."
    else:
        status = "insufficient_evidence"
        explanation = "No recruiter responsibility is evidenced by the contact title; company alone is not sufficient."
    return Qualification(status, score, completeness, recruiter_score, role_score, role_hits, evidence, explanation)