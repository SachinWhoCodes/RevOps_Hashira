"""Encryption at rest for credentials an admin pastes into the app (onboarding).

Fernet (AES-128-CBC + HMAC-SHA256) with the read-zone key ``SECRETS_KEY``. A database dump or backup
alone does not reveal a token; the key never goes into the database. Only read-zone credentials are
stored this way — write credentials stay in the executor's environment (rule R1).
"""

from __future__ import annotations

from cryptography.fernet import Fernet, InvalidToken

from casefile.config import get_settings


class SecretsUnavailable(RuntimeError):
    """No key configured, or the stored value was encrypted with a different key."""


def _fernet() -> Fernet:
    key = get_settings().secrets_key
    if key is None or not key.get_secret_value():
        raise SecretsUnavailable("SECRETS_KEY is not set; run `casefile init-secrets-key` on the server")
    return Fernet(key.get_secret_value().encode())


def available() -> bool:
    try:
        _fernet()
    except (SecretsUnavailable, ValueError):
        return False
    return True


def seal(plaintext: str) -> str:
    return _fernet().encrypt(plaintext.encode()).decode()


def unseal(ciphertext: str) -> str:
    try:
        return _fernet().decrypt(ciphertext.encode()).decode()
    except InvalidToken as exc:
        raise SecretsUnavailable(
            "stored credential cannot be decrypted with the current SECRETS_KEY"
        ) from exc


def new_key() -> str:
    return Fernet.generate_key().decode()
