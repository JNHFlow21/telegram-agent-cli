"""Stable CLI errors and Telethon exception mapping."""
from __future__ import annotations

from typing import Any

from telethon.errors import (
    ChatAdminInviteRequiredError,
    ChatAdminRequiredError,
    ChatForbiddenError,
    ChatWriteForbiddenError,
    ChannelPrivateError,
    FloodWaitError,
    InviteHashExpiredError,
    InviteHashInvalidError,
    MessageEditTimeExpiredError,
    MessageIdInvalidError,
    PeerIdInvalidError,
    PasswordHashInvalidError,
    PhoneCodeExpiredError,
    PhoneCodeInvalidError,
    SessionPasswordNeededError,
    SessionRevokedError,
    RightForbiddenError,
    SlowModeWaitError,
    TopicDeletedError,
    UserNotParticipantError,
    UserPrivacyRestrictedError,
    UsernameInvalidError,
    UsernameNotOccupiedError,
    UsernameOccupiedError,
    RPCError,
)


class CliError(Exception):
    """An error that already carries a stable tg.v1 code."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        retriable: bool = False,
        details: Any = None,
        outcome: str = "noop",
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.retriable = retriable
        self.details = details
        self.outcome = outcome


def _rpc_details(exc: RPCError) -> dict:
    # Only stable metadata; never include the raw request payload.
    return {
        "type": type(exc).__name__,
        "error_code": getattr(exc, "code", None),
        "telegram_message": str(exc)[:500],
    }


def map_exception(exc: BaseException) -> CliError:
    if isinstance(exc, CliError):
        return exc
    if isinstance(exc, FloodWaitError):
        return CliError(
            "THROTTLED",
            "Telegram 要求等待后再操作，未自动重试。",
            retriable=True,
            details={"type": type(exc).__name__, "retry_after": exc.seconds},
        )
    if isinstance(exc, SlowModeWaitError):
        return CliError(
            "THROTTLED",
            "目标会话处于慢速模式，未自动重试。",
            retriable=True,
            details={"type": type(exc).__name__, "retry_after": exc.seconds},
        )
    if isinstance(
        exc,
        (
            ChatAdminRequiredError,
            ChatAdminInviteRequiredError,
            ChatWriteForbiddenError,
            ChatForbiddenError,
            RightForbiddenError,
        ),
    ):
        return CliError(
            "PERMISSION_DENIED",
            "当前账号没有执行该操作所需的权限。",
            details=_rpc_details(exc) if isinstance(exc, RPCError) else {"type": type(exc).__name__},
        )
    if isinstance(exc, (InviteHashExpiredError, InviteHashInvalidError)):
        return CliError(
            "NOT_FOUND",
            "邀请链接无效或已过期。",
            details={"type": type(exc).__name__},
        )
    if isinstance(exc, (TopicDeletedError, MessageIdInvalidError)):
        return CliError(
            "NOT_FOUND",
            "话题或消息不存在/已删除。",
            details={"type": type(exc).__name__},
        )
    if isinstance(exc, UserPrivacyRestrictedError):
        return CliError(
            "PERMISSION_DENIED",
            "对方隐私设置不允许该操作。",
            details={"type": type(exc).__name__},
        )
    if isinstance(exc, MessageEditTimeExpiredError):
        return CliError(
            "REJECTED",
            "消息已超过可编辑时间。",
            details={"type": type(exc).__name__},
        )
    if isinstance(exc, (ChannelPrivateError, UserNotParticipantError)):
        return CliError(
            "NOT_FOUND",
            "目标不存在、不可见，或当前账号不在其中。",
            details=_rpc_details(exc) if isinstance(exc, RPCError) else {"type": type(exc).__name__},
        )
    if isinstance(exc, (UsernameNotOccupiedError, PeerIdInvalidError)):
        return CliError(
            "NOT_FOUND",
            "找不到该用户名或会话。",
            details=_rpc_details(exc) if isinstance(exc, RPCError) else {"type": type(exc).__name__},
        )
    if isinstance(exc, UsernameInvalidError):
        return CliError(
            "VALIDATION",
            "用户名格式不合法。",
            details={"type": type(exc).__name__},
        )
    if isinstance(exc, UsernameOccupiedError):
        return CliError(
            "REJECTED",
            "该用户名已被占用。",
            details={"type": type(exc).__name__},
        )
    if isinstance(exc, (PasswordHashInvalidError, PhoneCodeInvalidError, PhoneCodeExpiredError)):
        return CliError(
            "VALIDATION",
            "登录验证码或两步验证密码不正确/已过期。",
            details={"type": type(exc).__name__},
        )
    if isinstance(exc, (SessionRevokedError,)):
        return CliError(
            "CONFIRMATION_REQUIRED",
            "当前 CLI 会话已被撤销；请重新运行 auth qr 或 auth login。",
            details={"type": type(exc).__name__},
        )
    if isinstance(exc, SessionPasswordNeededError):
        return CliError(
            "CONFIRMATION_REQUIRED",
            "该账号启用了两步验证，需要密码才能完成登录。",
            details={"type": type(exc).__name__},
        )
    if isinstance(exc, (TimeoutError, ConnectionError, OSError)):
        return CliError(
            "UNAVAILABLE",
            "连接 Telegram 失败或超时。",
            retriable=True,
            details={"type": type(exc).__name__},
        )
    if isinstance(exc, (ValueError, TypeError)):
        return CliError(
            "VALIDATION",
            str(exc)[:500] or "参数校验失败。",
            details={"type": type(exc).__name__},
        )
    if isinstance(exc, RPCError):
        return CliError(
            "REJECTED",
            "Telegram 拒绝了该请求。",
            details=_rpc_details(exc),
        )
    return CliError(
        "INTERNAL",
        "未预期的内部错误。",
        details={"type": type(exc).__name__, "diagnostic": str(exc)[:500]},
    )
