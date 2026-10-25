"""Group/channel creation, deletion and invite links."""
from __future__ import annotations

import re
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from telethon.errors import InviteRequestSentError, UserAlreadyParticipantError
from telethon.tl import functions, types

from ..client import TG
from ..envelope import emit_read
from ..errors import CliError
from ..serialize import entity_to_dict, iso, username
from .common import add_chat, add_write_flags, csv, run_write
from .invites import normalize_invite_link


def _extract_invite_hash(link: str) -> str | None:
    link = link.strip()
    m = re.match(r"^(?:https?://)?t\.me/(?:joinchat/|\+)(?P<hash>[A-Za-z0-9_-]+)", link)
    if m:
        return m.group("hash")
    m = re.match(r"^tg://join\?invite=(?P<hash>[A-Za-z0-9_-]+)", link)
    if m:
        return m.group("hash")
    return None


async def _resolve_users(ctx: TG, values: list[str]):
    users = []
    for value in values:
        resolved = await ctx.resolve(value, kinds=["private", "bot"])
        if not isinstance(resolved.entity, types.User):
            raise CliError("VALIDATION", f"`{value}` 不是用户。")
        users.append(resolved)
    return users


async def create_group(ctx: TG, args) -> dict:
    await ctx.connect()
    try:
        title = (args.title or "").strip()
        if not title:
            raise CliError("VALIDATION", "--title 不能为空。")
        users = await _resolve_users(ctx, csv(args.users))
        if args.basic and not users:
            raise CliError("VALIDATION", "创建普通群至少需要一个用户。")
        if args.basic and args.forum:
            raise CliError("VALIDATION", "普通群不支持话题；请去掉 --basic 或 --forum。")

        async def execute():
            if args.basic:
                result = await ctx.client(
                    functions.messages.CreateChatRequest(users=[u.entity for u in users], title=title)
                )
                # Layer 229 returns messages.InvitedUsers, not Updates.
                updates = getattr(result, "updates", result)
                chat = updates.chats[0]
            else:
                result = await ctx.client(
                    functions.channels.CreateChannelRequest(
                        title=title,
                        about=args.about or "",
                        megagroup=True,
                        forum=bool(args.forum),
                    )
                )
                chat = result.chats[0]
            warnings = []
            if args.basic and (args.username or args.photo):
                warnings.append(
                    {
                        "step": "unsupported_options",
                        "error": "普通群不支持 --username/--photo；这两项已忽略。",
                    }
                )
            if not args.basic and users:
                try:
                    await ctx.client(
                        functions.channels.InviteToChannelRequest(
                            channel=chat, users=[u.entity for u in users]
                        )
                    )
                except Exception as exc:  # group exists; report the partial step
                    warnings.append({"step": "invite_users", "error": str(exc)[:300]})
            if not args.basic and args.username:
                try:
                    await ctx.client(
                        functions.channels.UpdateUsernameRequest(
                            channel=chat, username=args.username.lstrip("@")
                        )
                    )
                except Exception as exc:  # keep the created group, report the partial
                    warnings.append({"step": "set_username", "error": str(exc)[:300]})
            if not args.basic and args.photo:
                try:
                    path = Path(args.photo).expanduser()
                    uploaded = await ctx.client.upload_file(str(path))
                    await ctx.client(
                        functions.channels.EditPhotoRequest(
                            channel=chat,
                            photo=types.InputChatUploadedPhoto(file=uploaded),
                        )
                    )
                except Exception as exc:
                    warnings.append({"step": "set_photo", "error": str(exc)[:300]})
            data = entity_to_dict(chat)
            if warnings:
                data["warnings"] = warnings
            return data

        params = {
            "title": title,
            "about": args.about or "",
            "forum": bool(args.forum),
            "basic": bool(args.basic),
            "users": [u.as_dict() for u in users],
            "username": args.username,
        }
        return await run_write(
            ctx,
            args,
            command="group create",
            action="group.create",
            params=params,
            preview=params,
            execute=execute,
        )
    finally:
        await ctx.close()


