"""
providers/m365.py
Microsoft 365 provider — polls the shared mailbox via the Graph API.

Auth flow: OAuth 2.0 client credentials (app-only, no user sign-in required).
Permissions needed on your Azure AD app registration (application, not delegated):
  - Mail.Read         (read shared mailbox)
  - Mail.Send         (forward emails)
  - Mail.ReadWrite    (mark as read)

No inbound firewall rules required — all calls are outbound HTTPS.
"""

import logging
import re
from datetime import datetime

import msal
import requests

import config
from providers.base import BaseEmailProvider, StandardEmail

log = logging.getLogger(__name__)

GRAPH_BASE = "https://graph.microsoft.com/v1.0"


def _strip_html(html: str) -> str:
    """Remove HTML tags and collapse whitespace for cleaner AI input."""
    text = re.sub(r"<[^>]+>", " ", html)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


class M365Provider(BaseEmailProvider):

    def __init__(self) -> None:
        self._mailbox = config.M365_MAILBOX
        self._token: str | None = None
        self._token_expiry: datetime | None = None

        self._msal_app = msal.ConfidentialClientApplication(
            client_id=config.M365_CLIENT_ID,
            client_credential=config.M365_CLIENT_SECRET,
            authority=f"https://login.microsoftonline.com/{config.M365_TENANT_ID}",
        )

    # ── Auth ──────────────────────────────────────────────────────────────────

    def _get_token(self) -> str:
        """
        Acquire (or return cached) access token.
        MSAL handles caching and silent refresh automatically.
        """
        result = self._msal_app.acquire_token_silent(
            scopes=["https://graph.microsoft.com/.default"],
            account=None,
        )
        if not result:
            result = self._msal_app.acquire_token_for_client(
                scopes=["https://graph.microsoft.com/.default"]
            )
        if "access_token" not in result:
            raise RuntimeError(
                f"Failed to acquire M365 token: {result.get('error_description', result)}"
            )
        return result["access_token"]

    def _headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self._get_token()}",
            "Content-Type": "application/json",
        }

    def _get(self, url: str, params: dict | None = None) -> dict:
        resp = requests.get(url, headers=self._headers(), params=params, timeout=15)
        resp.raise_for_status()
        return resp.json()

    def _post(self, url: str, payload: dict) -> requests.Response:
        resp = requests.post(url, headers=self._headers(), json=payload, timeout=15)
        resp.raise_for_status()
        return resp

    def _patch(self, url: str, payload: dict) -> requests.Response:
        resp = requests.patch(url, headers=self._headers(), json=payload, timeout=15)
        resp.raise_for_status()
        return resp

    # ── Provider interface ────────────────────────────────────────────────────

    def fetch_new_emails(self) -> list[StandardEmail]:
        """Fetch unread messages from the shared mailbox."""
        url = f"{GRAPH_BASE}/users/{self._mailbox}/mailFolders/Inbox/messages"
        params = {
            "$filter": "isRead eq false",
            "$select": "id,subject,body,sender,receivedDateTime",
            "$top": 25,
            "$orderby": "receivedDateTime asc",
        }

        try:
            data = self._get(url, params=params)
        except requests.HTTPError as exc:
            log.error("Graph API fetch failed: %s", exc)
            return []

        emails: list[StandardEmail] = []
        for msg in data.get("value", []):
            try:
                body_content = msg.get("body", {}).get("content", "")
                body_type    = msg.get("body", {}).get("contentType", "text")
                plain_body   = _strip_html(body_content) if body_type == "html" else body_content

                emails.append(StandardEmail(
                    id        = msg["id"],
                    subject   = msg.get("subject", "(no subject)"),
                    body      = plain_body,
                    sender    = msg.get("sender", {}).get("emailAddress", {}).get("address", "unknown"),
                    timestamp = datetime.fromisoformat(
                        msg["receivedDateTime"].replace("Z", "+00:00")
                    ),
                    raw       = msg,
                ))
            except (KeyError, ValueError) as exc:
                log.warning("Skipping malformed message: %s", exc)

        return emails

    def mark_as_processed(self, email_id: str) -> None:
        """Mark a message as read so it won't be fetched again."""
        url = f"{GRAPH_BASE}/users/{self._mailbox}/messages/{email_id}"
        try:
            self._patch(url, {"isRead": True})
            log.debug("Marked message %s as read.", email_id)
        except requests.HTTPError as exc:
            log.error("Failed to mark message %s as read: %s", email_id, exc)

    def forward_email(self, email: StandardEmail, to_address: str) -> None:
        """Forward the email to the routing target."""
        url = f"{GRAPH_BASE}/users/{self._mailbox}/messages/{email.id}/forward"
        payload = {
            "toRecipients": [
                {"emailAddress": {"address": to_address}}
            ],
            "comment": (
                "[Auto-routed by IT Email Router] "
                "This email was automatically classified and forwarded."
            ),
        }
        try:
            self._post(url, payload)
            log.info("Forwarded message %s to %s", email.id, to_address)
        except requests.HTTPError as exc:
            log.error("Failed to forward message %s: %s", email.id, exc)
            raise

    def forward_for_review(self, email: StandardEmail, to_address: str, context_header: str) -> None:
        """Forward to human review inbox with context block prepended."""
        url = f"{GRAPH_BASE}/users/{self._mailbox}/messages/{email.id}/forward"
        payload = {
            "toRecipients": [{"emailAddress": {"address": to_address}}],
            "comment": context_header,
        }
        try:
            self._post(url, payload)
            log.info("Forwarded message %s to review inbox %s", email.id, to_address)
        except requests.HTTPError as exc:
            log.error("Failed to forward message %s for review: %s", email.id, exc)
            raise
