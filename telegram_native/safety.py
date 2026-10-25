"""Idempotency receipts and a BotFather lock."""
from __future__ import annotations

import contextlib
import fcntl
import hashlib
import json
import os
import time
import uuid
from pathlib import Path
from typing import Any, Iterator

from .config import Settings, load_settings


def new_request_id(prefix: str = "req") -> str:
    return f"{prefix}-{time.strftime('%Y%m%d%H%M%S', time.gmtime())}-{uuid.uuid4().hex[:10]}"


def _receipt_path(settings: Settings, request_id: str) -> Path:
    digest = hashlib.sha256(request_id.encode("utf-8")).hexdigest()[:32]
    return settings.receipts_dir / f"{digest}.json"


def read_receipt(request_id: str, settings: Settings | None = None) -> dict | None:
    settings = settings or load_settings()
    path = _receipt_path(settings, request_id)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text("utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def write_receipt(
    request_id: str,
    *,
    action: str,
    params: dict,
    status: str,
    result: Any = None,
    error: Any = None,
    settings: Settings | None = None,
) -> dict:
    settings = settings or load_settings()
    settings.receipts_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    path = _receipt_path(settings, request_id)
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    existing = read_receipt(request_id, settings)
    record = {
        "request_id": request_id,
        "action": action,
        "params": params,
        "status": status,
        "result": result,
        "error": error,
        "created_at": (existing or {}).get("created_at", now),
        "updated_at": now,
    }
    tmp = path.with_suffix(".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(record, fh, ensure_ascii=False, indent=2, default=str)
        fh.write("\n")
    os.replace(tmp, path)
    return record


@contextlib.contextmanager
def botfather_lock(settings: Settings | None = None) -> Iterator[None]:
    """Serialize BotFather conversations across processes."""
    settings = settings or load_settings()
    settings.data_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd = os.open(settings.lock_path, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)