async def create_channel(ctx: TG, args) -> dict:
    await ctx.connect()
    try:
        title = (args.title or "").strip()
        if not title:
            raise CliError("VALIDATION", "--title 不能为空。")
        users = await _resolve_users(ctx, csv(args.users))

        async def execute():
            result = await ctx.client(
                functions.channels.CreateChannelRequest(
                    title=title,
                    about=args.about or "",
                    broadcast=True,
                    megagroup=False,
                )
            )
            channel = result.chats[0]
            warnings = []
            if args.username:
                try:
                    await ctx.client(
                        functions.channels.UpdateUsernameRequest(
                            channel=channel, username=args.username.lstrip("@")
                        )
                    )
                except Exception as exc:
                    warnings.append({"step": "set_username", "error": str(exc)[:300]})
            if args.photo:
                try:
                    path = Path(args.photo).expanduser()
                    uploaded = await ctx.client.upload_file(str(path))
                    await ctx.client(
                        functions.channels.EditPhotoRequest(
                            channel=channel,
                            photo=types.InputChatUploadedPhoto(file=uploaded),
                        )
                    )
                except Exception as exc:
                    warnings.append({"step": "set_photo", "error": str(exc)[:300]})
            if users:
                try:
                    await ctx.client(
                        functions.channels.InviteToChannelRequest(
                            channel=channel, users=[u.entity for u in users]
                        )
                    )
                except Exception as exc:  # channel exists; report the partial step
                    warnings.append({"step": "invite_users", "error": str(exc)[:300]})
            data = entity_to_dict(channel)
            if warnings:
                data["warnings"] = warnings
            return data

        params = {
            "title": title,
            "about": args.about or "",
            "username": args.username,
            "users": [u.as_dict() for u in users],
        }
        return await run_write(
            ctx,
            args,
            command="channel create",
            action="channel.create",
            params=params,
            preview=params,
            execute=execute,
        )
    finally:
        await ctx.close()


async def delete_group_or_channel(ctx: TG, args) -> dict:
    await ctx.connect()
    try:
        resolved = await ctx.resolve(args.chat, kinds=["group", "supergroup", "channel"])
        entity = resolved.entity
        if isinstance(entity, types.Chat):
            raise CliError(
                "UNSUPPORTED",
                "普通群（basic group）不能通过 DeleteChannel 删除；请用 `chat leave` 退出，"
                "或先转为超级群再删除。",
                details={"chat": resolved.as_dict()},
            )

        async def execute():
            await ctx.client(functions.channels.DeleteChannelRequest(channel=entity))
            return {"chat": resolved.as_dict(), "deleted": True}

        return await run_write(
            ctx,
            args,
            command="chat delete",
            action="chat.delete",
            params={"chat": resolved.as_dict()},
            preview={"delete_for_everyone": resolved.title},
            execute=execute,
            destructive=True,
            confirm_label=username(entity) or resolved.title,
        )
    finally:
        await ctx.close()


async def invite_link(ctx: TG, args) -> dict:
    await ctx.connect()
    try:
        resolved = await ctx.resolve(args.chat, kinds=["group", "supergroup", "channel"])
        if args.request_needed and args.usage_limit is not None:
            raise CliError(
                "VALIDATION",
                "Telegram 不允许审批链接同时设置 usage_limit；请二选一。",
            )
        expire_date = None
        if args.expire_days:
            expire_date = datetime.now(timezone.utc) + timedelta(days=int(args.expire_days))

        async def execute():
            result = await ctx.client(
                functions.messages.ExportChatInviteRequest(
                    peer=resolved.entity,
                    title=args.title,
                    expire_date=expire_date,
                    usage_limit=args.usage_limit,
                    request_needed=bool(args.request_needed),
                )
            )
            return {
                "chat": resolved.as_dict(),
                "link": result.link,
                "expire_date": iso(getattr(result, "expire_date", None)),
                "usage_limit": getattr(result, "usage_limit", None),
                "request_needed": bool(getattr(result, "request_needed", False)),
            }

        return await run_write(
            ctx,
            args,
            command="invite link",
            action="invite.link",
            params={
                "chat": resolved.as_dict(),
                "expire_days": args.expire_days,
                "usage_limit": args.usage_limit,
                "request_needed": bool(args.request_needed),
                "title": args.title,
            },
            preview={"chat": resolved.as_dict(), "expire_date": iso(expire_date)},
            execute=execute,
        )
    finally:
        await ctx.close()


