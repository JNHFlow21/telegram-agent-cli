"""Shared argument helpers and the preview/execute write gate."""
from __future__ import annotations

import time
from typing import Any, Awaitable, Callable, Iterable

from ..client import TG
from ..envelope import emit, emit_failure, emit_preview, emit_write
from ..errors import CliError, map_exception
from ..safety import new_request_id, read_receipt, write_receipt


def add_write_flags(parser, *, destructive: bool = False) -> None:
    parser.add_argument(
        "--execute",
        action="store_true",
        help="真正提交写操作；缺省只做预检，不改变 Telegram 状态。",
    )
    parser.add_argument(
        "--request-id",
        default=None,
        help="幂等键；同一条写操作重试时复用同一个值。",
    )
    if destructive:
        parser.add_argument("--yes", action="store_true", help="确认执行破坏性操作。")
        parser.add_argument(
            "--confirm",
            default=None,
            help="必须与目标标签完全一致（通常为 @username 或标题）才执行。",
        )


def add_chat(parser, *, name: str = "chat", help_text: str = "目标会话：@username、peer_id、t.me 链接或标题。") -> None:
    parser.add_argument(name, metavar="CHAT", help=help_text)


def add_limit(parser, *, default: int = 50, maximum: int = 1000) -> None:
    parser.add_argument("--limit", type=int, default=default, help=f"最多返回条数（默认 {default}）。")


def csv(value: str | None) -> list[str]:
    if not value:
        return []
    return [part.strip() for part in value.split(",") if part.strip()]


def require_confirmation(args, label: str) -> None:
    if getattr(args, "yes", False):
        return
    supplied = getattr(args, "confirm", None)
    if supplied is not None and supplied == label:
        return
    raise CliError(
        "BLOCKED",
        "破坏性操作需要显式确认：加 --yes，或 --confirm 传入与目标完全一致的标签。",
        details={"required_confirm": label},
    )


async def run_write(
    ctx: TG,
    args,
    *,
    command: str,
    action: str,
    params: dict,
    preview: Any,
    execute: Callable[[], Awaitable[Any]],
    destructive: bool = False,
    confirm_label: str | None = None,
) -> dict:
    """Preview by default; execute once per request id.

    A request id that is already `executed` replays the stored result with
    outcome=noop. A request id in `unknown` state is blocked: the caller must
    inspect Telegram before opening a new request.
    """
    started = time.monotonic()
    request_id = getattr(args, "request_id", None) or new_request_id(action.replace(".", "-"))

    base = {"action": action, "request_id": request_id, **ctx.account_meta()}
    if not getattr(args, "execute", False):
        return emit_preview(
            command,
            {"status": "preview", "params": params, "preview": preview, "requires_execute": True},
            started,
            **base,
        )

    if destructive:
        try:
            require_confirmation(args, confirm_label or "")
        except CliError as exc:
            return emit_failure(
                command,
                exc.code,
                exc.message,
                started,
                details=exc.details,
                outcome=exc.outcome,
                **base,
            )

    existing = read_receipt(request_id, ctx.settings)
    if existing is not None:
        if existing.get("action") != action or existing.get("params") != params:
            return emit_failure(
                command,
                "BLOCKED",
                "该 request_id 已用于不同的动作或参数；请换一个新的 request_id。",
                started,
                details={
                    "request_id": request_id,
                    "existing_action": existing.get("action"),
                    "existing_params": existing.get("params"),
                },
                **base,
            )
        status = existing.get("status")
        if status == "executed":
            return emit(
                command,
                True,
                "noop",
                data={
                    "status": "idempotent_replay",
                    "action": action,
                    "request_id": request_id,
                    "result": existing.get("result"),
                    "original_updated_at": existing.get("updated_at"),
                },
                meta={"elapsed_ms": round((time.monotonic() - started) * 1000), **base},
            )
        if status in {"unknown", "submitted"}:
            return emit_failure(
                command,
                "OUTCOME_UNKNOWN",
                "该 request_id 的上一次提交结果未知；请先核对 Telegram，再决定是否用新的 request_id 重试。",
                started,
                details={"request_id": request_id, "receipt": existing},
                outcome="unknown",
                **base,
            )

    try:
        result = await execute()
    except Exception as exc:  # noqa: BLE001 - mapped to a stable envelope
        mapped = map_exception(exc)
        if mapped.code in {"UNAVAILABLE", "OUTCOME_UNKNOWN"}:
            write_receipt(
                request_id,
                action=action,
                params=params,
                status="unknown",
                error={"code": mapped.code, "message": mapped.message, "details": mapped.details},
                settings=ctx.settings,
            )
            mapped.outcome = "unknown"
        return emit_failure(
            command,
            mapped.code,
            mapped.message,
            started,
            retriable=mapped.retriable,
            details=mapped.details,
            outcome=mapped.outcome,
            **base,
        )

    write_receipt(
        request_id,
        action=action,
        params=params,
        status="executed",
        result=result,
        settings=ctx.settings,
    )
    return emit_write(
        command,
        {"status": "executed", "params": params, "result": result},
        started,
        **base,
    )


def describe_resolved(resolved) -> dict:
    return resolved.as_dict()


def ensure_not_empty(value: str | None, field: str) -> str:
    if value is None or not value.strip():
        raise CliError("VALIDATION", f"{field} 不能为空。")
    return value.strip()


def log_stderr(message: str) -> None:
    import sys

    print(message, file=sys.stderr, flush=True)
