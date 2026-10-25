"""Chat discovery, info and chat-level settings."""
from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from telethon.tl import functions, types

from ..client import TG, kind_detail
from ..envelope import emit_failure, emit_read
from ..errors import CliError
from ..serialize import (
    admin_rights,
    banned_rights,
    dialog_to_dict,
    display_name,
    entity_to_dict,
    iso,
    message_to_dict,
    peer_id,
    username,
)
from .common import add_chat, add_limit, add_write_flags, run_write


def _validate_title(value: str) -> str:
    title = (value or "").strip()
    if not title:
        raise CliError("VALIDATION", "--title 不能为空。")
    if len(title) > 128:
        raise CliError("VALIDATION", "标题不能超过 128 个字符。")
    return title


def _validate_about(value: str) -> str:
    about = value or ""
    if len(about) > 255:
        raise CliError("VALIDATION", "简介不能超过 255 个字符。")
    return about


def _validate_public_username(value: str) -> str:
    import re

    name = (value or "").lstrip("@")
    if not name:
        return ""
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{4,31}", name):
        raise CliError("VALIDATION", "公开用户名必须为 5-32 位、字母开头、只含字母/数字/下划线。")
    return name


async def list_chats(ctx: TG, args) -> dict:
    started = time.monotonic()
    await ctx.connect()
    try:
        await ctx.require_authorized()
        kinds = set(args.type or [])
        search = (args.search or "").casefold().strip()
        archived = None if getattr(args, "all", False) else bool(getattr(args, "archived", False))
        out = []
        async for dialog in ctx.client.iter_dialogs(limit=args.limit, archived=archived):
            entity = dialog.entity
            if entity is None:
                continue
            detail = kind_detail(entity)
            aliases = {detail}
            if detail in {"group", "supergroup"}:
                aliases |= {"group", "supergroup"}
            if getattr(entity, "forum", False):
                aliases.add("forum")
            if kinds and not (aliases & kinds):
                continue
            if search:
                hay = f"{display_name(entity)} {username(entity) or ''}".casefold()
                if search not in hay:
                    continue
            out.append(dialog_to_dict(dialog, include_last=bool(getattr(args, "with_last", False))))
        return emit_read(
            "chats list",
            {"count": len(out), "items": out},
            started,
            **ctx.account_meta(),
        )
    finally:
        await ctx.close()


async def _full_info(ctx: TG, entity) -> dict:
    if isinstance(entity, types.Channel):
        full = await ctx.client(functions.channels.GetFullChannelRequest(channel=entity))
        chat = full.full_chat
        info = {
            "about": getattr(chat, "about", None),
            "participants_count": getattr(chat, "participants_count", None) or getattr(entity, "participants_count", None),
            "online_count": getattr(chat, "online_count", None),
            "linked_chat_id": getattr(chat, "linked_chat_id", None),
            "slowmode_seconds": getattr(chat, "slowmode_seconds", None),
            "slowmode_next_send_date": iso(getattr(chat, "slowmode_next_send_date", None)),
            "admin_rights": admin_rights(getattr(chat, "admin_rights", None)),
            "default_banned_rights": banned_rights(getattr(chat, "default_banned_rights", None)),
            "pinned_msg_id": getattr(chat, "pinned_msg_id", None),
            "available_min_id": getattr(chat, "available_min_id", None),
            "can_view_participants": getattr(chat, "can_view_participants", None),
            "can_set_username": getattr(chat, "can_set_username", None),
        }
        if getattr(chat, "exported_invite", None) is not None:
            info["invite_link"] = chat.exported_invite.link
        return info
    if isinstance(entity, types.Chat):
        full = await ctx.client(functions.messages.GetFullChatRequest(chat_id=entity.id))
        chat = full.full_chat
        info = {
            "about": chat.about,
            "participants_count": getattr(chat, "participants_count", None),
            "pinned_msg_id": chat.pinned_msg_id,
            "can_set_username": getattr(chat, "can_set_username", None),
        }
        if getattr(chat, "exported_invite", None) is not None:
            info["invite_link"] = chat.exported_invite.link
        return info
    if isinstance(entity, types.User):
        full = await ctx.client(functions.users.GetFullUserRequest(id=entity))
        user = full.full_user
        return {
            "about": user.about,
            "common_chats_count": user.common_chats_count,
            "phone_calls_available": user.phone_calls_available,
            "phone_calls_private": user.phone_calls_private,
            "blocked": user.blocked,
            "bot_info": {
                "description": getattr(user.bot_info, "description", None),
                "commands": [
                    {"command": c.command, "description": c.description}
                    for c in getattr(user.bot_info, "commands", None) or []
                ],
            }
            if user.bot_info is not None
            else None,
        }
    return {}


