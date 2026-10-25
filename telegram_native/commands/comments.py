"""Channel post comments via the linked discussion supergroup."""
from __future__ import annotations

import time

from telethon.tl import functions, types

from ..client import TG
from ..envelope import emit_read
from ..errors import CliError
from ..serialize import entity_to_dict, message_to_dict
from .common import add_chat, add_limit, add_write_flags, run_write


async def _discussion(ctx: TG, channel, post_id: int):
    result = await ctx.client(
        functions.messages.GetDiscussionMessageRequest(peer=channel, msg_id=post_id)
    )
    messages = [m for m in (getattr(result, "messages", None) or []) if isinstance(m, types.Message)]
    group = next(
        (
            c
            for c in (getattr(result, "chats", None) or [])
            if isinstance(c, types.Channel) and c.megagroup
        ),
        None,
    )
    if group is None or not messages:
        raise CliError(
            "UNSUPPORTED",
            "该频道帖子没有关联讨论群/评论区。",
            details={"post_id": post_id},
        )
    # messages[0] is normally the channel post, messages[1] the discussion
    # message in the linked group. Use the last message defensively.
    discussion_message = messages[-1]
    return result, group, discussion_message


async def info(ctx: TG, args) -> dict:
    started = time.monotonic()
    await ctx.connect()
    try:
        channel = await ctx.resolve(args.chat, kinds=["channel"])
        result, group, discussion_message = await _discussion(ctx, channel.entity, args.post_id)
        return emit_read(
            "comments info",
            {
                "channel": channel.as_dict(),
                "post_id": args.post_id,
                "discussion_group": entity_to_dict(group),
                "discussion_message_id": discussion_message.id,
                "unread_count": getattr(result, "unread_count", None),
                "max_id": getattr(result, "max_id", None),
                "read_inbox_max_id": getattr(result, "read_inbox_max_id", None),
                "read_outbox_max_id": getattr(result, "read_outbox_max_id", None),
            },
            started,
            **ctx.account_meta(),
        )
    finally:
        await ctx.close()


async def list_comments(ctx: TG, args) -> dict:
    started = time.monotonic()
    await ctx.connect()
    try:
        channel = await ctx.resolve(args.chat, kinds=["channel"])
        _, group, discussion_message = await _discussion(ctx, channel.entity, args.post_id)
        result = await ctx.client(
            functions.messages.GetRepliesRequest(
                peer=group,
                msg_id=discussion_message.id,
                offset_id=args.offset_id or 0,
                offset_date=None,
                add_offset=0,
                limit=args.limit,
                max_id=0,
                min_id=0,
                hash=0,
            )
        )
        items = [
            message_to_dict(m)
            for m in (getattr(result, "messages", None) or [])
            if isinstance(m, types.Message)
        ]
        return emit_read(
            "comments list",
            {
                "channel": channel.as_dict(),
                "post_id": args.post_id,
                "discussion_group": entity_to_dict(group),
                "discussion_message_id": discussion_message.id,
                "count": len(items),
                "items": items,
            },
            started,
            **ctx.account_meta(),
        )
    finally:
        await ctx.close()


async def reply(ctx: TG, args) -> dict:
    await ctx.connect()
    try:
        channel = await ctx.resolve(args.chat, kinds=["channel"])
        _, group, discussion_message = await _discussion(ctx, channel.entity, args.post_id)
        text = args.text or ""
        if not text:
            raise CliError("VALIDATION", "--text 不能为空。")
        if len(text.encode("utf-16-le")) // 2 > 4096:
            raise CliError("VALIDATION", "评论不能超过 4096（UTF-16）。")
        reply_to = args.reply_to or discussion_message.id

        async def execute():
            message = await ctx.client.send_message(
                group, text, reply_to=reply_to
            )
            return {
                "channel": channel.as_dict(),
                "post_id": args.post_id,
                "discussion_group": entity_to_dict(group),
                "message": message_to_dict(message),
            }

        params = {
            "channel": channel.as_dict(),
            "post_id": args.post_id,
            "discussion_group": entity_to_dict(group),
            "reply_to": reply_to,
            "text_preview": text[:200],
        }
        return await run_write(
            ctx,
            args,
            command="comments reply",
            action="comments.reply",
            params=params,
            preview=params,
            execute=execute,
        )
    finally:
        await ctx.close()
