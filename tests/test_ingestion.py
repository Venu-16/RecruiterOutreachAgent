import pandas as pd

from app.services.ingestion import IngestionError, ingest_file, write_outputs


def _csv(tmp_path, text):
    path = tmp_path / "contacts.csv"
    path.write_text(text, encoding="utf-8")
    return path


def test_rejects_malformed_and_missing_email(tmp_path):
    result = ingest_file(_csv(tmp_path, "Email,Name\nbad-address,A\n,B\n"))
    assert [row["rejection_reason"] for row in result.rejected] == ["invalid_email", "missing_email"]


def test_identical_duplicate_is_rejected_case_insensitively(tmp_path):
    result = ingest_file(_csv(tmp_path, "Email,Name,Company\nA@Example.com,Alex,North\na@example.com,Alex,North\n"))
    assert len(result.clean) == 1
    assert result.rejected[0]["rejection_reason"] == "duplicate"
    assert result.clean[0]["email"] == "a@example.com"


def test_conflicting_duplicate_rejects_entire_group(tmp_path):
    result = ingest_file(_csv(tmp_path, "Email,Name,Company\na@example.com,Alex,North\na@example.com,Sam,South\n"))
    assert result.clean == []
    assert result.stats["conflicting_duplicate"] == 2


def test_missing_or_ambiguous_email_columns_fail_clearly(tmp_path):
    with pytest.raises(IngestionError, match="required email"):
        ingest_file(_csv(tmp_path, "Name,Company\nAlex,North\n"))
    with pytest.raises(IngestionError, match="ambiguous"):
        ingest_file(_csv(tmp_path, "Email,Email Address\na@example.com,a@example.com\n"))


def test_empty_spreadsheet_fails_clearly(tmp_path):
    path = tmp_path / "empty.xlsx"
    pd.DataFrame().to_excel(path, index=False)
    with pytest.raises(IngestionError, match="empty"):
        ingest_file(path)
    with pytest.raises(IngestionError, match="no contact rows"):
        ingest_file(_csv(tmp_path, "Email,Name\n"))


def test_empty_clean_and_rejected_outputs_keep_headers(tmp_path):
    result = ingest_file(_csv(tmp_path, "Email,Name\nbad-email,Alex\n"))
    clean_path, rejected_path = write_outputs(result, tmp_path / "output")
    assert clean_path.read_text(encoding="utf-8").startswith("source_row,email,name")
    assert rejected_path.read_text(encoding="utf-8").startswith("source_row,email,name")


def test_xlsx_ingestion_preserves_original_and_source_row(tmp_path):
    path = tmp_path / "contacts.xlsx"
    pd.DataFrame([{"Email": "Alex@Example.com", "Company": "Example"}]).to_excel(path, index=False)
    original = path.read_bytes()
    result = ingest_file(path)
    assert len(result.clean) == 1
    assert result.clean[0]["email"] == "alex@example.com"
    assert result.clean[0]["source_row"] == 2
    assert path.read_bytes() == original


def test_opt_out_and_suppression_are_rejected_and_unknown_consent_is_not_permission(tmp_path):
    path = _csv(tmp_path, "Email,Name,Opt Out,Consent\na@example.com,A,yes,yes\nb@example.com,B,no,\nc@example.com,C,no,yes\n")
    result = ingest_file(path, suppression_emails={"c@example.com"})
    assert [row["rejection_reason"] for row in result.rejected] == ["opted_out", "suppressed"]
    assert result.clean[0]["consent_status"] == "unknown"


import pytest