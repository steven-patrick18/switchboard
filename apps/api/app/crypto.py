"""Symmetric encryption for credentials at rest.

The Fernet key is derived from settings.app_secret_key (or an explicit
credential_enc_key) so dev works without extra config and existing
ciphertext stays decryptable as long as the secret is stable. In
production this should be backed by a real KMS / Vault / Infisical
(the brief's foundation layer) — see CLAUDE.md. Secrets are encrypted
in the DB and only ever decrypted server-side; they are never returned
through the API or placed in agent/model context.
"""

import base64
import hashlib
from functools import lru_cache

from cryptography.fernet import Fernet, InvalidToken

from app.config import settings


@lru_cache
def _fernet() -> Fernet:
    raw = (
        getattr(settings, "credential_enc_key", "") or settings.app_secret_key
    ).encode("utf-8")
    key = base64.urlsafe_b64encode(hashlib.sha256(raw).digest())
    return Fernet(key)


def encrypt(plaintext: str) -> str:
    return _fernet().encrypt(plaintext.encode("utf-8")).decode("utf-8")


def decrypt(token: str) -> str:
    try:
        return _fernet().decrypt(token.encode("utf-8")).decode("utf-8")
    except InvalidToken as e:  # wrong key / corrupted ciphertext
        raise ValueError("Unable to decrypt credential") from e
