from __future__ import annotations

import csv
import re
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd


ALIASES = {
    "email": ("email", "email address", "e-mail", "work email", "recruiter email"),
    "name": ("name", "full name", "recruiter name", "contact name"),
    "company": ("company", "company name", "organization", "organization name", "employer", "firm"),
    "title": ("title", "job title", "recruiter title", "current title", "role", "position", "designation"),
    "linkedin_url": ("linkedin", "linkedin url", "profile url", "linkedin profile"),
    "source": ("source", "lead source", "contact source"),
    "opt_out": ("opt out", "opt-out", "opted out", "unsubscribe", "unsubscribed", "do not contact", "do not email", "dnc"),
    "consent": ("consent", "permission to contact", "contact permission"),
}
EMAIL_PATTERN = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
TRUE_VALUES = {"1", "true", "yes", "y", "opted out", "unsubscribed", "do not contact"}
FALSE_VALUES = {"0", "false", "no", "n", "subscribed"}


class IngestionError(ValueError):
    """An input cannot be safely interpreted as a contact dataset."""


@dataclass
class IngestionResult:
    clean: list[dict[str, Any]]
    rejected: list[dict[str, Any]]
    stats: dict[str, int]
    column_mapping: dict[str, str]


def normalize_email(value: Any) -> str | None:
    if value is None:
        return None
    email = str(value).strip().casefold()
    if not email or not EMAIL_PATTERN.fullmatch(email):
        return None
    return email


def _column_key(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(value).casefold()).strip()


def _map_columns(columns: list[Any]) -> dict[str, str]:
    normalized = {_column_key(column): str(column) for column in columns}
    mapping: dict[str, str] = {}
    ambiguous: dict[str, list[str]] = {}
    for field, aliases in ALIASES.items():
        matches = [normalized[_column_key(alias)] for alias in aliases if _column_key(alias) in normalized]
        matches = list(dict.fromkeys(matches))
        if len(matches) > 1:
            ambiguous[field] = matches
        elif matches:
            mapping[field] = matches[0]
    if ambiguous:
        fields = ", ".join(sorted(ambiguous))
        raise IngestionError(f"ambiguous column mappings for: {fields}")
    if "email" not in mapping:
        raise IngestionError("required email column is missing")
    return mapping


def _read_table(path: Path, worksheet: str | int | None) -> pd.DataFrame:
    suffix = path.suffix.casefold()
    try:
        if suffix == ".csv":
            return pd.read_csv(path, dtype=str, keep_default_na=False, skip_blank_lines=False)
        if suffix == ".xlsx":
            return pd.read_excel(path, sheet_name=worksheet or 0, dtype=str).fillna("")
    except (ValueError, OSError, ImportError, csv.Error, pd.errors.EmptyDataError) as exc:
        raise IngestionError("input file could not be read; check format and worksheet") from exc
    raise IngestionError("input must be a .csv or .xlsx file")


def _opted_out(value: Any) -> bool:
    return str(value or "").strip().casefold() in TRUE_VALUES


def _explicit_consent(value: Any) -> bool:
    return str(value or "").strip().casefold() in {"1", "true", "yes", "y", "consented", "permission granted"}


def ingest_file(
    input_path: str | Path,
    worksheet: str | int | None = None,
    suppression_emails: set[str] | None = None,
) -> IngestionResult:
    path = Path(input_path)
    if not path.is_file():
        raise IngestionError("input file does not exist")
    table = _read_table(path, worksheet)
    mapping = _map_columns(list(table.columns)) if len(table.columns) else {}
    if not mapping:
        raise IngestionError("input spreadsheet is empty or has no columns")
    if table.empty:
        raise IngestionError("input spreadsheet contains no contact rows")

    rows: list[dict[str, Any]] = []
    for offset, (_, raw) in enumerate(table.iterrows(), start=2):
        record: dict[str, Any] = {"source_row": offset}
        for field in ALIASES:
            source = mapping.get(field)
            value = raw[source] if source is not None else ""
            value = str(value).strip() if value is not None else ""
            record[field] = value or None
        raw_email = record["email"]
        record["email"] = normalize_email(raw_email)
        record["_email_error"] = "missing_email" if not raw_email else "invalid_email"
        record["opt_out"] = _opted_out(record["opt_out"])
        record["consent_status"] = "explicit_permission" if _explicit_consent(record["consent"]) else "unknown"
        record.pop("consent", None)
        rows.append(record)

    suppressed = {email.casefold().strip() for email in (suppression_emails or set()) if email.strip()}
    by_email: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        if row["email"]:
            by_email[row["email"]].append(row)

    conflicting_rows: set[int] = set()
    for grouped in by_email.values():
        if len(grouped) < 2:
            continue
        identities = {
            tuple((field, grouped_row.get(field)) for field in ("name", "company", "title", "linkedin_url"))
            for grouped_row in grouped
        }
        if len(identities) > 1:
            conflicting_rows.update(row["source_row"] for row in grouped)

    clean: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    seen: set[str] = set()
    counts = {
        "input_rows": len(rows), "accepted": 0, "rejected": 0, "invalid_email": 0,
        "missing_email": 0, "duplicate": 0, "conflicting_duplicate": 0,
        "opted_out": 0, "suppressed": 0,
    }
    for row in rows:
        reason: str | None = None
        if row["source_row"] in conflicting_rows:
            reason = "conflicting_duplicate"
        elif not row["email"]:
            reason = row["_email_error"]
        elif row["email"] in seen:
            reason = "duplicate"
        elif row["opt_out"]:
            reason = "opted_out"
        elif row["email"] in suppressed:
            reason = "suppressed"

        if row["email"]:
            seen.add(row["email"])
        if reason:
            rejected_row = dict(row)
            rejected_row.pop("_email_error", None)
            rejected_row["rejection_reason"] = reason
            rejected.append(rejected_row)
            counts[reason] += 1
        else:
            row.pop("_email_error", None)
            clean.append(row)
            counts["accepted"] += 1
    counts["rejected"] = len(rejected)
    return IngestionResult(clean, rejected, counts, mapping)


def write_outputs(result: IngestionResult, output_dir: str | Path) -> tuple[Path, Path]:
    destination = Path(output_dir)
    destination.mkdir(parents=True, exist_ok=True)
    clean_path = destination / "contacts_clean.csv"
    rejected_path = destination / "contacts_rejected.csv"
    columns = ["source_row", "email", "name", "company", "title", "linkedin_url", "source", "opt_out", "consent_status"]
    pd.DataFrame(result.clean, columns=columns).to_csv(clean_path, index=False)
    pd.DataFrame(result.rejected, columns=[*columns, "rejection_reason"]).to_csv(rejected_path, index=False)
    return clean_path, rejected_path