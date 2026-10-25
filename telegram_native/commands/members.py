"""Members and administrators."""
from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone

from telethon.tl import functions, types

from ..client import TG
from ..envelope import emit_read
from ..errors import CliError
from ..serialize import participant_to_dict, user_to_dict
from .common import add_chat, add_limit, csv, run_write


def _is_channel(entity) -> bool:
    return isinstance(entity, types.Channel)


async def _users(ctx: TG, values: list[str]):
    out = []
    for value in values:
        resolved = await ctx.resolve(value, kinds=["private", "bot"])
        if not isinstance(resolved.entity, types.User):
            raise CliError("VALIDATION", f"`{value}` 不是用户。")
        out.append(resolved)
    return out


async def _chat_participants(ctx: TG, entity) -> list[dict]:
    full = await ctx.client(functions.messages.GetFullChatRequest(chat_id=entity.id))
    participants = getattr(full.full_chat.participants, "participants", None) or []
    users = {u.id: u for u in (getattr(full, "users", None) or [])}
    out = []
    for participant in participants:
        user = users.get(participant.user_id) or await ctx.client.get_entity(participant.user_id)
        item = user_to_dict(user)
        item["participant_type"] = type(participant).__name__.replace("ChatParticipant", "")
        if isinstance(participant, types.ChatParticipantAdmin):
            item["admin_rights"] = {"is_admin": True}
        if isinstance(participant, types.ChatParticipantCreator):
            item["admin_rights"] = {"is_admin": True, "creator": True}
        out.append(item)
    return out


async def list_members(ctx: TG, args) -> dict:
    started = time.monotonic()
    await ctx.connect()
    try:
        resolved = await ctx.resolve(args.chat, kinds=["group", "supergroup", "channel"])
        entity = resolved.entity
        kind = args.filter or "all"
        search = args.search or ""
        if isinstance(entity, types.Chat):
            if kind in {"recent", "kicked"}:
                raise CliError(
                    "UNSUPPORTED",
                    "普通群没有 recent/kicked 成员过滤器；请用 all/admins/bots。",
                )
            items = await _chat_participants(ctx, entity)
            if search:
                needle = search.casefold()
                items = [
                    i
                    for i in items
                    if needle in f"{i.get('first_name') or ''} {i.get('last_name') or ''} {i.get('username') or ''}".casefold()
                ]
            if kind == "admins":
                items = [i for i in items if i.get("admin_rights")]
            elif kind == "bots":
                items = [i for i in items if i.get("bot")]
            items = items[: args.limit]
        else:
            participant_filter = {
                "all": types.ChannelParticipantsSearch(q=search),
                "admins": types.ChannelParticipantsAdmins(),
                "bots": types.ChannelParticipantsBots(),
                "recent": types.ChannelParticipantsRecent(),
                "kicked": types.ChannelParticipantsKicked(q=search),
            }.get(kind)
            if participant_filter is None:
                raise CliError("VALIDATION", f"未知 filter：{kind}")
            items = []
            offset = 0
            while len(items) < args.limit:
                batch_limit = min(200, args.limit - len(items))
                result = await ctx.client(
                    functions.channels.GetParticipantsRequest(
                        channel=entity,
                        filter=participant_filter,
                        offset=offset,
                        limit=batch_limit,
                        hash=0,
                    )
                )
                users = {u.id: u for u in (getattr(result, "users", None) or [])}
                batch = getattr(result, "participants", None) or []
                for participant in batch:
                    uid = getattr(participant, "user_id", None)
                    if uid is None:
                        uid = getattr(getattr(participant, "peer", None), "user_id", None)
                    user = users.get(uid) or getattr(participant, "user", None)
                    items.append(participant_to_dict(participant, user=user))
                if len(batch) < batch_limit:
                    break
                offset += len(batch)
        return emit_read(
            "members list",
            {"chat": resolved.as_dict(), "count": len(items), "items": items},
            started,
            **ctx.account_meta(),
        )
    finally:
        await ctx.close()


