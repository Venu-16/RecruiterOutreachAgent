from __future__ import annotations

import os
from pathlib import Path

import pandas as pd
import requests
import streamlit as st
from dotenv import load_dotenv


load_dotenv(Path(__file__).resolve().parents[1] / ".env")
API_URL = os.getenv("OUTREACH_API_URL", "http://127.0.0.1:8000").rstrip("/")
API_TOKEN = os.getenv("OUTREACH_API_TOKEN", "")
REQUEST_TIMEOUT = 8

st.set_page_config(page_title="Recruiter Outreach Review", page_icon="R", layout="wide")
st.title("Recruiter Outreach Review")
st.caption("Local review workspace · Drafts only · Email sending is disabled")


def request(method: str, path: str, **kwargs):
    try:
        headers = kwargs.pop("headers", {})
        if API_TOKEN:
            headers["Authorization"] = f"Bearer {API_TOKEN}"
        response = requests.request(method, f"{API_URL}{path}", timeout=REQUEST_TIMEOUT, headers=headers, **kwargs)
        if response.ok:
            return response
        st.error(f"API request failed ({response.status_code}): {response.text[:500]}")
    except requests.RequestException:
        st.error("The local API is unavailable. Start FastAPI and refresh this page.")
    return None


health = request("GET", "/health")
if health:
    st.success("Local API connected · sending disabled")

section = st.sidebar.radio("Workspace", ["Overview", "Contacts", "Draft review", "Suppression & audit", "Import"])

if section == "Overview":
    response = request("GET", "/stats")
    if response:
        stats = response.json()
        columns = st.columns(4)
        columns[0].metric("Contacts", stats["contacts"])
        categories = stats["qualifications"]
        columns[1].metric("Qualified for review", categories.get("qualified_for_review", 0))
        columns[2].metric("Pending drafts", stats["drafts"].get("pending", 0))
        columns[3].metric("Suppressed", stats["suppressed"])
        st.write("Qualification categories")
        st.json(categories)
        st.write("Draft review states")
        st.json(stats["drafts"])
        st.warning("Approval is a review record only. It does not send email.")

elif section == "Import":
    uploaded = st.file_uploader("Contacts file", type=["csv", "xlsx"])
    worksheet = st.text_input("Worksheet name (Excel only)")
    if st.button("Validate and import", disabled=uploaded is None, type="primary") and uploaded:
        response = request("POST", "/imports", files={"file": (uploaded.name, uploaded.getvalue())},
                           data={"worksheet": worksheet} if worksheet else {})
        if response:
            st.json(response.json())
            st.success("Import finished. Clean and rejected datasets were written to the local output directory.")

elif section == "Contacts":
    query = st.text_input("Search company, title, or name")
    status_options = ["All", "qualified_for_review", "needs_research", "insufficient_evidence"]
    selected_status = st.selectbox("Qualification", status_options)
    score_min, score_max = st.slider("Score range", 0, 100, (0, 100))
    page_size = st.selectbox("Rows per page", [25, 50, 100, 200], index=1)
    page_number = st.number_input("Page", min_value=1, value=1, step=1)
    params = {"page": page_number, "page_size": page_size, "score_min": score_min, "score_max": score_max}
    if query:
        params["query"] = query
    if selected_status != "All":
        params["status"] = selected_status
    response = request("GET", "/contacts", params=params)
    if response:
        listing = response.json()
        st.caption(f"{listing['total']} matching contacts")
        if listing["items"]:
            frame = pd.DataFrame(listing["items"])
            visible = [column for column in ("id", "email", "name", "company", "title", "qualification_status", "score", "consent_status") if column in frame]
            selection = st.dataframe(frame[visible], hide_index=True, use_container_width=True,
                                     on_select="rerun", selection_mode="single-row")
            selected_rows = selection.selection.rows
            if selected_rows:
                contact = listing["items"][selected_rows[0]]
                detail_response = request("GET", f"/contacts/{contact['id']}")
                detail = detail_response.json() if detail_response else contact
                st.subheader(f"Contact {detail['id']}")
                st.write({key: detail.get(key) for key in ("name", "company", "title", "source_row", "consent_status", "opt_out")})
                st.write("Qualification evidence")
                st.json(detail.get("qualification") or "Not evaluated")
                if st.button("Generate review draft", key=f"draft-{detail['id']}"):
                    created = request("POST", f"/contacts/{detail['id']}/drafts")
                    if created:
                        st.session_state["selected_draft_id"] = created.json()["id"]
                        st.success("Draft created for human review. No email was sent.")
        else:
            st.info("No matching contacts.")

elif section == "Draft review":
    response = request("GET", "/drafts", params={"review_status": "pending"})
    if response:
        pending = response.json()
        if not pending:
            st.info("No pending drafts.")
        else:
            chosen_id = st.selectbox("Pending draft", [draft["id"] for draft in pending],
                                     index=next((i for i, draft in enumerate(pending)
                                                 if draft["id"] == st.session_state.get("selected_draft_id")), 0))
            draft = next(item for item in pending if item["id"] == chosen_id)
            contact_response = request("GET", f"/contacts/{draft['contact_id']}")
            if contact_response:
                contact = contact_response.json()
                st.write(f"To: {contact.get('name') or 'Unknown recipient'} · {contact.get('email')}")
                st.write(f"Company/title evidence: {contact.get('company') or 'Unknown'} · {contact.get('title') or 'Unknown'}")
            st.caption(f"Version {draft['version']} · {draft['delivery_status']}")
            with st.form(f"draft-edit-{draft['id']}"):
                subject = st.text_input("Subject", value=draft["subject"], max_chars=180)
                body = st.text_area("Message", value=draft["body"], height=260, max_chars=2500)
                save = st.form_submit_button("Save as new version")
            if save:
                edited = request("PUT", f"/drafts/{draft['id']}", json={"subject": subject, "body": body})
                if edited:
                    st.session_state["selected_draft_id"] = edited.json()["id"]
                    st.success(f"Saved version {edited.json()['version']}.")
                    st.rerun()
            st.caption("Candidate facts used: " + "; ".join(draft["candidate_facts_used"]))
            approve_col, reject_col = st.columns(2)
            with approve_col:
                if st.button("Approve draft", key=f"approve-{draft['id']}"):
                    reviewed = request("POST", f"/drafts/{draft['id']}/review", json={"decision": "approved"})
                    if reviewed:
                        st.success("Approval recorded. This did not send the message.")
                        st.rerun()
            with reject_col:
                if st.button("Reject draft", key=f"reject-{draft['id']}"):
                    reviewed = request("POST", f"/drafts/{draft['id']}/review", json={"decision": "rejected"})
                    if reviewed:
                        st.success("Rejection recorded. No email was sent.")
                        st.rerun()

else:
    st.subheader("Suppression list")
    with st.form("suppression-add"):
        email = st.text_input("Email address")
        reason = st.text_input("Reason (optional)")
        add = st.form_submit_button("Suppress contact")
    if add:
        response = request("POST", "/suppressions", json={"email": email, "reason": reason or None})
        if response:
            st.success("Suppression recorded.")
    response = request("GET", "/suppressions")
    if response:
        st.dataframe(pd.DataFrame(response.json()), hide_index=True, use_container_width=True)
    st.subheader("Audit history")
    response = request("GET", "/audit")
    if response:
        st.dataframe(pd.DataFrame(response.json()), hide_index=True, use_container_width=True)