"""Telethon connection, authorization and target resolution."""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Iterable

from telethon import TelegramClient
from telethon.tl import types

from .config import Settings, load_settings, mask_phone
from .errors import CliError
from .serialize import display_name, entity_kind, is_forum, peer_id, username


@dataclass
class Resolved:
    entity: Any
    ref: str
    kind: str
    title: str
    peer_id: int | None
    is_forum: bool

    def as_dict(self) -> dict:
        return {
            "ref": self.ref,
            "id": getattr(self.entity, "id", None),
            "peer_id": self.peer_id,
            "kind": self.kind,
            "title": self.title,
            "username": username(self.entity),
            "is_forum": self.is_forum,
        }


def kind_detail(entity: Any) -> str:
    if isinstance(entity, types.User):
        return "bot" if entity.bot else "private"
    if isinstance(entity, types.Chat):
        return "group"
    if isinstance(entity, types.Channel):
        if entity.broadcast:
            return "channel"
        return "supergroup"
    return "unknown"


def _matches_kind(entity: Any, kinds: Iterable[str] | None) -> bool:
    if not kinds:
        return True
    detail = kind_detail(entity)
    kind = entity_kind(entity)
    aliases = {detail, kind}
    if detail == "supergroup":
        aliases.add("group")
    if detail == "group":
        aliases.add("supergroup")
    if is_forum(entity):
        aliases.add("forum")
    return bool(aliases & set(kinds))


def _parse_link(ref: str) -> str:
    ref = ref.strip()
    m = re.match(r"^tg://resolve\?domain=(?P<name>[A-Za-z0-9_]+)", ref)
    if m:
        return "@" + m.group("name")
    ref = ref.split("?", 1)[0]
    # Private supergroup message links must be checked before public names:
    # t.me/c/<internal_id>/<msg_id>.
    m = re.match(r"^(?:https?://)?(?:t\.me|telegram\.me|telegram\.dog)/c/(?P<id>\d+)(?:/\d+)?/?$", ref)
    if m:
        return str(int(f"-100{m.group('id')}"))
    # Public username links, optionally with a message id: t.me/name/123.
    m = re.match(
        r"^(?:https?://)?(?:t\.me|telegram\.me|telegram\.dog)/(?:s/)?(?P<name>[A-Za-z][A-Za-z0-9_]{2,})(?:/\d+)?/?$",
        ref,
    )
    if m:
        return "@" + m.group("name")
    return ref