async def info(ctx: TG, args) -> dict:
    started = time.monotonic()
    await ctx.connect()
    try:
        resolved = await ctx.resolve(args.chat)
        entity = resolved.entity
        full = await _full_info(ctx, entity)
        base = entity_to_dict(entity, include_about=full.get("about"))
        base["kind_detail"] = resolved.kind
        return emit_read(
            "chat info",
            {"chat": base, "full": full},
            started,
            **ctx.account_meta(),
        )
    finally:
        await ctx.close()


async def set_title(ctx: TG, args) -> dict:
    await ctx.connect()
    try:
        resolved = await ctx.resolve(args.chat)
        entity = resolved.entity
        title = _validate_title(args.title)

        async def execute():
            if isinstance(entity, types.Channel):
                await ctx.client(functions.channels.EditTitleRequest(channel=entity, title=title))
            elif isinstance(entity, types.Chat):
                await ctx.client(functions.messages.EditChatTitleRequest(chat_id=entity.id, title=title))
            else:
                raise CliError("UNSUPPORTED", "私聊/Bot 不能设置标题。")
            return {"chat": resolved.as_dict(), "title": title}

        return await run_write(
            ctx,
            args,
            command="chat set-title",
            action="chat.set_title",
            params={"chat": resolved.as_dict(), "title": title},
            preview={"before": resolved.title, "after": title},
            execute=execute,
        )
    finally:
        await ctx.close()


async def set_about(ctx: TG, args) -> dict:
    await ctx.connect()
    try:
        resolved = await ctx.resolve(args.chat)
        if not isinstance(resolved.entity, (types.Channel, types.Chat)):
            raise CliError("UNSUPPORTED", "只有群组/频道支持简介。")
        about = _validate_about(args.about)

        async def execute():
            await ctx.client(functions.messages.EditChatAboutRequest(peer=resolved.entity, about=about))
            return {"chat": resolved.as_dict(), "about": about}

        return await run_write(
            ctx,
            args,
            command="chat set-about",
            action="chat.set_about",
            params={"chat": resolved.as_dict(), "about": about},
            preview={"about": about},
            execute=execute,
        )
    finally:
        await ctx.close()


async def set_username(ctx: TG, args) -> dict:
    await ctx.connect()
    try:
        resolved = await ctx.resolve(args.chat)
        if not isinstance(resolved.entity, types.Channel):
            raise CliError("UNSUPPORTED", "只有频道/超级群支持公开用户名。")
        value = _validate_public_username(args.username)

        async def execute():
            await ctx.client(functions.channels.UpdateUsernameRequest(channel=resolved.entity, username=value))
            return {"chat": resolved.as_dict(), "username": value or None}

        return await run_write(
            ctx,
            args,
            command="chat set-username",
            action="chat.set_username",
            params={"chat": resolved.as_dict(), "username": value or None},
            preview={"before": username(resolved.entity), "after": value or None},
            execute=execute,
        )
    finally:
        await ctx.close()


