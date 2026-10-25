"""BotFather operations.

BotFather is a conversational interface with inline keyboards, not a single
RPC.  Every public command therefore opens one serialized conversation (the
``botfather.lock`` file), sends one command, follows the inline buttons and
waits for the next BotFather message with ``get_messages(min_id=...)``.

Safety invariants:

* nothing is sent to BotFather before the ``run_write`` preview/execute gate
  has opened, and the "execute" half is additionally protected by a receipt
  keyed by ``--request-id``;
* a timeout after a value or a confirmation button has been submitted raises
  ``OUTCOME_UNKNOWN`` and is recorded as such, so the same request id cannot
  blindly send the step twice;
* full tokens are only written to ``<bots_dir>/<username>.json`` (mode 0600)
  and are never put in an envelope, a receipt or an error message unless the
  caller explicitly asked for ``--show-secret``.
"""
from __future__ import annotations

import asyncio
import inspect
import json
import os
import re
import time
from pathlib import Path
from typing import Any, Callable, Iterable

from ..client import TG
from ..config import Settings
from ..envelope import (
    emit,
    emit_failure,
    emit_preview,
    emit_read,
    emit_write,
)
from ..errors import CliError, map_exception
from ..safety import botfather_lock, new_request_id, read_receipt, write_receipt
from .common import run_write

BOTFATHER = "BotFather"
BOTFATHER_TIMEOUT = 30.0
BOTFATHER_POLL_INTERVAL = 1.0

# Bot tokens are "<bot id>:<secret>"; the secret length is not part of the
# public contract, so accept the real-world length and anything longer.
TOKEN_RE = re.compile(r"\b\d{6,12}:[A-Za-z0-9_-]{30,}\b")
# Broader pattern used only to scrub replies/errors, in case BotFather ever
# returns a token whose secret does not match the accepted real-world length.
_SENSITIVE_RE = re.compile(r"\b\d{6,12}:[A-Za-z0-9_-]{8,}\b")
BOT_USERNAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]*$")
BOT_USERNAME_FROM_TEXT_RE = re.compile(r"t\.me/([A-Za-z][A-Za-z0-9_]{1,28}bot)\b", re.I)
COMMAND_RE = re.compile(r"^[a-z0-9_]{1,32}$")

MAX_BOT_NAME = 64
MAX_BOT_ABOUT = 120
MAX_BOT_DESCRIPTION = 512
MAX_COMMAND_DESCRIPTION = 256
MAX_COMMANDS = 100

_CANCEL_RE = re.compile(r"\bcancell?ed\b", re.I)
_SUCCESS_RE = re.compile(r"\b(success(?:fully)?|done|updated|changed|deleted|removed|revoked|saved|okay)\b", re.I)

_FAILURE_MARKERS = (
    "already taken",
    "username is invalid",
    "invalid username",
    "invalid command",
    "invalid list",
    "not available",
    "don't have a bot with",
    "don't have a bot named",
    "something went wrong",
    "failed",
    "too long",
    "too many bots",
    "invalid name",
    "can only contain",
    "not a valid",
    "unable to",
    "can't be used",
    "couldn't",
    "could not",
)
_ROTATION_MARKERS = (
    "revoked",
    "new token",
    "generated a new",
    "regenerated",
    "no longer valid",
    "previous token",
    "invalidated",
    "rotated",
)
_TOKEN_ROTATION_NOTE = (
    "BotFather 未明确说明 /token 是否轮换了 token；官方文档措辞不一致，"
    "请视为可能已轮换。"
)
_NO_BOTS_MARKERS = (
    "no bots",
    "don't have any bots",
    "do not have any bots",
    "haven't created any bots",
    "haven't made any bots",
)


# ---------------------------------------------------------------------------
# Small value helpers
# ---------------------------------------------------------------------------


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _normalize_username(value: str | None) -> str:
    text = (value or "").strip()
    if text.startswith("@"):
        text = text[1:]
    if not text:
        raise CliError("VALIDATION", "--username 不能为空。")
    if (
        len(text) < 5
        or len(text) > 32
        or not BOT_USERNAME_RE.fullmatch(text)
        or not text.lower().endswith("bot")
    ):
        raise CliError(
            "VALIDATION",
            "Bot 用户名必须为 5-32 位、以字母开头、只含字母/数字/下划线，并以 bot 结尾。",
            details={"username": text},
        )
    return text


def _validate_text(value: str | None, *, field: str, maximum: int) -> str:
    text = (value or "").strip()
    if not text:
        raise CliError("VALIDATION", f"{field} 不能为空。")
    if len(text) > maximum:
        raise CliError(
            "VALIDATION",
            f"{field} 长度 {len(text)} 超过 BotFather 限制 {maximum}。",
            details={"field": field, "length": len(text), "maximum": maximum},
        )
    return text


def _extract_token(text: str | None) -> str | None:
    if not text:
        return None
    match = TOKEN_RE.search(text)
    return match.group(0) if match else None


def _bot_id_from_token(token: str) -> int:
    return int(token.split(":", 1)[0])


