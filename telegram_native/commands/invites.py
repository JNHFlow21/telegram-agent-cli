"""Full invite-link lifecycle: list, edit, delete, importers."""
from __future__ import annotations

import re
import time
from datetime import datetime, timedelta, timezone

from telethon.errors import InviteRevokedMissingError
from telethon.tl import functions, types

from ..client import TG
from ..envelope import emit_read
from ..errors import CliError
from ..serialize import iso, user_to_dict
from .common import add_chat, add_limit, add_write_flags, run_write


def normalize_invite_link(link: str) -> str:
    """Normalize t.me/telegram.me short links to the canonical https URL."""
    value = (link or "").strip()
    if not value:
        raise CliError("VALIDATION", "--link 不能为空。")
    if value.startswith("tg://join?invite="):
        value = "https://t.me/+" + value.split("invite=", 1)[1]
    elif value.startswith("+"):
        value = "https://t.me/" + value
    elif value.startswith("t.me/") or value.startswith("telegram.me/") or value.startswith("telegram.dog/"):
        value = "https://" + value
    if not re.match(r"^https://(?:t\.me|telegram\.me|telegram\.dog)/(?:\+|joinchat/)", value):
        raise CliError("VALIDATION", "需要私有邀请链接（https://t.me/+... 或 t.me/joinchat/...）。")
    return value


def _invite_to_dict(invite: types.ChatInviteExported) -> dict:
    return {
        "link": invite.link,
        "admin_id": invite.admin_id,
        "date": iso(invite.date),
        "revoked": bool(invite.revoked),
        "permanent": bool(invite.permanent),
        "request_needed": bool(invite.request_needed),
        "start_date": iso(invite.start_date),
        "expire_date": iso(invite.expire_date),
        "usage_limit": invite.usage_limit,
        "usage": invite.usage,
        "requested": invite.requested,
        "title": invite.title,
    }


async def list_invites(ctx: TG, args) -> dict:
    started = time.monotonic()
    await ctx.connect()
    try:
        resolved = await ctx.resolve(args.chat, kinds=["group", "supergroup", "channel"])
        items = []
        offset_date = None
        offset_link = None
        remaining = int(args.limit)
        while remaining > 0:
            batch_limit = min(100, remaining)
            result = await ctx.client(
                functions.messages.GetExportedChatInvitesRequest(
                    peer=resolved.entity,
                    admin_id=types.InputUserSelf(),
                    limit=batch_limit,
                    revoked=True if getattr(args, "revoked", False) else None,
                    offset_date=offset_date,
                    offset_link=offset_link,
                )
            )
            batch = list(getattr(result, "invites", None) or [])
            items.extend(_invite_to_dict(invite) for invite in batch)
            if len(batch) < batch_limit:
                break
            last = batch[-1]
            offset_date = last.date
            offset_link = last.link
            remaining = int(args.limit) - len(items)
        return emit_read(
            "invite list",
            {"chat": resolved.as_dict(), "count": len(items), "items": items},
            started,
            **ctx.account_meta(),
        )
    finally:
        await ctx.close()


