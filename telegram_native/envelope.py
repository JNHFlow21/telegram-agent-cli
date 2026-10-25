"""tg.v1 unified CLI output envelope.

One JSON object on stdout for every command, success or failure. ``ok`` is the
single truth of success; ``outcome`` is the orthogonal effect dimension;
``error.code`` is a stable machine-branchable string.
"""
from __future__ import annotations

import json
import time
from typing import Any

SCHEMA = "tg.v1"
OUTCOMES = ("readonly", "preview", "executed", "noop", "unknown")
ERROR_CODES = (
    "VALIDATION",
    "PERMISSION_DENIED",
    "NOT_FOUND",
    "TARGET_AMBIGUOUS",
    "THROTTLED",
    "UNSUPPORTED",
    "REJECTED",
    "UNAVAILABLE",
    "OUTCOME_UNKNOWN",
    "CONFIRMATION_REQUIRED",
    "BLOCKED",
    "INTERNAL",
)


def _iso(ts: float | None = None) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ts))


def error(
    code: str,
    message: str,
    retriable: bool = False,
    details: Any = None,
) -> dict:
    if code not in ERROR_CODES:
        raise ValueError(f"Unknown error code: {code}")
    if not isinstance(message, str) or not message:
        raise ValueError("Error message required")
    err: dict[str, Any] = {"code": code, "message": message, "retriable": bool(retriable)}
    if details is not None:
        err["details"] = details
    return err


def build(
    command: str,
    ok: bool,
    outcome: str,
    data: Any = None,
    err: dict | None = None,
    warnings: list | None = None,
    meta: dict | None = None,
) -> dict:
    if outcome not in OUTCOMES:
        raise ValueError(f"Unknown outcome: {outcome}")
    if bool(ok) == (err is not None):
        raise ValueError("Exactly one of ok/error is required")
    m: dict[str, Any] = {"ts": _iso()}
    if meta:
        m.update({k: v for k, v in meta.items() if v is not None})
    return {
        "schema": SCHEMA,
        "command": command,
        "ok": bool(ok),
        "outcome": outcome,
        "data": data if ok else None,
        "error": err,
        "warnings": list(warnings or []),
        "meta": m,
    }


def emit(
    command: str,
    ok: bool,
    outcome: str,
    data: Any = None,
    err: dict | None = None,
    warnings: list | None = None,
    meta: dict | None = None,
) -> dict:
    env = build(command, ok, outcome, data, err, warnings, meta)
    print(json.dumps(env, ensure_ascii=False, default=str))
    return env


def emit_read(command: str, data: Any, started: float, **meta: Any) -> dict:
    return emit(
        command,
        True,
        "readonly",
        data=data,
        meta={"elapsed_ms": round((time.monotonic() - started) * 1000), **meta},
    )


def emit_preview(command: str, data: Any, started: float, **meta: Any) -> dict:
    return emit(
        command,
        True,
        "preview",
        data=data,
        meta={"elapsed_ms": round((time.monotonic() - started) * 1000), **meta},
    )


def emit_write(command: str, data: Any, started: float, **meta: Any) -> dict:
    return emit(
        command,
        True,
        "executed",
        data=data,
        meta={"elapsed_ms": round((time.monotonic() - started) * 1000), **meta},
    )


def emit_failure(
    command: str,
    code: str,
    message: str,
    started: float | None = None,
    retriable: bool = False,
    details: Any = None,
    outcome: str = "noop",
    **meta: Any,
) -> dict:
    if started is not None:
        meta["elapsed_ms"] = round((time.monotonic() - started) * 1000)
    return emit(
        command,
        False,
        outcome,
        err=error(code, message, retriable=retriable, details=details),
        meta=meta,
    )
