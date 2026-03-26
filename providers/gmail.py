"""
providers/gmail.py
Gmail provider — polls a Gmail inbox via the Gmail API.

Auth flow: OAuth 2.0 with offline access (refresh token).
Run providers/gmail_auth.py once to generate your refresh token.

Security fixes applied:
  - MIME header injection: subject is stripped of newlines/carriage returns
    before being placed in email headers (prevents Bcc/Cc injection)
  - Recursion DoS: _get_plain_body has a depth limit of 10 to prevent
    stack exhaustion from maliciously crafted nested multipart emails
"""

import base64
import email as email_lib
import logging
import re
from datetime import datetime, timezone
from email.mime.text import MIMEText

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

import config
from providers.base import BaseEmailProvider, StandardEmail

log = logging.getLogger(__name__)

SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/gmail.modify",
]

_MAX_RECURSION_DEPTH = 10


def _strip_html(html: str) -> str:
    text = re.sub(r"<[^>]+>", " ", html)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _sanitize_header(value: str) -> str:
    """
    Strip newlines and carriage returns from a value before using it as a
    MIME header. Newlines in headers enable injection of arbitrary headers
    (Bcc, Cc, Content-Type, etc.) by a malicious sender.
    """
    return value.replace("\r", "").replace("\n", " ").strip()


class GmailProvider(BaseEmailProvider):

    def __init__(self) -> None:
        self._mailbox = config.GMAIL_ADDRESS
        self._service = self._build_service()

    def _build_service(self):
        creds = Credentials(
            token=None,
            refresh_token=config.GMAIL_REFRESH_TOKEN,
            token_uri="https://oauth2.googleapis.com/token",
            client_id=config.GMAIL_CLIENT_ID,
            client_secret=config.GMAIL_CLIENT_SECRET,
            scopes=SCOPES,
        )
        creds.refresh(Request())
        return build("gmail", "v1", credentials=creds, cache_discovery=False)

    def _get_plain_body(self, payload: dict, depth: int = 0) -> str:
        """
        Recursively extract plain text from a Gmail message payload.
        Depth is capped at _MAX_RECURSION_DEPTH to prevent stack exhaustion
        from pathologically nested multipart emails.
        """
        if depth > _MAX_RECURSION_DEPTH:
            log.warning("Email payload nesting exceeds depth limit — truncating body extraction.")
            return ""

        mime_type = payload.get("mimeType", "")
        body_data = payload.get("body", {}).get("data", "")

        if mime_type == "text/plain" and body_data:
            return base64.urlsafe_b64decode(body_data).decode("utf-8", errors="replace")

        if mime_type == "text/html" and body_data:
            html = base64.urlsafe_b64decode(body_data).decode("utf-8", errors="replace")
            return _strip_html(html)

        for part in payload.get("parts", []):
            result = self._get_plain_body(part, depth=depth + 1)
            if result:
                return result

        return ""

    def fetch_new_emails(self) -> list[StandardEmail]:
        try:
            result = self._service.users().messages().list(
                userId="me",
                q="is:unread in:inbox",
                maxResults=25,
            ).execute()
        except HttpError as exc:
            log.error("Gmail API list error: %s", exc)
            return []

        messages = result.get("messages", [])
        if not messages:
            return []

        emails: list[StandardEmail] = []
        for msg_ref in messages:
            try:
                msg = self._service.users().messages().get(
                    userId="me",
                    id=msg_ref["id"],
                    format="full",
                ).execute()

                headers = {
                    h["name"].lower(): h["value"]
                    for h in msg.get("payload", {}).get("headers", [])
                }

                subject  = headers.get("subject", "(no subject)")
                sender   = headers.get("from", "unknown")
                date_str = headers.get("date", "")
                body     = self._get_plain_body(msg.get("payload", {}))

                try:
                    timestamp = email_lib.utils.parsedate_to_datetime(date_str)
                    if timestamp.tzinfo is None:
                        timestamp = timestamp.replace(tzinfo=timezone.utc)
                except Exception:
                    timestamp = datetime.now(timezone.utc)

                emails.append(StandardEmail(
                    id        = msg["id"],
                    subject   = subject,
                    body      = body,
                    sender    = sender,
                    timestamp = timestamp,
                    raw       = msg,
                ))

            except (HttpError, KeyError, ValueError) as exc:
                log.warning("Skipping malformed Gmail message %s: %s", msg_ref["id"], exc)

        return emails

    def mark_as_processed(self, email_id: str) -> None:
        try:
            self._service.users().messages().modify(
                userId="me",
                id=email_id,
                body={"removeLabelIds": ["UNREAD"]},
            ).execute()
            log.debug("Marked Gmail message %s as read.", email_id)
        except HttpError as exc:
            log.error("Failed to mark Gmail message %s as read: %s", email_id, exc)

    def forward_email(self, email: StandardEmail, to_address: str) -> None:
        """
        Forward the email to the routing target.
        Subject and sender are sanitized before being placed in MIME headers
        to prevent header injection attacks.
        """
        raw_subject = email.subject if email.subject.startswith("Fwd:") else f"Fwd: {email.subject}"

        # Sanitize all values used in MIME headers
        safe_subject = _sanitize_header(raw_subject)
        safe_to      = _sanitize_header(to_address)
        safe_from    = _sanitize_header(self._mailbox)
        safe_sender  = _sanitize_header(email.sender)

        body_text = (
            f"[Auto-routed by IT Email Router]\n"
            f"Original sender: {safe_sender}\n"
            f"Received: {email.timestamp.strftime('%Y-%m-%d %H:%M UTC')}\n"
            f"\n--- Original message ---\n\n"
            f"{email.body[:3000]}"
        )

        msg = MIMEText(body_text)
        msg["to"]      = safe_to
        msg["from"]    = safe_from
        msg["subject"] = safe_subject

        raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()

        try:
            self._service.users().messages().send(
                userId="me",
                body={"raw": raw},
            ).execute()
            log.info("Forwarded Gmail message %s to %s", email.id, to_address)
        except HttpError as exc:
            log.error("Failed to forward Gmail message %s: %s", email.id, exc)
            raise

    def forward_for_review(self, email: StandardEmail, to_address: str, context_header: str) -> None:
        """Forward to human review inbox with context block prepended."""
        subject   = email.subject if email.subject.startswith("Fwd:") else f"Fwd: {email.subject}"
        body_text = (
            f"{context_header}"
            f"{email.body[:3000]}"
        )

        msg = MIMEText(body_text)
        msg["to"]      = to_address
        msg["from"]    = self._mailbox
        msg["subject"] = subject

        raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
        try:
            self._service.users().messages().send(
                userId="me",
                body={"raw": raw},
            ).execute()
            log.info("Forwarded Gmail message %s to review inbox %s", email.id, to_address)
        except HttpError as exc:
            log.error("Failed to forward Gmail message %s for review: %s", email.id, exc)
            raise
