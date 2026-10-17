from __future__ import annotations

import argparse
import json
from pathlib import Path

from pydantic import ValidationError

from app import config
from app.db import repository
from app.schemas import CandidateProfile
from app.services.ingestion import IngestionError, ingest_file, write_outputs
from app.services.qualification import qualify_contact
from app.workflows.recruiter_graph import run_workflow


def _profile(path: Path) -> CandidateProfile:
    return CandidateProfile.model_validate(json.loads(path.read_text(encoding="utf-8")))


def main() -> int:
    parser = argparse.ArgumentParser(description="Local recruiter qualification and draft review tools")
    subparsers = parser.add_subparsers(dest="command", required=True)
    import_parser = subparsers.add_parser("import", help="Validate and import a CSV or XLSX file")
    import_parser.add_argument("--file", required=True, type=Path)
    import_parser.add_argument("--worksheet")
    subparsers.add_parser("qualify", help="Recompute explainable qualification for stored contacts")
    draft_parser = subparsers.add_parser("draft", help="Generate a single review-only draft")
    draft_parser.add_argument("--contact-id", required=True, type=int)
    draft_parser.add_argument("--profile", type=Path, default=config.LOCAL_CANDIDATE_PROFILE)
    parser.add_argument("--database", type=Path, default=config.DATABASE_PATH)
    args = parser.parse_args()
    repository.initialize(args.database)

    try:
        if args.command == "import":
            result = ingest_file(args.file, args.worksheet, repository.get_suppressed_emails(args.database))
            write_outputs(result, config.DATA_OUTPUT_DIR)
            persistence = repository.import_contacts(args.database, result.clean)
            for row in result.clean:
                contact = repository.get_contact_by_email(args.database, row["email"])
                if contact:
                    repository.save_qualification(
                        args.database, contact["id"],
                        qualify_contact(row, config.TARGET_ROLES, config.QUALIFIED_THRESHOLD).to_dict(),
                    )
            print(json.dumps({"processing": result.stats, "persistence": persistence}))
        elif args.command == "qualify":
            ids = repository.list_contact_ids(args.database)
            for contact_id in ids:
                contact = repository.get_contact(args.database, contact_id)
                repository.save_qualification(
                    args.database, contact_id,
                    qualify_contact(contact, config.TARGET_ROLES, config.QUALIFIED_THRESHOLD).to_dict(),
                )
            print(json.dumps({"evaluated": len(ids)}))
        else:
            profile = _profile(args.profile)
            result = run_workflow(args.database, f"contact-{args.contact_id}", args.contact_id, profile)
            print(json.dumps({"status": result.get("status", "awaiting_human_review"),
                              "draft_id": result.get("draft_id"), "review_required": bool(result.get("__interrupt__")),
                              "sending_enabled": False}))
    except (IngestionError, OSError, json.JSONDecodeError, ValidationError, ValueError) as exc:
        print(f"operation failed: {type(exc).__name__}")
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())