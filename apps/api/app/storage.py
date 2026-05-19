"""Content-addressed local document storage.

Files live on disk under `{documents_dir}/{sha256[:2]}/{sha256}` — keyed
by the SHA-256 of the bytes. Two identical uploads de-dupe automatically
to the same on-disk file. A reasonable default for dev and a single-box
prod; for multi-node prod, swap the read/write functions for S3 calls
without touching the routers.
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

from app.config import settings

_MAX_BYTES = 25 * 1024 * 1024  # 25 MB ceiling per file


def _base() -> Path:
    """Resolve the documents directory lazily so tests can monkey-patch
    settings.documents_dir before the first call."""
    base = Path(settings.documents_dir)
    base.mkdir(parents=True, exist_ok=True)
    return base


def _path_for(sha256: str) -> Path:
    return _base() / sha256[:2] / sha256


class TooLargeError(ValueError):
    pass


def store_bytes(data: bytes) -> tuple[str, int]:
    """Write `data` content-addressed. Returns (sha256, size_bytes)."""
    if len(data) > _MAX_BYTES:
        raise TooLargeError(
            f"File is {len(data)} bytes; max is {_MAX_BYTES}."
        )
    digest = hashlib.sha256(data).hexdigest()
    path = _path_for(digest)
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_bytes(data)
        os.replace(tmp, path)
    return digest, len(data)


def read_bytes(sha256: str) -> bytes:
    return _path_for(sha256).read_bytes()


def exists(sha256: str) -> bool:
    return _path_for(sha256).exists()