def _mask_token(token: str) -> str:
    """Keep only enough of a token to identify it, never the full secret."""
    match = TOKEN_RE.search(token or "")
    if not match:
        return "***"
    value = match.group(0)
    bot_id, _, secret = value.partition(":")
    if not secret:
        return f"{bot_id}:***"
    if len(secret) <= 8:
        return f"{bot_id}:****"
    return f"{bot_id}:{secret[:4]}…{secret[-4:]}"


def _safe_text(text: str | None, limit: int = 500) -> str | None:
    """A BotFather reply safe to put in error details (tokens masked)."""
    if not text:
        return None
    masked = _SENSITIVE_RE.sub(lambda m: _mask_token(m.group(0)), text)
    return masked[:limit]


def _detect_token_rotation(text: str | None) -> bool | None:
    """True only when BotFather explicitly says the token was rotated."""
    if not text:
        return None
    folded = text.casefold()
    if any(marker in folded for marker in _ROTATION_MARKERS):
        return True
    return None


def _is_confirmation(message: Any, keywords: Iterable[str]) -> bool:
    if not _has_buttons(message):
        return False
    if _contains_any(message, ("choose a bot", "choose the bot", "select a bot")):
        return False
    return _contains_any(message, keywords)


def _username_from_text(text: str | None) -> str | None:
    if not text:
        return None
    match = BOT_USERNAME_FROM_TEXT_RE.search(text)
    return match.group(1) if match else None


# ---------------------------------------------------------------------------
# Telegram message/button adapters (duck-typed for fake fixtures in tests)
# ---------------------------------------------------------------------------


def _message_id(message: Any) -> int | None:
    value = getattr(message, "id", None)
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _message_text(message: Any) -> str:
    for attr in ("message", "text", "raw_text"):
        value = getattr(message, attr, None)
        if isinstance(value, str):
            return value
    return ""


def _is_outgoing(message: Any) -> bool:
    return bool(getattr(message, "out", False) or getattr(message, "is_out", False))


def _iter_buttons(message: Any) -> Iterable[Any]:
    rows = getattr(message, "buttons", None) or []
    for row in rows:
        if isinstance(row, (list, tuple)):
            for button in row:
                yield button
        else:
            yield row


def _button_label(button: Any) -> str:
    label = getattr(button, "text", None)
    if isinstance(label, str):
        return label
    inner = getattr(button, "button", None)
    label = getattr(inner, "text", None)
    return label if isinstance(label, str) else ""


def _button_labels(message: Any) -> list[str]:
    return [_button_label(button) for button in _iter_buttons(message)]


async def _ensure_buttons(message: Any) -> None:
    """Populate ``message.buttons`` when Telethon's lazy property returned None.

    ``Message.buttons`` normally resolves the bot from the chat; in rare cases
    the async ``get_buttons()`` fallback is needed.  Fixtures that do not
    implement it are left untouched.
    """
    if _has_buttons(message):
        return
    getter = getattr(message, "get_buttons", None)
    if not callable(getter):
        return
    try:
        result = getter()
        if inspect.isawaitable(result):
            await result
    except Exception:  # noqa: BLE001 - buttons may genuinely be absent
        return


def _has_buttons(message: Any) -> bool:
    return any(label for label in _button_labels(message))


def _fingerprint(message: Any) -> tuple:
    return (
        getattr(message, "edit_date", None),
        _message_text(message),
        tuple(_button_labels(message)),
    )


def _message_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        return list(value)
    return [value]


def _contains_any(message: Any, needles: Iterable[str]) -> bool:
    text = _message_text(message).casefold()
    return any(needle.casefold() in text for needle in needles)


def _contains_all(message: Any, needles: Iterable[str]) -> bool:
    text = _message_text(message).casefold()
    return all(needle.casefold() in text for needle in needles)


def _mentions_no_bots(message: Any) -> bool:
    return _contains_any(message, _NO_BOTS_MARKERS)


def _raise_on_botfather_failure(message: Any, *, step: str) -> None:
    text = _message_text(message).strip()
    if not text:
        return
    folded = text.casefold()
    if _CANCEL_RE.search(text) or re.search(r"^\s*/cancel\b", text):
        raise CliError(
            "REJECTED",
            "BotFather 回复已取消（cancel），本次操作未完成。",
            details={"step": step, "botfather_message": _safe_text(text)},
        )
    marker = next((marker for marker in _FAILURE_MARKERS if marker in folded), None)
    if marker is not None:
        code = "NOT_FOUND" if "bot with" in marker or "bot named" in marker else "REJECTED"
        raise CliError(
            code,
            "BotFather 拒绝了该步骤。",
            details={
                "step": step,
                "marker": marker,
                "botfather_message": _safe_text(text),
            },
        )


