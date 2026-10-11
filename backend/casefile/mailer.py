"""Transactional email: sign-up confirmation, password reset, invites. Platform mail to our own users,
never customer-facing (customer email is only ever a Gmail draft, written by the executor).

Brevo's transactional API when ``BREVO_API_KEY`` and ``MAIL_FROM_EMAIL`` are set (the sender must be
verified in Brevo). Otherwise, on a local deployment only, the message is written to ``data/outbox``
so development needs no email account; a public deployment without Brevo refuses to pretend it sent.
"""

from __future__ import annotations

import contextlib
import html
import re
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from email.message import EmailMessage
from typing import Protocol

import httpx
import structlog

from casefile.config import get_settings

log = structlog.get_logger()
BREVO_URL = "https://api.brevo.com/v3/smtp/email"
LOCAL = re.compile(r"^https?://(localhost|127\.0\.0\.1|\[::1\]|[a-z0-9-]+\.localhost)(:\d+)?(/|$)")


class MailError(RuntimeError):
    """The message was not accepted for delivery."""


@dataclass(frozen=True)
class Mail:
    to: str
    subject: str
    text: str
    to_name: str = ""

    def html(self) -> str:
        """Plain text with links made clickable — no templates, nothing to inject into."""
        body = html.escape(self.text)
        body = re.sub(r"(https?://[^\s<]+)", r'<a href="\1">\1</a>', body)
        style = "font-family:system-ui,sans-serif;font-size:14px;line-height:1.5"
        return f'<div style="{style}">{body.replace(chr(10), "<br>")}</div>'


class Mailer(Protocol):
    def send(self, mail: Mail) -> str: ...


class BrevoMailer:
    def __init__(
        self, api_key: str, sender: str, sender_name: str, client: httpx.Client | None = None
    ) -> None:
        self.sender, self.sender_name = sender, sender_name
        self.http = client or httpx.Client(
            timeout=20, headers={"api-key": api_key, "accept": "application/json"}
        )

    def send(self, mail: Mail) -> str:
        body = {
            "sender": {"email": self.sender, "name": self.sender_name},
            "to": [{"email": mail.to, **({"name": mail.to_name} if mail.to_name else {})}],
            "subject": mail.subject,
            "textContent": mail.text,
            "htmlContent": mail.html(),
        }
        try:
            resp = self.http.post(BREVO_URL, json=body)
        except httpx.HTTPError as exc:
            raise MailError(f"email service unreachable: {exc}") from exc
        if resp.status_code not in (200, 201, 202):
            # Brevo explains itself in "message" (e.g. an unverified sender); never echo the key.
            detail = ""
            with contextlib.suppress(ValueError):
                detail = str(resp.json().get("message", ""))[:200]
            raise MailError(f"email service refused the message ({resp.status_code}) {detail}".strip())
        return str(resp.json().get("messageId", ""))


class OutboxMailer:
    """Development only: the message is written to a file instead of being sent."""

    def send(self, mail: Mail) -> str:
        folder = get_settings().outbox_dir
        folder.mkdir(parents=True, exist_ok=True)
        msg = EmailMessage()
        msg["To"], msg["Subject"] = mail.to, mail.subject
        msg["Date"] = datetime.now(UTC).strftime("%a, %d %b %Y %H:%M:%S +0000")
        msg.set_content(mail.text)
        name = f"{datetime.now(UTC):%Y%m%dT%H%M%S}-{uuid.uuid4().hex[:8]}.eml"
        (folder / name).write_bytes(msg.as_bytes())
        log.info("email_written_to_outbox", file=name, subject=mail.subject)  # never the body: it holds links
        return f"outbox:{name}"


def get_mailer() -> Mailer:
    s = get_settings()
    if s.brevo_api_key and s.brevo_api_key.get_secret_value() and s.mail_from_email:
        return BrevoMailer(s.brevo_api_key.get_secret_value(), s.mail_from_email, s.mail_from_name)
    if LOCAL.match(s.app_base_url):
        return OutboxMailer()
    raise MailError("email is not configured on this server (BREVO_API_KEY and MAIL_FROM_EMAIL)")


def mail_mode() -> str:
    """'brevo', 'outbox' (local development) or 'none' — shown to admins, never secret."""
    s = get_settings()
    if s.brevo_api_key and s.brevo_api_key.get_secret_value() and s.mail_from_email:
        return "brevo"
    return "outbox" if LOCAL.match(s.app_base_url) else "none"
