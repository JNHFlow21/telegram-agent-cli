"""Default member permissions and admin log."""
from __future__ import annotations

import json
import time

from telethon.tl import functions, types

from ..client import TG
from ..envelope import emit_read
from ..errors import CliError
from ..serialize import iso, user_to_dict
from .common import add_chat, add_limit, add_write_flags, run_write

SEND_FIELDS = (
    "send_messages",
    "send_media",
    "send_stickers",
    "send_gifs",
    "send_games",
    "send_inline",
    "embed_links",
    "send_polls",
    "send_photos",
    "send_videos",
    "send_roundvideos",
    "send_audios",
    "send_voices",
    "send_docs",
    "send_plain",
    "send_reactions",
)

PRESETS = {
    "open": {field: False for field in SEND_FIELDS},
    "readonly": {field: True for field in SEND_FIELDS},
    "media-only": {field: False for field in SEND_FIELDS} | {"send_plain": True},
}


async def set_permissions(ctx: TG, args) -> dict:
    await ctx.connect()
    try:
        resolved = await ctx.resolve(args.chat, kinds=["group", "supergroup", "channel"])
        if not isinstance(resolved.entity, types.Channel):
            raise CliError(
                "UNSUPPORTED",
                "普通群不支持默认成员权限；请先迁移为超级群。",
            )
        if not args.preset and not args.restrict:
            raise CliError("VALIDATION", "需要 --preset 或 --restrict。")
        rights = {field: False for field in SEND_FIELDS}
        if args.preset:
            rights.update(PRESETS[args.preset])
        if args.restrict:
            for field in (x.strip() for x in args.restrict.split(",")):
                if not field:
                    continue
                if field not in SEND_FIELDS:
                    raise CliError(
                        "VALIDATION",
                        f"未知限制字段：{field}",
                        details={"allowed": list(SEND_FIELDS)},
                    )
                rights[field] = True
        rights_obj = types.ChatBannedRights(until_date=None, **rights)

        async def execute():
            await ctx.client(
                functions.messages.EditChatDefaultBannedRightsRequest(
                    peer=resolved.entity, banned_rights=rights_obj
                )
            )
            return {
                "chat": resolved.as_dict(),
                "restrictions": [k for k, v in rights.items() if v],
                "preset": args.preset,
            }

        params = {
            "chat": resolved.as_dict(),
            "preset": args.preset,
            "restrict": args.restrict,
        }
        return await run_write(
            ctx,
            args,
            command="chat set-permissions",
            action="chat.set_permissions",
            params=params,
            preview=params,
            execute=execute,
        )
    finally:
        await ctx.close()


def _event_to_dict(event, users: dict, *, full: bool) -> dict:
    user = users.get(getattr(event, "user_id", None))
    action = getattr(event, "action", None)
    data = {
        "id": getattr(event, "id", None),
        "date": iso(getattr(event, "date", None)),
        "user_id": getattr(event, "user_id", None),
        "user": user_to_dict(user) if user is not None else None,
        "action_type": type(action).__name__ if action is not None else None,
    }
    if action is not None:
        try:
            raw = action.to_dict()
        except Exception:  # noqa: BLE001
            raw = {"repr": str(action)[:500]}
        if full:
            data["action"] = raw
        else:
            data["action_summary"] = json.dumps(raw, ensure_ascii=False, default=str)[:800]
    return data


async def admin_log(ctx: TG, args) -> dict:
    started = time.monotonic()
    await ctx.connect()
    try:
        resolved = await ctx.resolve(args.chat, kinds=["supergroup", "channel"])
        result = await ctx.client(
            functions.channels.GetAdminLogRequest(
                channel=resolved.entity,
                q=args.query or "",
                max_id=0,
                min_id=0,
                limit=args.limit,
                events_filter=None,
                admins=None,
            )
        )
        users = {u.id: u for u in (getattr(result, "users", None) or [])}
        items = [
            _event_to_dict(event, users, full=bool(args.full))
            for event in (getattr(result, "events", None) or [])
        ]
        return emit_read(
            "admin-log list",
            {"chat": resolved.as_dict(), "count": len(items), "items": items},
            started,
            **ctx.account_meta(),
        )
    finally:
        await ctx.close()