async def add_members(ctx: TG, args) -> dict:
    await ctx.connect()
    try:
        resolved = await ctx.resolve(args.chat, kinds=["group", "supergroup", "channel"])
        users = await _users(ctx, csv(args.users))
        if not users:
            raise CliError("VALIDATION", "--users 不能为空。")
        if (
            isinstance(resolved.entity, types.Channel)
            and resolved.entity.broadcast
            and any(getattr(u.entity, "bot", False) for u in users)
        ):
            raise CliError(
                "UNSUPPORTED",
                "广播频道里的 Bot 只能通过 `admins promote` 设为管理员，不能作为普通成员邀请。",
            )

        async def execute():
            missing = []
            if _is_channel(resolved.entity):
                result = await ctx.client(
                    functions.channels.InviteToChannelRequest(
                        channel=resolved.entity, users=[u.entity for u in users]
                    )
                )
                missing.extend(getattr(result, "missing_invitees", None) or [])
            else:
                for user in users:
                    result = await ctx.client(
                        functions.messages.AddChatUserRequest(
                            chat_id=resolved.entity.id, user_id=user.entity, fwd_limit=10
                        )
                    )
                    missing.extend(getattr(result, "missing_invitees", None) or [])
            missing_ids = [getattr(m, "user_id", m) for m in missing]
            data = {
                "chat": resolved.as_dict(),
                "added": [u.as_dict() for u in users],
                "missing_invitees": missing_ids,
            }
            if missing_ids:
                data["warnings"] = [
                    {
                        "step": "invite_users",
                        "error": "部分用户未被加入（隐私设置/需要先私聊/权限限制）。",
                        "missing_invitees": missing_ids,
                    }
                ]
            return data

        return await run_write(
            ctx,
            args,
            command="members add",
            action="members.add",
            params={"chat": resolved.as_dict(), "users": [u.as_dict() for u in users]},
            preview={"chat": resolved.as_dict(), "users": [u.as_dict() for u in users]},
            execute=execute,
        )
    finally:
        await ctx.close()


async def remove_members(ctx: TG, args) -> dict:
    await ctx.connect()
    try:
        resolved = await ctx.resolve(args.chat, kinds=["group", "supergroup", "channel"])
        users = await _users(ctx, csv(args.users))
        if not users:
            raise CliError("VALIDATION", "--users 不能为空。")

        async def execute():
            for user in users:
                if _is_channel(resolved.entity):
                    await ctx.client.kick_participant(resolved.entity, user.entity)
                else:
                    await ctx.client(
                        functions.messages.DeleteChatUserRequest(
                            chat_id=resolved.entity.id, user_id=user.entity, revoke_history=False
                        )
                    )
            return {"chat": resolved.as_dict(), "removed": [u.as_dict() for u in users]}

        return await run_write(
            ctx,
            args,
            command="members remove",
            action="members.remove",
            params={"chat": resolved.as_dict(), "users": [u.as_dict() for u in users]},
            preview={"chat": resolved.as_dict(), "users": [u.as_dict() for u in users]},
            execute=execute,
            destructive=True,
            confirm_label=",".join(u.ref for u in users),
        )
    finally:
        await ctx.close()


def _until(days: int | None):
    if not days:
        return None
    return datetime.now(timezone.utc) + timedelta(days=days)


async def ban_members(ctx: TG, args) -> dict:
    await ctx.connect()
    try:
        resolved = await ctx.resolve(args.chat, kinds=["supergroup", "channel"])
        users = await _users(ctx, csv(args.users))
        if not users:
            raise CliError("VALIDATION", "--users 不能为空。")
        if args.days is not None and args.days <= 0:
            raise CliError("VALIDATION", "--days 必须是正整数；不填表示永久。")
        until = _until(args.days)

        async def execute():
            for user in users:
                await ctx.client(
                    functions.channels.EditBannedRequest(
                        channel=resolved.entity,
                        participant=user.entity,
                        banned_rights=types.ChatBannedRights(
                            until_date=until, view_messages=True
                        ),
                    )
                )
            return {
                "chat": resolved.as_dict(),
                "banned": [u.as_dict() for u in users],
                "until_date": until.isoformat() if until else None,
            }

        return await run_write(
            ctx,
            args,
            command="members ban",
            action="members.ban",
            params={"chat": resolved.as_dict(), "users": [u.as_dict() for u in users], "days": args.days},
            preview={"chat": resolved.as_dict(), "users": [u.as_dict() for u in users], "days": args.days},
            execute=execute,
            destructive=True,
            confirm_label=",".join(u.ref for u in users),
        )
    finally:
        await ctx.close()


async def unban_members(ctx: TG, args) -> dict:
    await ctx.connect()
    try:
        resolved = await ctx.resolve(args.chat, kinds=["supergroup", "channel"])
        users = await _users(ctx, csv(args.users))
        if not users:
            raise CliError("VALIDATION", "--users 不能为空。")

        async def execute():
            for user in users:
                await ctx.client(
                    functions.channels.EditBannedRequest(
                        channel=resolved.entity,
                        participant=user.entity,
                        banned_rights=types.ChatBannedRights(until_date=None),
                    )
                )
            return {"chat": resolved.as_dict(), "unbanned": [u.as_dict() for u in users]}

        return await run_write(
            ctx,
            args,
            command="members unban",
            action="members.unban",
            params={"chat": resolved.as_dict(), "users": [u.as_dict() for u in users]},
            preview={"chat": resolved.as_dict(), "users": [u.as_dict() for u in users]},
            execute=execute,
        )
    finally:
        await ctx.close()