async def invite_revoke(ctx: TG, args) -> dict:
    await ctx.connect()
    try:
        resolved = await ctx.resolve(args.chat, kinds=["group", "supergroup", "channel"])
        link = normalize_invite_link(args.link)

        async def execute():
            await ctx.client(
                functions.messages.EditExportedChatInviteRequest(
                    peer=resolved.entity, link=link, revoked=True
                )
            )
            return {"chat": resolved.as_dict(), "link": link, "revoked": True}

        return await run_write(
            ctx,
            args,
            command="invite revoke",
            action="invite.revoke",
            params={"chat": resolved.as_dict(), "link": link},
            preview={"revoke": link},
            execute=execute,
            destructive=True,
            confirm_label=args.link,
        )
    finally:
        await ctx.close()


async def invite_join(ctx: TG, args) -> dict:
    await ctx.connect()
    try:
        link = args.link.strip()
        invite_hash = _extract_invite_hash(link)

        async def execute():
            if invite_hash:
                try:
                    result = await ctx.client(
                        functions.messages.ImportChatInviteRequest(hash=invite_hash)
                    )
                except InviteRequestSentError:
                    return {
                        "joined": False,
                        "request_sent": True,
                        "invite_hash": invite_hash,
                        "chat": None,
                    }
                except UserAlreadyParticipantError:
                    return {
                        "joined": True,
                        "already_member": True,
                        "invite_hash": invite_hash,
                        "chat": None,
                    }
                # Layer 229 returns messages.ChatInviteJoinResult, whose
                # `.updates` is the Updates container holding chats/users.
                updates = getattr(result, "updates", result)
                chats = getattr(updates, "chats", None) or []
                chat = chats[0] if chats else None
                return {
                    "joined": True,
                    "request_sent": False,
                    "invite_hash": invite_hash,
                    "chat": entity_to_dict(chat) if chat is not None else None,
                }
            resolved = await ctx.resolve(link, kinds=["group", "supergroup", "channel"])
            try:
                result = await ctx.client(
                    functions.channels.JoinChannelRequest(channel=resolved.entity)
                )
            except InviteRequestSentError:
                return {
                    "joined": False,
                    "request_sent": True,
                    "invite_hash": None,
                    "chat": resolved.as_dict(),
                }
            except UserAlreadyParticipantError:
                return {
                    "joined": True,
                    "already_member": True,
                    "invite_hash": None,
                    "chat": resolved.as_dict(),
                }
            chats = getattr(result, "chats", None) or []
            chat = chats[0] if chats else resolved.entity
            return {
                "joined": True,
                "request_sent": False,
                "chat": entity_to_dict(chat) if chat is not None else resolved.as_dict(),
            }

        return await run_write(
            ctx,
            args,
            command="invite join",
            action="invite.join",
            params={"link": link},
            preview={"link": link, "invite_hash": invite_hash},
            execute=execute,
        )
    finally:
        await ctx.close()


