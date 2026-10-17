from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping


class ContactConflictError(ValueError):
    """An existing contact differs from a newly imported record."""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def connect(database_path: str | Path) -> sqlite3.Connection:
    path = Path(database_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path, timeout=30)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def initialize(database_path: str | Path) -> None:
    with connect(database_path) as connection:
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS contacts (
                id INTEGER PRIMARY KEY,
                email TEXT NOT NULL COLLATE NOCASE UNIQUE,
                name TEXT,
                company TEXT,
                title TEXT,
                linkedin_url TEXT,
                source TEXT,
                source_row INTEGER NOT NULL,
                consent_status TEXT NOT NULL DEFAULT 'unknown',
                opt_out INTEGER NOT NULL DEFAULT 0 CHECK (opt_out IN (0, 1)),
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS qualifications (
                contact_id INTEGER PRIMARY KEY REFERENCES contacts(id) ON DELETE CASCADE,
                status TEXT NOT NULL,
                score INTEGER NOT NULL CHECK (score BETWEEN 0 AND 100),
                completeness_score INTEGER NOT NULL,
                evidence_json TEXT NOT NULL,
                result_json TEXT NOT NULL,
                evaluated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS drafts (
                id INTEGER PRIMARY KEY,
                contact_id INTEGER NOT NULL REFERENCES contacts(id) ON DELETE CASCADE,
                version INTEGER NOT NULL,
                subject TEXT NOT NULL,
                body TEXT NOT NULL,
                candidate_facts_json TEXT NOT NULL,
                review_status TEXT NOT NULL DEFAULT 'pending'
                    CHECK (review_status IN ('pending', 'approved', 'rejected', 'superseded')),
                delivery_status TEXT NOT NULL DEFAULT 'not_sent'
                    CHECK (delivery_status = 'not_sent'),
                created_at TEXT NOT NULL,
                UNIQUE(contact_id, version)
            );
            CREATE TABLE IF NOT EXISTS approval_decisions (
                id INTEGER PRIMARY KEY,
                draft_id INTEGER NOT NULL REFERENCES drafts(id) ON DELETE CASCADE,
                decision TEXT NOT NULL CHECK (decision IN ('approved', 'rejected')),
                note TEXT,
                decided_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS suppression (
                email TEXT PRIMARY KEY COLLATE NOCASE,
                reason TEXT,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS workflow_executions (
                thread_id TEXT PRIMARY KEY,
                contact_id INTEGER NOT NULL REFERENCES contacts(id) ON DELETE CASCADE,
                status TEXT NOT NULL,
                error_code TEXT,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS email_delivery_attempts (
                id INTEGER PRIMARY KEY,
                draft_id INTEGER NOT NULL REFERENCES drafts(id) ON DELETE CASCADE,
                status TEXT NOT NULL,
                attempted_at TEXT NOT NULL,
                provider_reference TEXT,
                UNIQUE(draft_id)
            );
            """
        )


def import_contacts(database_path: str | Path, contacts: Iterable[Mapping[str, Any]]) -> dict[str, int]:
    counts = {"inserted": 0, "unchanged": 0, "conflicts": 0}
    fields = ("name", "company", "title", "linkedin_url", "source", "source_row", "consent_status", "opt_out")
    with connect(database_path) as connection:
        for contact in contacts:
            email = str(contact["email"]).casefold().strip()
            values = [contact.get(field) for field in fields]
            existing = connection.execute("SELECT * FROM contacts WHERE email = ?", (email,)).fetchone()
            if existing:
                expected = dict(zip(fields, values))
                if all(existing[field] == expected[field] for field in fields):
                    counts["unchanged"] += 1
                else:
                    counts["conflicts"] += 1
                continue
            connection.execute(
                """INSERT INTO contacts
                (email, name, company, title, linkedin_url, source, source_row, consent_status, opt_out, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (email, *values, utc_now()),
            )
            counts["inserted"] += 1
    return counts


def get_contact(database_path: str | Path, contact_id: int) -> dict[str, Any] | None:
    with connect(database_path) as connection:
        row = connection.execute("SELECT * FROM contacts WHERE id = ?", (contact_id,)).fetchone()
    return dict(row) if row else None


def get_contact_by_email(database_path: str | Path, email: str) -> dict[str, Any] | None:
    with connect(database_path) as connection:
        row = connection.execute("SELECT * FROM contacts WHERE email = ?", (email.casefold().strip(),)).fetchone()
    return dict(row) if row else None


def get_contact_detail(database_path: str | Path, contact_id: int) -> dict[str, Any] | None:
    with connect(database_path) as connection:
        row = connection.execute(
            """SELECT c.*, q.result_json FROM contacts c LEFT JOIN qualifications q ON q.contact_id=c.id
            WHERE c.id=?""", (contact_id,)
        ).fetchone()
    if not row:
        return None
    result = dict(row)
    result["qualification"] = json.loads(result.pop("result_json")) if result.get("result_json") else None
    return result


def save_qualification(database_path: str | Path, contact_id: int, result: Mapping[str, Any]) -> None:
    with connect(database_path) as connection:
        connection.execute(
            """INSERT INTO qualifications (contact_id, status, score, completeness_score, evidence_json, result_json, evaluated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(contact_id) DO UPDATE SET status=excluded.status, score=excluded.score,
            completeness_score=excluded.completeness_score, evidence_json=excluded.evidence_json,
            result_json=excluded.result_json, evaluated_at=excluded.evaluated_at""",
            (contact_id, result["status"], result["score"], result["completeness_score"],
             json.dumps(result["evidence"]), json.dumps(dict(result)), utc_now()),
        )


def create_draft(database_path: str | Path, contact_id: int, draft: Mapping[str, Any]) -> dict[str, Any]:
    with connect(database_path) as connection:
        if connection.execute("SELECT 1 FROM contacts WHERE id = ?", (contact_id,)).fetchone() is None:
            raise ValueError("contact not found")
        if connection.execute(
            "SELECT 1 FROM contacts c LEFT JOIN suppression s ON s.email=c.email WHERE c.id=? AND (c.opt_out=1 OR s.email IS NOT NULL)",
            (contact_id,),
        ).fetchone():
            raise ValueError("suppressed contacts cannot have drafts")
        version = connection.execute(
            "SELECT COALESCE(MAX(version), 0) + 1 FROM drafts WHERE contact_id = ?", (contact_id,)
        ).fetchone()[0]
        connection.execute("UPDATE drafts SET review_status='superseded' WHERE contact_id=? AND review_status!='superseded'", (contact_id,))
        cursor = connection.execute(
            """INSERT INTO drafts (contact_id, version, subject, body, candidate_facts_json, created_at)
            VALUES (?, ?, ?, ?, ?, ?)""",
            (contact_id, version, draft["subject"], draft["body"], json.dumps(draft["candidate_facts_used"]), utc_now()),
        )
        draft_id = cursor.lastrowid
    return get_draft(database_path, draft_id)


def get_draft(database_path: str | Path, draft_id: int) -> dict[str, Any] | None:
    with connect(database_path) as connection:
        row = connection.execute(
            """SELECT d.*, c.email, c.name, c.company, c.title FROM drafts d
            JOIN contacts c ON c.id=d.contact_id WHERE d.id=?""", (draft_id,)
        ).fetchone()
    if not row:
        return None
    result = dict(row)
    result["candidate_facts_used"] = json.loads(result.pop("candidate_facts_json"))
    return result


def get_latest_draft_for_contact(database_path: str | Path, contact_id: int) -> dict[str, Any] | None:
    with connect(database_path) as connection:
        row = connection.execute(
            "SELECT id FROM drafts WHERE contact_id=? ORDER BY version DESC LIMIT 1", (contact_id,)
        ).fetchone()
    return get_draft(database_path, row["id"]) if row else None


def edit_draft(database_path: str | Path, draft_id: int, subject: str, body: str) -> dict[str, Any]:
    draft = get_draft(database_path, draft_id)
    if draft is None:
        raise ValueError("draft not found")
    if draft["review_status"] != "pending":
        raise ValueError("only pending drafts can be edited")
    return create_draft(database_path, draft["contact_id"], {
        "subject": subject, "body": body, "candidate_facts_used": draft["candidate_facts_used"]
    })


def review_draft(database_path: str | Path, draft_id: int, decision: str, note: str | None = None) -> dict[str, Any]:
    if decision not in {"approved", "rejected"}:
        raise ValueError("decision must be approved or rejected")
    with connect(database_path) as connection:
        cursor = connection.execute(
            """UPDATE drafts SET review_status=? WHERE id=? AND review_status='pending' AND delivery_status='not_sent'
            AND contact_id IN (SELECT c.id FROM contacts c LEFT JOIN suppression s ON s.email=c.email
            WHERE c.opt_out=0 AND s.email IS NULL)""",
            (decision, draft_id),
        )
        if cursor.rowcount != 1:
            raise ValueError("draft not found or has already been reviewed")
        connection.execute(
            "INSERT INTO approval_decisions (draft_id, decision, note, decided_at) VALUES (?, ?, ?, ?)",
            (draft_id, decision, note, utc_now()),
        )
    return get_draft(database_path, draft_id)


def add_suppression(database_path: str | Path, email: str, reason: str | None = None) -> None:
    normalized = email.casefold().strip()
    with connect(database_path) as connection:
        connection.execute(
            "INSERT INTO suppression(email, reason, created_at) VALUES (?, ?, ?) ON CONFLICT(email) DO NOTHING",
            (normalized, reason, utc_now()),
        )
        connection.execute("UPDATE contacts SET opt_out=1 WHERE email=?", (normalized,))


def list_suppressions(database_path: str | Path) -> list[dict[str, Any]]:
    with connect(database_path) as connection:
        rows = connection.execute("SELECT email, reason, created_at FROM suppression ORDER BY created_at DESC").fetchall()
    return [dict(row) for row in rows]


def get_suppressed_emails(database_path: str | Path) -> set[str]:
    return {entry["email"] for entry in list_suppressions(database_path)}


def is_suppressed(database_path: str | Path, email: str) -> bool:
    with connect(database_path) as connection:
        return connection.execute("SELECT 1 FROM suppression WHERE email=?", (email.casefold().strip(),)).fetchone() is not None


def list_contacts(database_path: str | Path, page: int = 1, page_size: int = 50, query: str | None = None,
                  status: str | None = None, score_min: int | None = None,
                  score_max: int | None = None) -> tuple[list[dict[str, Any]], int]:
    clauses: list[str] = []
    params: list[Any] = []
    if query:
        clauses.append("(c.company LIKE ? OR c.title LIKE ? OR c.name LIKE ?)")
        params.extend([f"%{query}%"] * 3)
    if status:
        clauses.append("q.status = ?")
        params.append(status)
    if score_min is not None:
        clauses.append("q.score >= ?")
        params.append(score_min)
    if score_max is not None:
        clauses.append("q.score <= ?")
        params.append(score_max)
    where = " WHERE " + " AND ".join(clauses) if clauses else ""
    with connect(database_path) as connection:
        total = connection.execute(f"SELECT COUNT(*) FROM contacts c LEFT JOIN qualifications q ON q.contact_id=c.id{where}", params).fetchone()[0]
        rows = connection.execute(
            f"""SELECT c.id, c.email, c.name, c.company, c.title, c.source_row, c.consent_status, c.opt_out,
            q.status AS qualification_status, q.score, q.result_json FROM contacts c
            LEFT JOIN qualifications q ON q.contact_id=c.id{where}
            ORDER BY c.id LIMIT ? OFFSET ?""",
            [*params, page_size, (page - 1) * page_size],
        ).fetchall()
    results = [dict(row) for row in rows]
    for item in results:
        item["qualification"] = json.loads(item.pop("result_json")) if item.get("result_json") else None
    return results, total


def list_drafts(database_path: str | Path, review_status: str | None = None) -> list[dict[str, Any]]:
    with connect(database_path) as connection:
        if review_status:
            rows = connection.execute("SELECT id FROM drafts WHERE review_status=? ORDER BY id DESC", (review_status,)).fetchall()
        else:
            rows = connection.execute("SELECT id FROM drafts ORDER BY id DESC").fetchall()
    return [get_draft(database_path, row["id"]) for row in rows]


def record_workflow(database_path: str | Path, thread_id: str, contact_id: int, status: str,
                    error_code: str | None = None) -> None:
    with connect(database_path) as connection:
        connection.execute(
            """INSERT INTO workflow_executions(thread_id, contact_id, status, error_code, updated_at)
            VALUES (?, ?, ?, ?, ?) ON CONFLICT(thread_id) DO UPDATE SET status=excluded.status,
            error_code=excluded.error_code, updated_at=excluded.updated_at""",
            (thread_id, contact_id, status, error_code, utc_now()),
        )


def statistics(database_path: str | Path) -> dict[str, Any]:
    with connect(database_path) as connection:
        contacts = connection.execute("SELECT COUNT(*) FROM contacts").fetchone()[0]
        categories = {row[0]: row[1] for row in connection.execute("SELECT status, COUNT(*) FROM qualifications GROUP BY status")}
        drafts = {row[0]: row[1] for row in connection.execute("SELECT review_status, COUNT(*) FROM drafts GROUP BY review_status")}
        rejected = connection.execute("SELECT COUNT(*) FROM approval_decisions WHERE decision='rejected'").fetchone()[0]
        suppressed = connection.execute("SELECT COUNT(*) FROM suppression").fetchone()[0]
    return {"contacts": contacts, "qualifications": categories, "drafts": drafts,
            "rejected_decisions": rejected, "suppressed": suppressed, "sending_enabled": False}


def list_contact_ids(database_path: str | Path) -> list[int]:
    with connect(database_path) as connection:
        rows = connection.execute("SELECT id FROM contacts ORDER BY id").fetchall()
    return [row["id"] for row in rows]


def audit_history(database_path: str | Path, limit: int = 100) -> list[dict[str, Any]]:
    with connect(database_path) as connection:
        rows = connection.execute(
            """SELECT 'approval' AS event_type, a.decision AS status, a.note AS detail,
            a.decided_at AS occurred_at, d.contact_id FROM approval_decisions a
            JOIN drafts d ON d.id=a.draft_id
            UNION ALL
            SELECT 'workflow' AS event_type, w.status, w.error_code AS detail,
            w.updated_at AS occurred_at, w.contact_id FROM workflow_executions w
            ORDER BY occurred_at DESC LIMIT ?""", (limit,)
        ).fetchall()
    return [dict(row) for row in rows]


def export_contact(database_path: str | Path, contact_id: int) -> dict[str, Any] | None:
    with connect(database_path) as connection:
        contact = connection.execute("SELECT * FROM contacts WHERE id=?", (contact_id,)).fetchone()
        if not contact:
            return None
        qualifications = connection.execute("SELECT * FROM qualifications WHERE contact_id=?", (contact_id,)).fetchone()
        drafts = connection.execute("SELECT * FROM drafts WHERE contact_id=? ORDER BY version", (contact_id,)).fetchall()
        approvals = connection.execute("SELECT a.* FROM approval_decisions a JOIN drafts d ON d.id=a.draft_id WHERE d.contact_id=?", (contact_id,)).fetchall()
    return {"contact": dict(contact), "qualification": dict(qualifications) if qualifications else None,
            "drafts": [dict(row) for row in drafts], "decisions": [dict(row) for row in approvals]}


def delete_contact(database_path: str | Path, contact_id: int) -> bool:
    with connect(database_path) as connection:
        cursor = connection.execute("DELETE FROM contacts WHERE id=?", (contact_id,))
        if cursor.rowcount == 1:
            thread_id = f"contact-{contact_id}"
            for table in ("checkpoints", "writes"):
                exists = connection.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
                ).fetchone()
                if exists:
                    connection.execute(f"DELETE FROM {table} WHERE thread_id=?", (thread_id,))
    return cursor.rowcount == 1