ADMIN_RIGHTS = {
    "change_info",
    "post_messages",
    "edit_messages",
    "delete_messages",
    "ban_users",
    "invite_users",
    "pin_messages",
    "add_admins",
    "anonymous",
    "manage_call",
    "manage_topics",
    "manage_direct_messages",
    "manage_ranks",
}


async def list_admins(ctx: TG, args) -> dict:
    return await list_members(ctx, _with_filter(args, "admins"))


def _with_filter(args, value: str):
    # argparse namespaces are mutable; avoid changing the caller's object.
    import copy

    clone = copy.copy(args)
    setattr(clone, "filter", value)
    return clone


async def promote_admins(ctx: TG, args) -> dict:
    await ctx.connect()
    try:
        resolved = await ctx.resolve(args.chat, kinds=["group", "supergroup", "channel"])
        users = await _users(ctx, csv(args.users))
        if not users:
            raise CliError("VALIDATION", "--users 不能为空。")
        if not isinstance(resolved.entity, types.Channel) and (args.rights or args.rank):
            raise CliError(
                "UNSUPPORTED",
                "普通群只能切换管理员，不能分配精细权限或头衔；请去掉 --rights/--rank。",
            )
        if args.rights:
            rights = {r.strip() for r in args.rights.split(",") if r.strip()}
        else:
            rights = {"invite_users", "pin_messages", "delete_messages", "ban_users"}
            if isinstance(resolved.entity, types.Channel) and resolved.entity.broadcast:
                rights.add("post_messages")
            if resolved.is_forum:
                rights.add("manage_topics")
        unknown = rights - ADMIN_RIGHTS
        if unknown:
            raise CliError("VALIDATION", f"未知管理员权限：{sorted(unknown)}")
        rights_obj = types.ChatAdminRights(**{r: True for r in rights})

        async def execute():
            if isinstance(resolved.entity, types.Channel):
                for user in users:
                    await ctx.client(
                        functions.channels.EditAdminRequest(
                            channel=resolved.entity,
                            user_id=user.entity,
                            admin_rights=rights_obj,
                            rank=args.rank,
                        )
                    )
            else:
                for user in users:
                    await ctx.client(
                        functions.messages.EditChatAdminRequest(
                            chat_id=resolved.entity.id, user_id=user.entity, is_admin=True
                        )
                    )
            is_channel = isinstance(resolved.entity, types.Channel)
            return {
                "chat": resolved.as_dict(),
                "promoted": [u.as_dict() for u in users],
                "rights": sorted(rights) if is_channel else [],
                "rank": args.rank if is_channel else None,
                "basic_group_is_admin_only": not is_channel,
            }

        return await run_write(
            ctx,
            args,
            command="admins promote",
            action="admins.promote",
            params={
                "chat": resolved.as_dict(),
                "users": [u.as_dict() for u in users],
                "rights": sorted(rights),
                "rank": args.rank,
            },
            preview={"chat": resolved.as_dict(), "users": [u.as_dict() for u in users], "rights": sorted(rights)},
            execute=execute,
        )
    finally:
        await ctx.close()


async def demote_admins(ctx: TG, args) -> dict:
    await ctx.connect()
    try:
        resolved = await ctx.resolve(args.chat, kinds=["group", "supergroup", "channel"])
        users = await _users(ctx, csv(args.users))
        if not users:
            raise CliError("VALIDATION", "--users 不能为空。")

        async def execute():
            if isinstance(resolved.entity, types.Channel):
                for user in users:
                    await ctx.client(
                        functions.channels.EditAdminRequest(
                            channel=resolved.entity,
                            user_id=user.entity,
                            admin_rights=types.ChatAdminRights(),
                        )
                    )
            else:
                for user in users:
                    await ctx.client(
                        functions.messages.EditChatAdminRequest(
                            chat_id=resolved.entity.id, user_id=user.entity, is_admin=False
                        )
                    )
            return {"chat": resolved.as_dict(), "demoted": [u.as_dict() for u in users]}

        return await run_write(
            ctx,
            args,
            command="admins demote",
            action="admins.demote",
            params={"chat": resolved.as_dict(), "users": [u.as_dict() for u in users]},
            preview={"chat": resolved.as_dict(), "users": [u.as_dict() for u in users]},
            execute=execute,
            destructive=True,
            confirm_label=",".join(u.ref for u in users),
        )
    finally:
        await ctx.close()
