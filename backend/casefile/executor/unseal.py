"""Open credentials sealed by the web app (``casefile.sealing``). Executor process only: the private
key comes from ``.env.executor`` and nothing outside this package imports this module (a test checks
that the API and worker never import ``casefile.executor``)."""

from __future__ import annotations

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey, X25519PublicKey
from cryptography.hazmat.primitives.ciphers.aead import ChaCha20Poly1305

from casefile.sealing import PREFIX, derive_key, raw_public, unb64


class UnsealError(RuntimeError):
    """Not sealed for this executor's key, or tampered with."""


def unseal(sealed: str, private_key: str) -> str:
    if not sealed.startswith(PREFIX):
        raise UnsealError("unknown sealing format")
    blob = unb64(sealed[len(PREFIX) :])
    eph_raw, nonce, ciphertext = blob[:32], blob[32:44], blob[44:]
    private = X25519PrivateKey.from_private_bytes(unb64(private_key))
    shared = private.exchange(X25519PublicKey.from_public_bytes(eph_raw))
    key = derive_key(shared, eph_raw, raw_public(private.public_key()))
    try:
        return ChaCha20Poly1305(key).decrypt(nonce, ciphertext, None).decode()
    except InvalidTag as exc:
        raise UnsealError("sealed credential does not open with this executor's key") from exc