async def invite_info(ctx: TG, args) -> dict:
    started = time.monotonic()
    await ctx.connect()
    try:
        invite_hash = _extract_invite_hash(args.link)
        if invite_hash:
            result = await ctx.client(functions.messages.CheckChatInviteRequest(hash=invite_hash))
            chat = getattr(result, "chat", None)
            data = {
                "kind": type(result).__name__,
                "title": getattr(result, "title", None) or (entity_to_dict(chat)["title"] if chat else None),
                "about": getattr(result, "about", None),
                "participants_count": getattr(result, "participants_count", None),
                "request_needed": bool(getattr(result, "request_needed", False)),
                "expires": iso(getattr(result, "expires", None)),
                "usage_limit": getattr(result, "usage_limit", None),
                "chat": entity_to_dict(chat) if chat is not None else None,
            }
            return emit_read("invite info", data, started, **ctx.account_meta())
        resolved = await ctx.resolve(args.link, kinds=["group", "supergroup", "channel"])
        return emit_read(
            "invite info",
            {"kind": "public", "chat": entity_to_dict(resolved.entity)},
            started,
            **ctx.account_meta(),
        )
    finally:
        await ctx.close()


async def set_join_request(ctx: TG, args) -> dict:
    return await _toggle_join(ctx, args, join_request=True)


async def set_join_to_send(ctx: TG, args) -> dict:
    return await _toggle_join(ctx, args, join_request=False)


async def _toggle_join(ctx: TG, args, *, join_request: bool) -> dict:
    await ctx.connect()
    try:
        resolved = await ctx.resolve(args.chat, kinds=["group", "supergroup"])
        if not isinstance(resolved.entity, types.Channel) or not resolved.entity.megagroup:
            raise CliError("UNSUPPORTED", "只有超级群支持该设置。")
        if args.enabled is None:
            raise CliError("VALIDATION", "需要 --enabled 或 --disabled。")

        async def execute():
            if join_request:
                await ctx.client(
                    functions.channels.ToggleJoinRequestRequest(
                        channel=resolved.entity,
                        enabled=bool(args.enabled),
                        apply_to_invites=bool(args.apply_to_invites),
                    )
                )
            else:
                await ctx.client(
                    functions.channels.ToggleJoinToSendRequest(
                        channel=resolved.entity, enabled=bool(args.enabled)
                    )
                )
            return {
                "chat": resolved.as_dict(),
                "join_request": bool(args.enabled) if join_request else None,
                "join_to_send": bool(args.enabled) if not join_request else None,
            }

        command = "group set-join-request" if join_request else "group set-join-to-send"
        action = "group.set_join_request" if join_request else "group.set_join_to_send"
        return await run_write(
            ctx,
            args,
            command=command,
            action=action,
            params={"chat": resolved.as_dict(), "enabled": bool(args.enabled)},
            preview={"chat": resolved.as_dict(), "enabled": bool(args.enabled)},
            execute=execute,
        )
    finally:
        await ctx.close()


async def migrate_group(ctx: TG, args) -> dict:
    """Migrate a basic group to a supergroup (irreversible)."""
    await ctx.connect()
    try:
        resolved = await ctx.resolve(args.chat, kinds=["group"])
        if not isinstance(resolved.entity, types.Chat):
            raise CliError(
                "UNSUPPORTED",
                "目标已经是超级群/频道；只有 basic group 需要迁移。",
            )

        async def execute():
            result = await ctx.client(
                functions.messages.MigrateChatRequest(chat_id=resolved.entity.id)
            )
            new_chat = next(
                (c for c in (getattr(result, "chats", None) or []) if isinstance(c, types.Channel)),
                None,
            )
            if new_chat is None:
                old = await ctx.client.get_entity(resolved.entity.id)
                migrated = getattr(old, "migrated_to", None)
                if migrated is not None:
                    new_chat = await ctx.client.get_entity(migrated)
            return {
                "old": resolved.as_dict(),
                "new": entity_to_dict(new_chat) if new_chat is not None else None,
                "migrated": True,
            }

        params = {"chat": resolved.as_dict()}
        return await run_write(
            ctx,
            args,
            command="group migrate",
            action="group.migrate",
            params=params,
            preview={"migrate_to_supergroup": resolved.title, "irreversible": True},
            execute=execute,
            destructive=True,
            confirm_label=resolved.title,
        )
    finally:
        await ctx.close()