async def set_photo(ctx: TG, args) -> dict:
    await ctx.connect()
    try:
        resolved = await ctx.resolve(args.chat)
        path = Path(args.photo).expanduser()
        if not path.is_file():
            raise CliError("VALIDATION", f"找不到图片文件：{path}")
        if path.stat().st_size > 10 * 1024 * 1024:
            raise CliError("VALIDATION", "头像文件不能超过 10 MB。")

        async def execute():
            uploaded = await ctx.client.upload_file(str(path))
            photo = types.InputChatUploadedPhoto(file=uploaded)
            if isinstance(resolved.entity, types.Channel):
                await ctx.client(functions.channels.EditPhotoRequest(channel=resolved.entity, photo=photo))
            elif isinstance(resolved.entity, types.Chat):
                await ctx.client(functions.messages.EditChatPhotoRequest(chat_id=resolved.entity.id, photo=photo))
            else:
                raise CliError("UNSUPPORTED", "该会话类型不支持设置头像。")
            return {"chat": resolved.as_dict(), "photo": str(path)}

        return await run_write(
            ctx,
            args,
            command="chat set-photo",
            action="chat.set_photo",
            params={"chat": resolved.as_dict(), "photo": str(path)},
            preview={"photo": str(path), "size": path.stat().st_size},
            execute=execute,
        )
    finally:
        await ctx.close()


async def leave(ctx: TG, args) -> dict:
    await ctx.connect()
    try:
        resolved = await ctx.resolve(args.chat)
        entity = resolved.entity
        if isinstance(entity, types.User):
            raise CliError("UNSUPPORTED", "私聊不能 leave；请使用 chat delete-dialog。")
        if isinstance(entity, types.Channel) and entity.broadcast and entity.creator:
            raise CliError(
                "UNSUPPORTED",
                "广播频道的创建者不能直接 leave；请先转让所有权或删除频道。",
            )
        if getattr(entity, "left", False):
            return await run_write(
                ctx,
                args,
                command="chat leave",
                action="chat.leave",
                params={"chat": resolved.as_dict()},
                preview={"already_left": True},
                execute=lambda: _already_left(resolved),
            )

        async def execute():
            if isinstance(entity, types.Channel):
                await ctx.client(functions.channels.LeaveChannelRequest(channel=entity))
            else:
                me = await ctx.client.get_me()
                await ctx.client(
                    functions.messages.DeleteChatUserRequest(
                        chat_id=entity.id, user_id=me.id, revoke_history=False
                    )
                )
            return {"chat": resolved.as_dict(), "left": True}

        return await run_write(
            ctx,
            args,
            command="chat leave",
            action="chat.leave",
            params={"chat": resolved.as_dict()},
            preview={"leave": resolved.title},
            execute=execute,
            destructive=True,
            confirm_label=username(entity) or resolved.title,
        )
    finally:
        await ctx.close()


async def _already_left(resolved):
    return {"chat": resolved.as_dict(), "left": True, "already_left": True}


async def delete_dialog(ctx: TG, args) -> dict:
    await ctx.connect()
    try:
        resolved = await ctx.resolve(args.chat)

        async def execute():
            await ctx.client.delete_dialog(resolved.entity, revoke=bool(args.revoke))
            return {"chat": resolved.as_dict(), "dialog_deleted": True, "revoke": bool(args.revoke)}

        return await run_write(
            ctx,
            args,
            command="chat delete-dialog",
            action="chat.delete_dialog",
            params={"chat": resolved.as_dict(), "revoke": bool(args.revoke)},
            preview={"delete_local_dialog": resolved.title, "revoke": bool(args.revoke)},
            execute=execute,
            destructive=True,
            confirm_label=username(resolved.entity) or resolved.title,
        )
    finally:
        await ctx.close()


async def _notify(ctx: TG, entity, mute_until: datetime | None) -> None:
    await ctx.client(
        functions.account.UpdateNotifySettingsRequest(
            peer=types.InputNotifyPeer(peer=entity),
            settings=types.InputPeerNotifySettings(mute_until=mute_until),
        )
    )


