"""Pending join requests for groups/channels with join-request enabled."""
from __future__ import annotations

import time

from telethon.tl import functions, types

from ..client import TG
from ..envelope import emit_read
from ..errors import CliError
from ..serialize import iso, user_to_dict
from .common import add_chat, add_limit, add_write_flags, csv, run_write
from .invites import normalize_invite_link


async def list_requests(ctx: TG, args) -> dict:
    started = time.monotonic()
    await ctx.connect()
    try:
        resolved = await ctx.resolve(args.chat, kinds=["group", "supergroup", "channel"])
        items = []
        offset_date = None
        offset_user = types.InputUserEmpty()
        remaining = int(args.limit)
        while remaining > 0:
            batch_limit = min(100, remaining)
            result = await ctx.client(
                functions.messages.GetChatInviteImportersRequest(
                    peer=resolved.entity,
                    offset_date=offset_date,
                    offset_user=offset_user,
                    limit=batch_limit,
                    requested=True,
                    q=args.search or None,
                )
            )
            users = {u.id: u for u in (getattr(result, "users", None) or [])}
            batch = getattr(result, "importers", None) or []
            for importer in batch:
                user = users.get(getattr(importer, "user_id", None))
                item = user_to_dict(user) if user is not None else {"id": getattr(importer, "user_id", None)}
                item.update(
                    {
                        "date": iso(getattr(importer, "date", None)),
                        "about": getattr(importer, "about", None),
                        "requested": bool(getattr(importer, "requested", False)),
                        "approved_by": getattr(importer, "approved_by", None),
                        "via_chatlist": bool(getattr(importer, "via_chatlist", False)),
                    }
                )
                items.append(item)
            if len(batch) < batch_limit:
                break
            last = batch[-1]
            last_user = users.get(getattr(last, "user_id", None))
            if last_user is None:
                break
            offset_date = getattr(last, "date", None)
            offset_user = types.InputUser(
                user_id=last_user.id, access_hash=last_user.access_hash
            )
            remaining = int(args.limit) - len(items)
        return emit_read(
            "join-requests list",
            {"chat": resolved.as_dict(), "count": len(items), "items": items},
            started,
            **ctx.account_meta(),
        )
    finally:
        await ctx.close()


async def _resolve_users(ctx: TG, values: list[str]):
    users = []
    for value in values:
        resolved = await ctx.resolve(value, kinds=["private", "bot"])
        if not isinstance(resolved.entity, types.User):
            raise CliError("VALIDATION", f"`{value}` 不是用户。")
        users.append(resolved)
    return users


async def _decide(ctx: TG, args, *, approved: bool) -> dict:
    await ctx.connect()
    try:
        resolved = await ctx.resolve(args.chat, kinds=["group", "supergroup", "channel"])
        users = await _resolve_users(ctx, csv(args.users))
        if not users:
            raise CliError("VALIDATION", "--users 不能为空。")

        async def execute():
            for user in users:
                await ctx.client(
                    functions.messages.HideChatJoinRequestRequest(
                        peer=resolved.entity,
                        user_id=user.entity,
                        approved=approved,
                    )
                )
            return {
                "chat": resolved.as_dict(),
                "approved": approved,
                "users": [u.as_dict() for u in users],
            }

        command = "join-requests approve" if approved else "join-requests reject"
        action = "join_requests.approve" if approved else "join_requests.reject"
        return await run_write(
            ctx,
            args,
            command=command,
            action=action,
            params={"chat": resolved.as_dict(), "users": [u.as_dict() for u in users]},
            preview={"chat": resolved.as_dict(), "users": [u.as_dict() for u in users], "approved": approved},
            execute=execute,
            destructive=not approved,
            confirm_label=",".join(u.ref for u in users),
        )
    finally:
        await ctx.close()


async def approve_requests(ctx: TG, args) -> dict:
    return await _decide(ctx, args, approved=True)


async def reject_requests(ctx: TG, args) -> dict:
    return await _decide(ctx, args, approved=False)


async def approve_all(ctx: TG, args) -> dict:
    return await _all(ctx, args, approved=True)


async def reject_all(ctx: TG, args) -> dict:
    return await _all(ctx, args, approved=False)


async def _all(ctx: TG, args, *, approved: bool) -> dict:
    await ctx.connect()
    try:
        resolved = await ctx.resolve(args.chat, kinds=["group", "supergroup", "channel"])
        link = normalize_invite_link(args.link) if args.link else None

        async def execute():
            await ctx.client(
                functions.messages.HideAllChatJoinRequestsRequest(
                    peer=resolved.entity, approved=approved, link=link
                )
            )
            return {
                "chat": resolved.as_dict(),
                "approved": approved,
                "link": link,
                "all": True,
            }

        command = "join-requests approve-all" if approved else "join-requests reject-all"
        action = "join_requests.approve_all" if approved else "join_requests.reject_all"
        params = {"chat": resolved.as_dict(), "link": link, "all": True}
        return await run_write(
            ctx,
            args,
            command=command,
            action=action,
            params=params,
            preview=params,
            execute=execute,
            destructive=True,
            confirm_label="all",
        )
    finally:
        await ctx.close()