def _find_username_button(message: Any, username: str) -> Any:
    target = username.casefold()
    exact: list[Any] = []
    partial: list[Any] = []
    for button in _iter_buttons(message):
        label = _button_label(button)
        normalized = label.strip().lstrip("@").casefold()
        if normalized == target or label.strip().casefold() == f"@{target}":
            exact.append(button)
        elif target in label.casefold():
            partial.append(button)
    labels = _button_labels(message)
    if len(exact) == 1:
        return exact[0]
    if len(exact) > 1:
        raise CliError(
            "TARGET_AMBIGUOUS",
            f"BotFather 提供了多个与 @{username} 完全同名的按钮。",
            details={"target": username, "available_buttons": labels},
        )
    if len(partial) == 1:
        return partial[0]
    if len(partial) > 1:
        raise CliError(
            "TARGET_AMBIGUOUS",
            f"BotFather 的多个按钮都匹配 @{username}；请使用更精确的 Bot 用户名。",
            details={"target": username, "available_buttons": labels},
        )
    raise CliError(
        "NOT_FOUND",
        f"BotFather 的按钮中没有 @{username}（按钮标题可能已变化）。",
        details={"target": username, "available_buttons": labels},
    )


def _find_action_button(
    message: Any,
    *,
    keywords: Iterable[str],
    step: str,
    exclude: Iterable[str] = ("cancel", "no", "back"),
) -> Any:
    labels = _button_labels(message)
    matches: list[Any] = []
    for button, label in zip(_iter_buttons(message), labels):
        folded = label.casefold()
        if any(word.casefold() in folded for word in keywords) and not any(
            re.search(rf"\b{re.escape(word.casefold())}\b", folded) for word in exclude
        ):
            matches.append(button)
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        raise CliError(
            "TARGET_AMBIGUOUS",
            f"「{step}」的确认按钮不唯一。",
            details={"step": step, "available_buttons": labels},
        )
    raise CliError(
        "NOT_FOUND",
        f"「{step}」缺少预期的确认按钮（按钮标题可能已变化）。",
        details={"step": step, "available_buttons": labels},
    )


# ---------------------------------------------------------------------------
# Conversation engine
# ---------------------------------------------------------------------------


class _BotFatherDialog:
    """One BotFather conversation: send, poll replies, click inline buttons."""

    def __init__(self, ctx: TG, *, timeout: float | None = None) -> None:
        self.ctx = ctx
        self.client = ctx.client
        self.timeout = BOTFATHER_TIMEOUT if timeout is None else timeout
        self.last_id = 0
        self._fingerprints: dict[int, tuple] = {}
        self._anchor: Any = None
        self.sent_texts: list[str] = []
        self.token_rotation_observed = False

    async def send(self, text: str) -> Any:
        self.sent_texts.append(text)
        message = await self.client.send_message(BOTFATHER, text)
        if message is not None:
            self._remember(message)
            self.last_id = max(self.last_id, _message_id(message) or 0)
        return message

    async def click(self, message: Any, button: Any) -> Any:
        # Record the pre-click content so an in-place edit is detected as fresh.
        self._remember(message)
        self._anchor = message
        clicker = getattr(button, "click", None)
        if callable(clicker):
            result = clicker()
            if inspect.isawaitable(result):
                return await result
            return result
        # Tolerate fixtures/clients that expose message.click(text=...) only.
        message_clicker = getattr(message, "click", None)
        if callable(message_clicker):
            result = message_clicker(text=_button_label(button))
            if inspect.isawaitable(result):
                return await result
            return result
        raise CliError(
            "INTERNAL",
            "当前消息既没有可点击的按钮对象，也没有 message.click(text=...) 接口。",
        )

    def _remember(self, message: Any) -> None:
        message_id = _message_id(message)
        if message_id is None:
            return
        self._fingerprints[message_id] = _fingerprint(message)
        if not _is_outgoing(message):
            self._anchor = message

    async def _fetch(self, *, min_id: int, limit: int = 50) -> list[Any]:
        result = await self.client.get_messages(BOTFATHER, min_id=min_id, limit=limit)
        return [message for message in _message_list(result) if message is not None]

    async def wait_for(
        self,
        predicate: Callable[[Any], bool],
        *,
        step: str,
        mutating: bool,
        timeout: float | None = None,
    ) -> Any:
        """Wait for a fresh or edited BotFather message matching ``predicate``.

        ``mutating`` controls the timeout code: once a value or a confirmation
        has been submitted, the outcome is unknown and must not be retried
        blindly.  Before that point the step is still retriable.
        """
        deadline = time.monotonic() + (self.timeout if timeout is None else timeout)
        last_reply = ""
        while True:
            fresh: list[Any] = []

            batch = sorted(await self._fetch(min_id=self.last_id), key=lambda m: _message_id(m) or 0)
            for message in batch:
                message_id = _message_id(message)
                if message_id is not None:
                    self.last_id = max(self.last_id, message_id)
                if _is_outgoing(message) or message_id is None:
                    continue
                if self._fingerprints.get(message_id) != _fingerprint(message):
                    self._fingerprints[message_id] = _fingerprint(message)
                    self._anchor = message
                    fresh.append(message)

            # BotFather often edits the message whose inline button was pressed
            # instead of sending a new one.  Re-read the anchor by id range.
            anchor = self._anchor
            anchor_id = _message_id(anchor) if anchor is not None else None
            if anchor_id is not None:
                recent = await self._fetch(min_id=max(anchor_id - 1, 0), limit=5)
                for message in recent:
                    if _message_id(message) != anchor_id or _is_outgoing(message):
                        continue
                    if self._fingerprints.get(anchor_id) != _fingerprint(message):
                        self._fingerprints[anchor_id] = _fingerprint(message)
                        self._anchor = message
                        fresh.append(message)

            for message in fresh:
                await _ensure_buttons(message)
                message_id = _message_id(message)
                if message_id is not None:
                    self._fingerprints[message_id] = _fingerprint(message)
                if _detect_token_rotation(_message_text(message)):
                    self.token_rotation_observed = True
            for message in fresh:
                _raise_on_botfather_failure(message, step=step)
            for message in fresh:
                if predicate(message):
                    self._remember(message)
                    return message
            if fresh:
                last_reply = _message_text(fresh[-1])

            if time.monotonic() >= deadline:
                raise self._timeout_error(step, mutating=mutating, last_reply=last_reply)
            await asyncio.sleep(BOTFATHER_POLL_INTERVAL)

    def _timeout_error(self, step: str, *, mutating: bool, last_reply: str) -> CliError:
        details: dict[str, Any] = {
            "step": step,
            "timeout_seconds": self.timeout,
            "last_message_id": self.last_id,
        }
        if last_reply:
            details["last_reply"] = _safe_text(last_reply)
        if mutating:
            return CliError(
                "OUTCOME_UNKNOWN",
                f"BotFather 未在 {self.timeout:.0f} 秒内确认「{step}」；结果未知，请先核对 Bot 状态，勿用新 request-id 重试。",
                details=details,
                outcome="unknown",
            )
        return CliError(
            "UNAVAILABLE",
            f"BotFather 未在 {self.timeout:.0f} 秒内响应「{step}」。",
            retriable=True,
            details=details,
        )