async def mute(ctx: TG, args) -> dict:
    await ctx.connect()
    try:
        resolved = await ctx.resolve(args.chat)
        seconds = int(args.seconds or 0)
        until = datetime(2038, 1, 1, tzinfo=timezone.utc) if seconds <= 0 else datetime.now(timezone.utc) + timedelta(seconds=seconds)

        async def execute():
            await _notify(ctx, resolved.entity, until)
            return {"chat": resolved.as_dict(), "muted_until": iso(until)}

        return await run_write(
            ctx,
            args,
            command="chat mute",
            action="chat.mute",
            params={"chat": resolved.as_dict(), "muted_until": iso(until)},
            preview={"muted_until": iso(until)},
            execute=execute,
        )
    finally:
        await ctx.close()


async def unmute(ctx: TG, args) -> dict:
    await ctx.connect()
    try:
        resolved = await ctx.resolve(args.chat)
        past = datetime.now(timezone.utc) - timedelta(seconds=1)

        async def execute():
            await _notify(ctx, resolved.entity, past)
            return {"chat": resolved.as_dict(), "muted": False}

        return await run_write(
            ctx,
            args,
            command="chat unmute",
            action="chat.unmute",
            params={"chat": resolved.as_dict()},
            preview={"muted": False},
            execute=execute,
        )
    finally:
        await ctx.close()


async def archive(ctx: TG, args) -> dict:
    return await _folder(ctx, args, folder=1)


async def unarchive(ctx: TG, args) -> dict:
    return await _folder(ctx, args, folder=0)


async def _folder(ctx: TG, args, *, folder: int) -> dict:
    await ctx.connect()
    try:
        resolved = await ctx.resolve(args.chat)

        async def execute():
            await ctx.client.edit_folder(resolved.entity, folder=folder)
            return {"chat": resolved.as_dict(), "folder_id": folder}

        command = "chat archive" if folder == 1 else "chat unarchive"
        return await run_write(
            ctx,
            args,
            command=command,
            action=f"chat.{command.split()[-1]}",
            params={"chat": resolved.as_dict()},
            preview={"folder_id": folder},
            execute=execute,
        )
    finally:
        await ctx.close()


async def pin_dialog(ctx: TG, args) -> dict:
    return await _toggle_pin(ctx, args, pinned=True)


async def unpin_dialog(ctx: TG, args) -> dict:
    return await _toggle_pin(ctx, args, pinned=False)


async def _toggle_pin(ctx: TG, args, *, pinned: bool) -> dict:
    await ctx.connect()
    try:
        resolved = await ctx.resolve(args.chat)
        peer = types.InputDialogPeer(peer=resolved.entity)

        async def execute():
            await ctx.client(functions.messages.ToggleDialogPinRequest(peer=peer, pinned=pinned))
            return {"chat": resolved.as_dict(), "pinned": pinned}

        command = "chat pin" if pinned else "chat unpin"
        return await run_write(
            ctx,
            args,
            command=command,
            action=f"chat.{command.split()[-1]}",
            params={"chat": resolved.as_dict()},
            preview={"pinned": pinned},
            execute=execute,
        )
    finally:
        await ctx.close()


async def block(ctx: TG, args) -> dict:
    return await _block(ctx, args, blocked=True)


async def unblock(ctx: TG, args) -> dict:
    return await _block(ctx, args, blocked=False)


async def _block(ctx: TG, args, *, blocked: bool) -> dict:
    await ctx.connect()
    try:
        resolved = await ctx.resolve(args.chat, kinds=["private", "bot"])
        entity = resolved.entity

        async def execute():
            if blocked:
                await ctx.client(functions.contacts.BlockRequest(id=entity))
            else:
                await ctx.client(functions.contacts.UnblockRequest(id=entity))
            return {"chat": resolved.as_dict(), "blocked": blocked}

        command = "chat block" if blocked else "chat unblock"
        return await run_write(
            ctx,
            args,
            command=command,
            action=f"chat.{command.split()[-1]}",
            params={"chat": resolved.as_dict()},
            preview={"blocked": blocked},
            execute=execute,
            destructive=blocked,
            confirm_label=username(entity) or resolved.title,
        )
    finally:
        await ctx.close()