class TG:
    def __init__(
        self,
        settings: Settings | None = None,
        *,
        timeout: float = 20.0,
        account_name: str | None = None,
    ) -> None:
        self.settings = settings or load_settings()
        self.timeout = timeout
        self.account_name = account_name
        self.client: TelegramClient | None = None
        self.me: Any = None

    async def connect(self) -> "TG":
        self.client = TelegramClient(
            str(self.settings.session_path),
            self.settings.api_id,
            self.settings.api_hash,
            timeout=self.timeout,
            connection_retries=2,
            retry_delay=1,
            auto_reconnect=True,
            flood_sleep_threshold=0,
            device_model="telegram-native",
            system_version="macOS",
            app_version="0.1.0",
            lang_code="en",
            receive_updates=False,
            proxy=self.settings.proxy,
        )
        self.client.parse_mode = None
        await self.client.connect()
        try:
            if self.settings.session_path.exists():
                self.settings.session_path.chmod(0o600)
        except OSError:
            pass
        return self

    async def close(self) -> None:
        if self.client is not None:
            await self.client.disconnect()
            self.client = None

    async def authorized(self) -> bool:
        assert self.client is not None
        return bool(await self.client.is_user_authorized())

    async def require_authorized(self) -> Any:
        assert self.client is not None
        if not await self.authorized():
            raise CliError(
                "CONFIRMATION_REQUIRED",
                "尚未授权 Telegram 会话；请先运行 `telegram-native auth login` 或 `auth qr`。",
                details={"authorized": False},
            )
        if self.me is None:
            self.me = await self.client.get_me()
        return self.me

    def account_meta(self) -> dict:
        me = self.me
        return {
            "account": self.account_name,
            "account_id": getattr(me, "id", None),
            "account_username": getattr(me, "username", None),
            "phone_masked": mask_phone(getattr(me, "phone", None)),
            "dc_id": getattr(getattr(self.client, "session", None), "dc_id", None),
        }

    async def resolve(self, ref: str, *, kinds: Iterable[str] | None = None) -> Resolved:
        assert self.client is not None
        await self.require_authorized()
        original = ref
        value = _parse_link(ref)
        lowered = value.strip().lower()
        if lowered in {"me", "self", "saved"}:
            entity = self.me
            if not _matches_kind(entity, kinds):
                raise CliError("TARGET_AMBIGUOUS", f"目标 `{original}` 不是请求的类型。")
            return self._resolved(entity, original)
        if lowered in {"botfather", "@botfather"}:
            entity = await self.client.get_entity("BotFather")
            return self._resolved(entity, original)

        if value.startswith("@") or re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{3,}", value):
            explicit = value.startswith("@")
            name = value[1:] if explicit else value
            try:
                entity = await self.client.get_entity(name)
            except Exception as exc:
                if explicit:
                    raise CliError(
                        "NOT_FOUND",
                        f"找不到 @{name}。",
                        details={"ref": original, "type": type(exc).__name__},
                    ) from None
                # A bare single word can be a title (e.g. "Ops"). Fall through
                # to the title matcher instead of treating it as a username.
                entity = None
            if entity is not None:
                if not _matches_kind(entity, kinds):
                    if explicit:
                        raise CliError(
                            "TARGET_AMBIGUOUS",
                            f"@{name} 不是请求的类型。",
                            details={"ref": original},
                        )
                else:
                    return self._resolved(entity, original)

        if re.fullmatch(r"-?\d+", value):
            numeric = int(value)
            entity = None
            for candidate in (numeric, int(f"-100{numeric}") if numeric > 0 else numeric):
                try:
                    entity = await self.client.get_entity(candidate)
                    break
                except Exception:
                    continue
            if entity is None:
                raise CliError(
                    "NOT_FOUND",
                    f"找不到 numeric id {numeric}（可能需要先与目标有过会话）。",
                    details={"ref": original},
                )
            if not _matches_kind(entity, kinds):
                raise CliError("TARGET_AMBIGUOUS", f"{numeric} 不是请求的类型。", details={"ref": original})
            return self._resolved(entity, original)

        # Exact/partial title search. Exact matches win; partial only if unique.
        exact: list[Any] = []
        partial: list[Any] = []
        needle = value.casefold().strip()
        async for dialog in self.client.iter_dialogs(limit=None):
            entity = dialog.entity
            if entity is None or not _matches_kind(entity, kinds):
                continue
            title = display_name(entity)
            uname = username(entity) or ""
            hay = f"{title} {uname}".casefold()
            if hay.strip() == needle or uname.casefold() == needle:
                exact.append(entity)
            elif needle in hay:
                partial.append(entity)
        matches = exact or partial
        if len(matches) == 1:
            return self._resolved(matches[0], original)
        if len(matches) > 1:
            candidates = [
                {
                    "id": getattr(e, "id", None),
                    "peer_id": peer_id(e),
                    "kind": kind_detail(e),
                    "title": display_name(e),
                    "username": username(e),
                }
                for e in matches[:20]
            ]
            raise CliError(
                "TARGET_AMBIGUOUS",
                f"`{original}` 匹配到多个会话，请改用 @username 或 peer_id。",
                details={"candidates": candidates, "match": "exact" if exact else "partial"},
            )
        raise CliError("NOT_FOUND", f"找不到会话 `{original}`。", details={"ref": original})

    def _resolved(self, entity: Any, ref: str) -> Resolved:
        return Resolved(
            entity=entity,
            ref=ref,
            kind=kind_detail(entity),
            title=display_name(entity),
            peer_id=peer_id(entity),
            is_forum=is_forum(entity),
        )

    async def resolve_many(self, refs: Iterable[str], *, kinds: Iterable[str] | None = None) -> list[Resolved]:
        out = []
        for ref in refs:
            if ref.strip():
                out.append(await self.resolve(ref.strip(), kinds=kinds))
        return out