async def _select_bot(dialog: _BotFatherDialog, username: str, *, command: str) -> Any:
    await dialog.send(command)
    message = await dialog.wait_for(
        lambda m: _has_buttons(m) or _mentions_no_bots(m),
        step=f"{command}/choose-bot",
        mutating=False,
    )
    if not _has_buttons(message):
        raise CliError(
            "NOT_FOUND",
            f"BotFather 表示该账号下没有可管理的 Bot（{command}）。",
            details={"command": command, "botfather_message": _safe_text(_message_text(message))},
        )
    button = _find_username_button(message, username)
    await dialog.click(message, button)
    return message


async def _wait_for_prompt(
    dialog: _BotFatherDialog,
    *,
    step: str,
    keywords: Iterable[str],
    timeout: float | None = None,
) -> Any:
    return await dialog.wait_for(
        lambda m: _contains_any(m, keywords),
        step=step,
        mutating=False,
        timeout=timeout,
    )


async def _wait_for_success(dialog: _BotFatherDialog, *, step: str) -> Any:
    return await dialog.wait_for(
        lambda m: bool(_SUCCESS_RE.search(_message_text(m))),
        step=step,
        mutating=True,
    )


# ---------------------------------------------------------------------------
# Token storage
# ---------------------------------------------------------------------------


def _bot_record_path(settings: Settings, username: str) -> Path:
    safe = username.casefold()
    if not re.fullmatch(r"[a-z][a-z0-9_]*bot", safe):
        raise CliError("VALIDATION", "Bot 用户名不能安全地映射到本地文件。", details={"username": username})
    return settings.bots_dir / f"{safe}.json"


