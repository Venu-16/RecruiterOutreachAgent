from __future__ import annotations

from typing import Any, Mapping

from app.schemas import CandidateProfile, Draft
from app.services.qualification import Qualification


class DraftGenerationError(ValueError):
    """A safe draft cannot be generated from the supplied evidence."""


def generate_draft(
    contact: Mapping[str, Any],
    qualification: Qualification,
    candidate: CandidateProfile,
    max_length: int = 2500,
) -> Draft:
    if qualification.status != "qualified_for_review" or not qualification.target_roles:
        raise DraftGenerationError("contact does not have sufficient role evidence for a draft")
    if contact.get("opt_out") or contact.get("suppressed"):
        raise DraftGenerationError("opted-out or suppressed contacts cannot receive drafts")
    if not candidate.verified_facts:
        raise DraftGenerationError("candidate profile has no verified facts; configure it before drafting")

    role = qualification.target_roles[0]
    recipient = str(contact.get("name") or "").strip()
    greeting = f"Hello {recipient.split()[0]}," if recipient else "Hello,"
    title = str(contact.get("title") or "").strip()
    first_fact = candidate.verified_facts[0]
    body = (
        f"{greeting}\n\n"
        f"I’m exploring {role} opportunities and wanted to ask whether this area is within your recruiting focus. "
        f"Your listed title, {title}, explicitly references this role area.\n\n"
        f"My background includes {first_fact}. If it is relevant to your work, I’d be glad to share more.\n\n"
        "Best,\n"
        f"{candidate.name or '[Your name]'}"
    )
    if len(body) > max_length:
        raise DraftGenerationError("generated draft exceeds the configured maximum length")
    return Draft(
        subject=f"Interest in {role} opportunities",
        body=body,
        candidate_facts_used=[first_fact],
    )