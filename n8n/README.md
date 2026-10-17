# Local n8n Review Bridge

This first milestone does not send mail and does not include an n8n workflow JSON. The Python API can be reviewed locally; node-by-node instructions are provided here rather than an unverified import artifact.

## Prerequisites

- Start FastAPI on `127.0.0.1:8000`; do not bind it to `0.0.0.0` or expose it publicly.
- Set `OUTREACH_API_TOKEN` to a locally generated secret before connecting n8n. Store that token as an n8n Header Auth credential, never in node JSON.
- If n8n runs in Docker, use the host's private bridge address supported by that deployment; do not open the service to the public internet.
- The API exposes draft review and suppression only. There is no Gmail sender endpoint.

## Suggested Manual Review Flow

1. **Manual Trigger**: run only when an operator starts the review.
2. **HTTP Request - List Pending Drafts**: `GET http://127.0.0.1:8000/drafts?review_status=pending`; attach the stored Header Auth credential.
3. **Split Out**: process one pending draft item at a time. Do not create a bulk-send branch.
4. **Wait / Human Review**: present the exact subject and body for an explicit human decision. The reviewer must be able to reject; a timeout must not approve.
5. **HTTP Request - Record Review**: on explicit approve or reject only, `POST http://127.0.0.1:8000/drafts/{id}/review` with JSON `{"decision":"approved"}` or `{"decision":"rejected"}` and the stored credential.
6. **Stop**: record the API result. Do not add a Gmail node; approval records do not send messages.

The API is idempotent at the contact/draft level for workflow replay, versions edits rather than replacing text, and rejects approvals for suppressed contacts. A replay must not be interpreted as permission to contact.

## Gmail Status

OAuth, delivery-attempt reconciliation, bounce/reply handling, and actual Gmail sending are not implemented or tested. To add them safely, first request an explicit implementation approval, then configure Google OAuth with the minimum send scope, test only with a dedicated account and recipient you control, and retain per-message human approval, suppression rechecks, bounded limits, and uncertain-delivery stop behavior. Never place OAuth credentials in workflow exports.