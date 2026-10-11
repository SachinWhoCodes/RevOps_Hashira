"""Google OAuth for an installed (desktop) app, using plain HTTP — no Google SDK.

- ``AccessToken`` swaps a stored refresh token for short-lived access tokens.
- ``interactive_consent`` runs the loopback flow once (PKCE), so a person can sign in as the
  mailbox owner and get a refresh token for the scopes of one zone (read or write).
"""

from __future__ import annotations

import base64
import hashlib
import secrets
import threading
import time
import urllib.parse
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer

import httpx

from casefile.connectors.base import AuthError

TOKEN_URL = "https://oauth2.googleapis.com/token"
AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
CONSENT_WAIT_SECONDS = 600

SCOPES = {
    # Read zone: sync only.
    "read": [
        "https://www.googleapis.com/auth/gmail.readonly",
        "https://www.googleapis.com/auth/calendar.readonly",
    ],
    # Write zone (executor, Phase 4): create drafts. The code has no send path.
    "write": ["https://www.googleapis.com/auth/gmail.compose"],
    # Sandbox seeding only: insert backdated synthetic messages into the test mailbox.
    "seed": [
        "https://www.googleapis.com/auth/gmail.insert",
        "https://www.googleapis.com/auth/calendar.events",
    ],
}


class AccessToken:
    def __init__(
        self, client_id: str, client_secret: str, refresh_token: str, client: httpx.Client | None = None
    ):
        self._client_id, self._secret, self._refresh = client_id, client_secret, refresh_token
        self._http = client or httpx.Client(timeout=20)
        self._token: str | None = None
        self._expires = 0.0

    def get(self) -> str:
        if self._token and time.time() < self._expires - 60:
            return self._token
        resp = self._http.post(
            TOKEN_URL,
            data={
                "client_id": self._client_id,
                "client_secret": self._secret,
                "refresh_token": self._refresh,
                "grant_type": "refresh_token",
            },
        )
        if resp.status_code != 200:
            # invalid_grant usually means the refresh token expired (7 days in Testing mode) or was revoked.
            raise AuthError(f"google token refresh failed: {resp.status_code} {resp.text[:200]}")
        data = resp.json()
        self._token = data["access_token"]
        self._expires = time.time() + float(data.get("expires_in", 3600))
        return self._token


def interactive_consent(client_id: str, client_secret: str, zone: str) -> str:
    """Open the browser for consent and return the refresh token."""
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    state = secrets.token_urlsafe(16)
    result: dict[str, str] = {}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            query = dict(urllib.parse.parse_qsl(urllib.parse.urlparse(self.path).query))
            # Only Google's redirect carries state; ignore stray requests (favicon, prefetch).
            if "state" in query:
                result.update(query)
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"Casefile: consent received. You can close this tab.")

        def log_message(self, *args: object) -> None:
            return

    server = HTTPServer(("127.0.0.1", 0), Handler)
    server.timeout = 1  # handle_request returns every second so we can stop on time
    redirect = f"http://127.0.0.1:{server.server_port}"
    url = (
        AUTH_URL
        + "?"
        + urllib.parse.urlencode(
            {
                "client_id": client_id,
                "redirect_uri": redirect,
                "response_type": "code",
                "scope": " ".join(SCOPES[zone]),
                "access_type": "offline",
                "prompt": "consent",
                "code_challenge": challenge,
                "code_challenge_method": "S256",
                "state": state,
            }
        )
    )
    deadline = time.time() + CONSENT_WAIT_SECONDS

    def serve() -> None:
        while "state" not in result and time.time() < deadline:
            server.handle_request()

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    print(f"Opening the browser for Google consent ({zone} scopes). If it does not open, visit:\n{url}")
    print(f"Waiting up to {CONSENT_WAIT_SECONDS // 60} minutes for you to finish in the browser…")
    webbrowser.open(url)
    thread.join(timeout=CONSENT_WAIT_SECONDS + 5)
    server.server_close()
    if result.get("state") != state or "code" not in result:
        error = result.get("error", "no reply from Google")
        raise AuthError(
            f"consent failed or timed out: {error}. If Google showed 'Access blocked', add the mailbox as a "
            "test user (Google Cloud Console → Google Auth Platform → Audience → Test users) and run again."
        )
    resp = httpx.post(
        TOKEN_URL,
        data={
            "client_id": client_id,
            "client_secret": client_secret,
            "code": result["code"],
            "code_verifier": verifier,
            "grant_type": "authorization_code",
            "redirect_uri": redirect,
        },
        timeout=20,
    )
    if resp.status_code != 200 or "refresh_token" not in resp.json():
        raise AuthError(f"code exchange failed: {resp.status_code} {resp.text[:200]}")
    return str(resp.json()["refresh_token"])
