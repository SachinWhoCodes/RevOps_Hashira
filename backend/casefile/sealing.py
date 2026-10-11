"""Seal a write credential for the executor (rule R1 with self-service setup).

An admin pastes a write token (HubSpot write app, Slack bot token, Gmail drafts grant) into the web
app. The app seals it with the executor's *public* key and stores only the result; it holds no key that
can open it again. The executor process alone has the private key (``.env.executor``) and opens the
seal when it builds that company's writers (``casefile.executor.unseal``).

Construction (an ECIES-style sealed box): an ephemeral X25519 key pair per message, HKDF-SHA256 over
the shared secret, ChaCha20-Poly1305. Format: ``x25519v1.`` + base64url(ephemeral public | nonce |
ciphertext). Tampering or the wrong key fails authentication.
"""

from __future__ import annotations

import base64
import os

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey, X25519PublicKey
from cryptography.hazmat.primitives.ciphers.aead import ChaCha20Poly1305
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from casefile.config import get_settings

PREFIX = "x25519v1."
INFO = b"casefile-executor-seal-v1"


class SealingUnavailable(RuntimeError):
    """No executor public key configured on this server."""


def b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def raw_public(key: X25519PublicKey) -> bytes:
    return key.public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)


def derive_key(shared: bytes, ephemeral: bytes, recipient: bytes) -> bytes:
    return HKDF(algorithm=hashes.SHA256(), length=32, salt=ephemeral + recipient, info=INFO).derive(shared)


def new_keypair() -> tuple[str, str]:
    """(private, public), both base64url. The private key goes to .env.executor only."""
    private = X25519PrivateKey.generate()
    raw_private = private.private_bytes(
        serialization.Encoding.Raw, serialization.PrivateFormat.Raw, serialization.NoEncryption()
    )
    return b64(raw_private), b64(raw_public(private.public_key()))


def available() -> bool:
    try:
        _public()
    except (SealingUnavailable, ValueError):
        return False
    return True


def _public() -> X25519PublicKey:
    raw = get_settings().executor_seal_public_key
    if not raw:
        raise SealingUnavailable("EXECUTOR_SEAL_PUBLIC_KEY is not set; run `casefile init-executor-keys`")
    return X25519PublicKey.from_public_bytes(unb64(raw))


def seal_for_executor(plaintext: str, public_key: str | None = None) -> str:
    recipient = X25519PublicKey.from_public_bytes(unb64(public_key)) if public_key else _public()
    ephemeral = X25519PrivateKey.generate()
    eph_raw = raw_public(ephemeral.public_key())
    key = derive_key(ephemeral.exchange(recipient), eph_raw, raw_public(recipient))
    nonce = os.urandom(12)
    sealed = ChaCha20Poly1305(key).encrypt(nonce, plaintext.encode(), None)
    return PREFIX + b64(eph_raw + nonce + sealed)