async def edit_invite(ctx: TG, args) -> dict:
    await ctx.connect()
    try:
        resolved = await ctx.resolve(args.chat, kinds=["group", "supergroup", "channel"])
        link = normalize_invite_link(args.link)
        if (
            args.expire_days is None
            and not args.no_expire
            and args.usage_limit is None
            and args.request_needed is None
            and args.title is None
        ):
            raise CliError(
                "VALIDATION",
                "至少提供一个修改项：--expire-days/--no-expire、--usage-limit、"
                "--request-needed/--no-request、--title。",
            )
        if args.usage_limit == 0:
            raise CliError(
                "UNSUPPORTED",
                "当前 Telethon 层无法把 usage_limit 清零；请新建链接或删除旧链接。",
            )
        if args.request_needed is True and args.usage_limit is not None:
            raise CliError(
                "VALIDATION",
                "Telegram 不允许审批链接同时设置 usage_limit；请分两次改，或二选一。",
            )
        expire_date = None
        if args.no_expire:
            expire_date = datetime(1970, 1, 1, tzinfo=timezone.utc)
        elif args.expire_days is not None:
            expire_date = datetime.now(timezone.utc) + timedelta(days=int(args.expire_days))

        async def execute():
            await ctx.client(
                functions.messages.EditExportedChatInviteRequest(
                    peer=resolved.entity,
                    link=link,
                    expire_date=expire_date,
                    usage_limit=args.usage_limit,
                    request_needed=args.request_needed,
                    title=args.title,
                )
            )
            return {
                "chat": resolved.as_dict(),
                "link": link,
                "expire_date": iso(expire_date),
                "usage_limit": args.usage_limit,
                "request_needed": args.request_needed,
                "title": args.title,
            }

        params = {
            "chat": resolved.as_dict(),
            "link": link,
            "expire_date": iso(expire_date),
            "usage_limit": args.usage_limit,
            "request_needed": args.request_needed,
            "title": args.title,
        }
        return await run_write(
            ctx,
            args,
            command="invite edit",
            action="invite.edit",
            params=params,
            preview=params,
            execute=execute,
        )
    finally:
        await ctx.close()


async def delete_invite(ctx: TG, args) -> dict:
    await ctx.connect()
    try:
        resolved = await ctx.resolve(args.chat, kinds=["group", "supergroup", "channel"])
        link = normalize_invite_link(args.link)

        async def execute():
            # Telegram only lets us delete a link from the "revoked" set; a
            # still-active link must be revoked first. Deleting an already
            # revoked/deleted link is treated as a no-op.
            try:
                await ctx.client(
                    functions.messages.EditExportedChatInviteRequest(
                        peer=resolved.entity, link=link, revoked=True
                    )
                )
            except InviteRevokedMissingError:
                pass
            try:
                await ctx.client(
                    functions.messages.DeleteExportedChatInviteRequest(
                        peer=resolved.entity, link=link
                    )
                )
            except InviteRevokedMissingError:
                return {
                    "chat": resolved.as_dict(),
                    "link": link,
                    "deleted": True,
                    "already_revoked": True,
                }
            return {"chat": resolved.as_dict(), "link": link, "deleted": True}

        return await run_write(
            ctx,
            args,
            command="invite delete",
            action="invite.delete",
            params={"chat": resolved.as_dict(), "link": link},
            preview={"delete_link": link},
            execute=execute,
            destructive=True,
            confirm_label=link,
        )
    finally:
        await ctx.close()


async def list_importers(ctx: TG, args) -> dict:
    started = time.monotonic()
    await ctx.connect()
    try:
        resolved = await ctx.resolve(args.chat, kinds=["group", "supergroup", "channel"])
        link = normalize_invite_link(args.link) if args.link else None
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
                    requested=True if args.pending else None,
                    link=link,
                )
            )
            users = {u.id: u for u in (getattr(result, "users", None) or [])}
            batch = list(getattr(result, "importers", None) or [])
            for importer in batch:
                user = users.get(importer.user_id)
                item = user_to_dict(user) if user is not None else {"id": importer.user_id}
                item.update(
                    {
                        "date": iso(importer.date),
                        "requested": bool(importer.requested),
                        "via_chatlist": bool(importer.via_chatlist),
                        "about": importer.about,
                        "approved_by": importer.approved_by,
                    }
                )
                items.append(item)
            if len(batch) < batch_limit:
                break
            last = batch[-1]
            last_user = users.get(last.user_id)
            if last_user is None:
                break
            offset_date = last.date
            offset_user = types.InputUser(
                user_id=last_user.id, access_hash=last_user.access_hash
            )
            remaining = int(args.limit) - len(items)
        return emit_read(
            "invite importers",
            {"chat": resolved.as_dict(), "link": link, "count": len(items), "items": items},
            started,
            **ctx.account_meta(),
        )
    finally:
        await ctx.close()