async def set_slowmode(ctx: TG, args) -> dict:
    await ctx.connect()
    try:
        resolved = await ctx.resolve(args.chat, kinds=["group", "supergroup"])
        if not isinstance(resolved.entity, types.Channel):
            raise CliError("UNSUPPORTED", "普通群不支持慢速模式；请使用超级群。")
        seconds = int(args.seconds)
        allowed = {0, 5, 10, 30, 60, 300, 900, 3600}
        if seconds not in allowed:
            raise CliError(
                "VALIDATION",
                "Telegram 只接受这些慢速模式值（秒）：0, 5, 10, 30, 60, 300, 900, 3600。",
                details={"allowed": sorted(allowed)},
            )

        async def execute():
            await ctx.client(
                functions.channels.ToggleSlowModeRequest(channel=resolved.entity, seconds=seconds)
            )
            return {"chat": resolved.as_dict(), "slowmode_seconds": seconds}

        return await run_write(
            ctx,
            args,
            command="chat set-slowmode",
            action="chat.set_slowmode",
            params={"chat": resolved.as_dict(), "seconds": seconds},
            preview={"chat": resolved.as_dict(), "seconds": seconds},
            execute=execute,
        )
    finally:
        await ctx.close()


async def set_signatures(ctx: TG, args) -> dict:
    await ctx.connect()
    try:
        resolved = await ctx.resolve(args.chat, kinds=["channel"])
        if not isinstance(resolved.entity, types.Channel) or not resolved.entity.broadcast:
            raise CliError("UNSUPPORTED", "只有广播频道支持签名设置。")
        enabled = _require_bool(args.enabled, "--enabled/--disabled")

        async def execute():
            await ctx.client(
                functions.channels.ToggleSignaturesRequest(
                    channel=resolved.entity, signatures_enabled=enabled
                )
            )
            return {"chat": resolved.as_dict(), "signatures_enabled": enabled}

        return await run_write(
            ctx,
            args,
            command="chat set-signatures",
            action="chat.set_signatures",
            params={"chat": resolved.as_dict(), "enabled": enabled},
            preview={"chat": resolved.as_dict(), "enabled": enabled},
            execute=execute,
        )
    finally:
        await ctx.close()


def _require_bool(value, flag: str) -> bool:
    if value is None:
        raise CliError("VALIDATION", f"需要 {flag}。")
    return bool(value)


async def set_discussion(ctx: TG, args) -> dict:
    await ctx.connect()
    try:
        broadcast = await ctx.resolve(args.chat, kinds=["channel"])
        if not isinstance(broadcast.entity, types.Channel) or not broadcast.entity.broadcast:
            raise CliError("UNSUPPORTED", "只有广播频道可以设置关联讨论群。")
        group = None
        if not getattr(args, "clear", False):
            group = await ctx.resolve(args.group, kinds=["group", "supergroup"])
            if not isinstance(group.entity, types.Channel) or not group.entity.megagroup:
                raise CliError("UNSUPPORTED", "讨论群必须是超级群。")

        async def execute():
            target = types.InputChannelEmpty() if getattr(args, "clear", False) else group.entity
            await ctx.client(
                functions.channels.SetDiscussionGroupRequest(
                    broadcast=broadcast.entity, group=target
                )
            )
            return {
                "channel": broadcast.as_dict(),
                "discussion_group": None if group is None else group.as_dict(),
                "cleared": bool(getattr(args, "clear", False)),
            }

        params = {
            "channel": broadcast.as_dict(),
            "group": None if group is None else group.as_dict(),
            "clear": bool(getattr(args, "clear", False)),
        }
        return await run_write(
            ctx,
            args,
            command="chat set-discussion",
            action="chat.set_discussion",
            params=params,
            preview=params,
            execute=execute,
        )
    finally:
        await ctx.close()