def _read_bot_record(path: Path) -> dict:
    try:
        value = json.loads(path.read_text("utf-8"))
    except (OSError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def _store_bot_token(
    settings: Settings,
    username: str,
    token: str,
    *,
    bot_id: int | None = None,
    name: str | None = None,
    source: str,
) -> Path:
    """Write the full token to a 0600 JSON file and return its path."""
    path = _bot_record_path(settings, username)
    settings.bots_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        os.chmod(settings.bots_dir, 0o700)
    except OSError:
        pass

    previous = _read_bot_record(path)
    now = _now()
    record: dict[str, Any] = {
        "username": username,
        "bot_id": bot_id if bot_id is not None else _bot_id_from_token(token),
        "token": token,
        "source": source,
        "created_at": previous.get("created_at") or now,
        "updated_at": now,
    }
    if name is not None:
        record["name"] = name
    elif isinstance(previous.get("name"), str):
        record["name"] = previous["name"]

    tmp = path.with_name(path.name + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.fchmod(fd, 0o600)
    except OSError:
        pass
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(record, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    return path


def _read_stored_token(settings: Settings, username: str) -> str | None:
    try:
        value = _read_bot_record(_bot_record_path(settings, username)).get("token")
    except CliError:
        return None
    return value if isinstance(value, str) and TOKEN_RE.fullmatch(value) else None


# ---------------------------------------------------------------------------
# Command-file parsing
# ---------------------------------------------------------------------------


def _parse_commands_file(path_value: str | None) -> tuple[list[tuple[str, str]], str]:
    """Read ``command - description`` lines and return (items, body)."""
    if not path_value:
        raise CliError("VALIDATION", "--commands-file 不能为空。")
    path = Path(path_value).expanduser()
    if not path.is_file():
        raise CliError("VALIDATION", f"找不到命令文件：{path}", details={"path": str(path)})
    try:
        raw = path.read_text("utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise CliError(
            "VALIDATION",
            f"无法读取命令文件：{path}",
            details={"path": str(path), "type": type(exc).__name__},
        ) from None

    items: list[tuple[str, str]] = []
    seen: set[str] = set()
    for line_number, line in enumerate(raw.splitlines(), start=1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if " - " not in stripped:
            raise CliError(
                "VALIDATION",
                f"命令文件第 {line_number} 行格式不正确；应为 `command - description`。",
                details={"line": line_number, "value": stripped[:120]},
            )
        command, description = (part.strip() for part in stripped.split(" - ", 1))
        command = command.lstrip("/")
        if not COMMAND_RE.fullmatch(command):
            raise CliError(
                "VALIDATION",
                f"命令文件第 {line_number} 行的命令名不合法：{command!r}（1-32 位小写字母/数字/下划线）。",
                details={"line": line_number, "command": command[:64]},
            )
        if command in seen:
            raise CliError(
                "VALIDATION",
                f"命令文件第 {line_number} 行重复定义 /{command}。",
                details={"line": line_number, "command": command},
            )
        if not description:
            raise CliError(
                "VALIDATION",
                f"命令文件第 {line_number} 行缺少描述。",
                details={"line": line_number, "command": command},
            )
        if len(description) > MAX_COMMAND_DESCRIPTION:
            raise CliError(
                "VALIDATION",
                f"命令文件第 {line_number} 行描述超过 {MAX_COMMAND_DESCRIPTION} 字符。",
                details={"line": line_number, "command": command, "length": len(description)},
            )
        seen.add(command)
        items.append((command, description))

    if not items:
        raise CliError("VALIDATION", "命令文件中没有有效命令。")
    if len(items) > MAX_COMMANDS:
        raise CliError(
            "VALIDATION",
            f"命令数量 {len(items)} 超过 BotFather 限制 {MAX_COMMANDS}。",
            details={"count": len(items), "maximum": MAX_COMMANDS},
        )
    body = "\n".join(f"{command} - {description}" for command, description in items)
    return items, body


# ---------------------------------------------------------------------------
# BotFather flows.  Each helper returns JSON-safe data for the envelope.
# ---------------------------------------------------------------------------


async def _create_flow(
    ctx: TG,
    *,
    name: str,
    username: str,
) -> dict:
    dialog = _BotFatherDialog(ctx)
    await dialog.send("/newbot")
    await _wait_for_prompt(
        dialog,
        step="newbot/name",
        keywords=("name", "call it"),
    )

    await dialog.send(name)
    await _wait_for_prompt(
        dialog,
        step="newbot/username",
        keywords=("username", "must end in"),
    )

    await dialog.send(username)
    reply = await dialog.wait_for(
        lambda m: _extract_token(_message_text(m)) is not None,
        step="newbot/token",
        mutating=True,
    )
    token = _extract_token(_message_text(reply))
    if token is None:  # defensive: the predicate cannot match without one
        raise CliError("NOT_FOUND", "BotFather 的回复中没有可识别的 token。")

    actual_username = _username_from_text(_message_text(reply)) or username
    bot_id = _bot_id_from_token(token)
    path = _store_bot_token(
        ctx.settings,
        actual_username,
        token,
        bot_id=bot_id,
        name=name,
        source="create",
    )
    masked = _mask_token(token)
    return {
        "bot": {"id": bot_id, "username": actual_username, "name": name},
        "username": actual_username,
        "bot_id": bot_id,
        "name": name,
        "token_masked": masked,
        "token_file": str(path),
    }


async def _token_flow(ctx: TG, *, username: str, show_secret: bool) -> tuple[dict, dict]:
    dialog = _BotFatherDialog(ctx)
    await _select_bot(dialog, username, command="/token")
    # Selecting the bot can rotate the token in some BotFather versions, so a
    # timeout after this click is an unknown outcome, not a retriable read.
    reply = await dialog.wait_for(
        lambda m: _extract_token(_message_text(m)) is not None,
        step="token/result",
        mutating=True,
    )
    token = _extract_token(_message_text(reply))
    if token is None:
        raise CliError("NOT_FOUND", "BotFather 的回复中没有可识别的 token。")
    rotation = True if dialog.token_rotation_observed or _detect_token_rotation(_message_text(reply)) else None

    bot_id = _bot_id_from_token(token)
    path = _store_bot_token(
        ctx.settings,
        username,
        token,
        bot_id=bot_id,
        source="token",
    )
    masked = _mask_token(token)
    display: dict[str, Any] = {
        "username": username,
        "bot_id": bot_id,
        "token_masked": masked,
        "token": token if show_secret else masked,
        "token_file": str(path),
        "show_secret": show_secret,
        "token_rotated": rotation,
    }
    if rotation is None:
        display["rotation_note"] = _TOKEN_ROTATION_NOTE
    safe_result: dict[str, Any] = {
        "username": username,
        "bot_id": bot_id,
        "token_masked": masked,
        "token_file": str(path),
        "token_rotated": rotation,
    }
    if rotation is None:
        safe_result["rotation_note"] = _TOKEN_ROTATION_NOTE
    return display, safe_result


async def _set_name_flow(ctx: TG, *, username: str, name: str) -> dict:
    dialog = _BotFatherDialog(ctx)
    await _select_bot(dialog, username, command="/setname")
    await _wait_for_prompt(dialog, step="setname/prompt", keywords=("new name",))
    await dialog.send(name)
    await _wait_for_success(dialog, step="setname/result")
    return {"username": username, "name": name, "status": "updated"}


async def _set_about_flow(ctx: TG, *, username: str, about: str) -> dict:
    dialog = _BotFatherDialog(ctx)
    await _select_bot(dialog, username, command="/setabouttext")
    await dialog.wait_for(
        lambda m: _contains_all(m, ("about", "new")),
        step="setabout/prompt",
        mutating=False,
    )
    await dialog.send(about)
    await _wait_for_success(dialog, step="setabout/result")
    return {"username": username, "about": about, "status": "updated"}


async def _set_description_flow(ctx: TG, *, username: str, description: str) -> dict:
    dialog = _BotFatherDialog(ctx)
    await _select_bot(dialog, username, command="/setdescription")
    await dialog.wait_for(
        lambda m: _contains_all(m, ("description", "new")),
        step="setdescription/prompt",
        mutating=False,
    )
    await dialog.send(description)
    await _wait_for_success(dialog, step="setdescription/result")
    return {"username": username, "description": description, "status": "updated"}


async def _set_commands_flow(ctx: TG, *, username: str, items: list[tuple[str, str]], body: str) -> dict:
    dialog = _BotFatherDialog(ctx)
    await _select_bot(dialog, username, command="/setcommands")
    await _wait_for_prompt(dialog, step="setcommands/prompt", keywords=("list of commands",))
    await dialog.send(body)
    await _wait_for_success(dialog, step="setcommands/result")
    return {
        "username": username,
        "commands": [{"command": command, "description": description} for command, description in items],
        "count": len(items),
        "status": "updated",
    }


async def _revoke_flow(ctx: TG, *, username: str) -> dict:
    dialog = _BotFatherDialog(ctx)
    await _select_bot(dialog, username, command="/revoke")
    # BotFather has two observed shapes here:
    #   /revoke -> choose bot -> new token immediately; or
    #   /revoke -> choose bot -> confirmation buttons -> new token.
    # Selecting the bot may already rotate the token, so every timeout from
    # here on is OUTCOME_UNKNOWN.
    reply = await dialog.wait_for(
        lambda m: _extract_token(_message_text(m)) is not None
        or _is_confirmation(m, ("revoke", "sure", "confirm", "yes", "token")),
        step="revoke/result-or-confirm",
        mutating=True,
    )
    token = _extract_token(_message_text(reply))
    confirmation_required = False
    if token is None:
        confirmation_required = True
        button = _find_action_button(
            reply,
            keywords=("yes", "revoke"),
            step="revoke/confirm",
        )
        await dialog.click(reply, button)
        reply = await dialog.wait_for(
            lambda m: _extract_token(_message_text(m)) is not None,
            step="revoke/token",
            mutating=True,
        )
        token = _extract_token(_message_text(reply))
    if token is None:
        raise CliError("NOT_FOUND", "BotFather 的撤销回复中没有可识别的新 token。")

    bot_id = _bot_id_from_token(token)
    path = _store_bot_token(
        ctx.settings,
        username,
        token,
        bot_id=bot_id,
        source="revoke",
    )
    masked = _mask_token(token)
    return {
        "username": username,
        "bot_id": bot_id,
        "revoked": True,
        "token_rotated": True,
        "confirmation_required": confirmation_required,
        "token_masked": masked,
        "token": masked,
        "token_file": str(path),
    }


async def _delete_flow(ctx: TG, *, username: str) -> dict:
    dialog = _BotFatherDialog(ctx)
    await _select_bot(dialog, username, command="/deletebot")
    confirmation = await dialog.wait_for(
        lambda m: _is_confirmation(m, ("delete", "sure", "confirm"))
        or _contains_any(m, ("are you sure", "totally sure", "send 'yes")),
        step="delete/confirm",
        mutating=False,
    )
    if _has_buttons(confirmation):
        button = _find_action_button(
            confirmation,
            keywords=("yes", "delete"),
            step="delete/confirm",
        )
        await dialog.click(confirmation, button)
    else:
        # Current BotFather asks the user to *send* this exact sentence
        # instead of providing an inline confirmation button.
        await dialog.send("Yes, I am totally sure.")
    await _wait_for_success(dialog, step="delete/result")

    path = _bot_record_path(ctx.settings, username)
    removed = False
    try:
        path.unlink()
        removed = True
    except FileNotFoundError:
        pass
    except OSError:
        # The remote bot is already gone; a local cleanup failure must not turn
        # a successful delete into an unknown outcome.
        removed = False
    return {"username": username, "deleted": True, "token_file_removed": removed}


# ---------------------------------------------------------------------------
# Write-gate plumbing
# ---------------------------------------------------------------------------


async def _connect_for_write(ctx: TG, args) -> None:
    """Connect before the gate only when ``--execute`` was requested.

    Preview therefore stays fully offline and never touches BotFather.
    """
    if getattr(args, "execute", False):
        await ctx.connect()
        await ctx.require_authorized()


async def _close_quietly(ctx: TG) -> None:
    try:
        await ctx.close()
    except Exception:  # noqa: BLE001 - closing must not mask the command result
        pass


# ---------------------------------------------------------------------------
# Public commands
# ---------------------------------------------------------------------------


async def create_bot(ctx: TG, args) -> dict:
    name = _validate_text(getattr(args, "name", None), field="--name", maximum=MAX_BOT_NAME)
    username = _normalize_username(getattr(args, "username", None))
    token_file = str(_bot_record_path(ctx.settings, username))

    async def execute() -> dict:
        with botfather_lock(ctx.settings):
            return await _create_flow(ctx, name=name, username=username)

    await _connect_for_write(ctx, args)
    try:
        params = {"name": name, "username": username}
        preview = {
            "action": "create_bot",
            "name": name,
            "username": username,
            "token_file": token_file,
            "botfather_steps": ["/newbot", "send name", "send username"],
        }
        return await run_write(
            ctx,
            args,
            command="bot create",
            action="bot.create",
            params=params,
            preview=preview,
            execute=execute,
        )
    finally:
        await _close_quietly(ctx)


async def list_bots(ctx: TG, args) -> dict:
    started = time.monotonic()
    await ctx.connect()
    try:
        await ctx.require_authorized()
        with botfather_lock(ctx.settings):
            dialog = _BotFatherDialog(ctx)
            await dialog.send("/mybots")
            reply = await dialog.wait_for(
                lambda m: _has_buttons(m) or _mentions_no_bots(m),
                step="mybots/list",
                mutating=False,
            )
            labels = _button_labels(reply)
            bots: list[dict[str, str]] = []
            seen: set[str] = set()
            for label in labels:
                for match in re.finditer(r"@?([A-Za-z][A-Za-z0-9_]{1,28}bot)\b", label, re.I):
                    username = match.group(1)
                    key = username.casefold()
                    if key in seen:
                        continue
                    seen.add(key)
                    bots.append({"username": username, "label": label})
            if labels and not bots:
                raise CliError(
                    "NOT_FOUND",
                    "BotFather 的 /mybots 按钮中没有可识别的 Bot 用户名。",
                    details={"available_buttons": labels},
                )
            return emit_read(
                "bot list",
                {
                    "count": len(bots),
                    "bots": bots,
                    "usernames": [item["username"] for item in bots],
                    "available_buttons": labels,
                },
                started,
                **ctx.account_meta(),
            )
    finally:
        await _close_quietly(ctx)


async def get_token(ctx: TG, args) -> dict:
    """Read the token; like writes, it previews unless ``--execute`` is set.

    This command has its own receipt path because the full secret must never
    be persisted in a receipt: the receipt stores only the masked result while
    the full token lives in the 0600 bot file.
    """
    username = _normalize_username(getattr(args, "username", None))
    show_secret = bool(getattr(args, "show_secret", False))
    token_file = str(_bot_record_path(ctx.settings, username))
    started = time.monotonic()
    request_id = getattr(args, "request_id", None) or new_request_id("bot-token")
    base = {"action": "bot.token", "request_id": request_id}
    params = {"username": username, "show_secret": show_secret, "token_file": token_file}

    if not getattr(args, "execute", False):
        return emit_preview(
            "bot token",
            {
                "status": "preview",
                "params": params,
                "preview": {
                    "action": "read_token",
                    "username": username,
                    "show_secret": show_secret,
                    "token_file": token_file,
                    "note": "BotFather 的 /token 在部分版本中可能轮换 token；执行前请确认。",
                    "botfather_steps": ["/token", f"click {username}"],
                },
                "requires_execute": True,
            },
            started,
            **base,
        )

    existing = read_receipt(request_id, ctx.settings)
    if existing is not None:
        status = existing.get("status")
        if status == "executed":
            data = dict(existing.get("result") or {})
            if show_secret:
                token = _read_stored_token(ctx.settings, username)
                if token:
                    data["token"] = token
            return emit(
                "bot token",
                True,
                "noop",
                data={
                    "status": "idempotent_replay",
                    "action": "bot.token",
                    "request_id": request_id,
                    "result": data,
                    "original_updated_at": existing.get("updated_at"),
                },
                meta={
                    "elapsed_ms": round((time.monotonic() - started) * 1000),
                    "account_id": None,
                    "account_username": None,
                    "phone_masked": None,
                    "dc_id": None,
                },
            )
        if status in {"unknown", "submitted"}:
            return emit_failure(
                "bot token",
                "OUTCOME_UNKNOWN",
                "该 request-id 的上一次 token 读取/轮换结果未知；请先核对本地 token 文件与 Telegram，再决定是否用新的 request-id 重试。",
                started,
                details={"request_id": request_id, "receipt": existing},
                outcome="unknown",
            )

    await ctx.connect()
    try:
        await ctx.require_authorized()
        base.update(ctx.account_meta())
        try:
            with botfather_lock(ctx.settings):
                display, safe_result = await _token_flow(
                    ctx,
                    username=username,
                    show_secret=show_secret,
                )
        except Exception as exc:  # noqa: BLE001 - mapped to a stable envelope
            mapped = map_exception(exc)
            if mapped.code in {"UNAVAILABLE", "OUTCOME_UNKNOWN"}:
                write_receipt(
                    request_id,
                    action="bot.token",
                    params=params,
                    status="unknown",
                    error={"code": mapped.code, "message": mapped.message, "details": mapped.details},
                    settings=ctx.settings,
                )
                mapped.outcome = "unknown"
            return emit_failure(
                "bot token",
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
            action="bot.token",
            params=params,
            status="executed",
            result=safe_result,
            settings=ctx.settings,
        )
        return emit_write(
            "bot token",
            {"status": "executed", "params": params, "result": display},
            started,
            **base,
        )
    finally:
        await _close_quietly(ctx)


async def set_name(ctx: TG, args) -> dict:
    username = _normalize_username(getattr(args, "username", None))
    name = _validate_text(getattr(args, "name", None), field="--name", maximum=MAX_BOT_NAME)

    async def execute() -> dict:
        with botfather_lock(ctx.settings):
            return await _set_name_flow(ctx, username=username, name=name)

    await _connect_for_write(ctx, args)
    try:
        params = {"username": username, "name": name}
        return await run_write(
            ctx,
            args,
            command="bot set-name",
            action="bot.set_name",
            params=params,
            preview={"action": "set_name", **params, "botfather_steps": ["/setname", name]},
            execute=execute,
        )
    finally:
        await _close_quietly(ctx)


async def set_about(ctx: TG, args) -> dict:
    username = _normalize_username(getattr(args, "username", None))
    about = _validate_text(getattr(args, "about", None), field="--about", maximum=MAX_BOT_ABOUT)

    async def execute() -> dict:
        with botfather_lock(ctx.settings):
            return await _set_about_flow(ctx, username=username, about=about)

    await _connect_for_write(ctx, args)
    try:
        params = {"username": username, "about": about}
        return await run_write(
            ctx,
            args,
            command="bot set-about",
            action="bot.set_about",
            params=params,
            preview={"action": "set_about", **params, "botfather_steps": ["/setabouttext", about]},
            execute=execute,
        )
    finally:
        await _close_quietly(ctx)


async def set_description(ctx: TG, args) -> dict:
    username = _normalize_username(getattr(args, "username", None))
    description = _validate_text(
        getattr(args, "description", None),
        field="--description",
        maximum=MAX_BOT_DESCRIPTION,
    )

    async def execute() -> dict:
        with botfather_lock(ctx.settings):
            return await _set_description_flow(ctx, username=username, description=description)

    await _connect_for_write(ctx, args)
    try:
        params = {"username": username, "description": description}
        return await run_write(
            ctx,
            args,
            command="bot set-description",
            action="bot.set_description",
            params=params,
            preview={"action": "set_description", **params, "botfather_steps": ["/setdescription", description]},
            execute=execute,
        )
    finally:
        await _close_quietly(ctx)


async def set_commands(ctx: TG, args) -> dict:
    username = _normalize_username(getattr(args, "username", None))
    items, body = _parse_commands_file(getattr(args, "commands_file", None))

    async def execute() -> dict:
        with botfather_lock(ctx.settings):
            return await _set_commands_flow(ctx, username=username, items=items, body=body)

    await _connect_for_write(ctx, args)
    try:
        params = {
            "username": username,
            "commands": [{"command": command, "description": description} for command, description in items],
            "count": len(items),
        }
        return await run_write(
            ctx,
            args,
            command="bot set-commands",
            action="bot.set_commands",
            params=params,
            preview={
                "action": "set_commands",
                "username": username,
                "count": len(items),
                "formatted": body,
                "botfather_steps": ["/setcommands", "send formatted list"],
            },
            execute=execute,
        )
    finally:
        await _close_quietly(ctx)


async def revoke_token(ctx: TG, args) -> dict:
    username = _normalize_username(getattr(args, "username", None))

    async def execute() -> dict:
        with botfather_lock(ctx.settings):
            return await _revoke_flow(ctx, username=username)

    await _connect_for_write(ctx, args)
    try:
        params = {"username": username}
        return await run_write(
            ctx,
            args,
            command="bot revoke",
            action="bot.revoke_token",
            params=params,
            preview={
                "action": "revoke_token",
                "username": username,
                "note": "旧 token 会立即失效，新 token 将写入本地 0600 文件。",
                "botfather_steps": ["/revoke", f"click {username}", "confirm revoke"],
            },
            execute=execute,
            destructive=True,
            confirm_label=username,
        )
    finally:
        await _close_quietly(ctx)


async def delete_bot(ctx: TG, args) -> dict:
    username = _normalize_username(getattr(args, "username", None))

    async def execute() -> dict:
        with botfather_lock(ctx.settings):
            return await _delete_flow(ctx, username=username)

    await _connect_for_write(ctx, args)
    try:
        params = {"username": username}
        return await run_write(
            ctx,
            args,
            command="bot delete",
            action="bot.delete",
            params=params,
            preview={
                "action": "delete_bot",
                "username": username,
                "note": "Bot 会被永久删除，token 会失效，本地 token 文件会删除。",
                "botfather_steps": ["/deletebot", f"click {username}", "confirm delete"],
            },
            execute=execute,
            destructive=True,
            confirm_label=username,
        )
    finally:
        await _close_quietly(ctx)
