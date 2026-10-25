"""Message read and write commands."""
from __future__ import annotations

import time
from pathlib import Path

from telethon.tl import functions, types

from ..client import TG
from ..envelope import emit_read
from ..errors import CliError
from ..serialize import iso, message_to_dict
from .common import add_chat, add_limit, add_write_flags, run_write


def _utf16_len(text: str) -> int:
    return len(text.encode("utf-16-le")) // 2


def _text_from_args(args, *, caption: bool = False) -> str:
    if getattr(args, "text", None) is not None and getattr(args, "text_file", None):
        raise CliError("VALIDATION", "--text 和 --text-file 只能二选一。")
    if getattr(args, "text_file", None):
        path = Path(args.text_file).expanduser()
        if not path.is_file():
            raise CliError("VALIDATION", f"找不到文本文件：{path}")
        text = path.read_text("utf-8")
    else:
        text = getattr(args, "text", None) or ""
    limit = 1024 if caption else 4096
    length = _utf16_len(text)
    if length > limit:
        raise CliError("VALIDATION", f"文本长度 {length} 超过 Telegram 限制 {limit}（UTF-16）。")
    return text


async def _search_in_topic(ctx: TG, entity, query: str, limit: int, topic: int) -> list:
    """Search inside one forum topic.

    messages.getReplies (used by iter_messages(reply_to=...)) silently drops
    the query on this layer, so use messages.search(top_msg_id=...).
    """
    result = await ctx.client(
        functions.messages.SearchRequest(
            peer=entity,
            q=query,
            filter=types.InputMessagesFilterEmpty(),
            min_date=None,
            max_date=None,
            offset_id=0,
            add_offset=0,
            limit=limit,
            max_id=0,
            min_id=0,
            hash=0,
            top_msg_id=topic,
        )
    )
    return [m for m in (getattr(result, "messages", None) or []) if isinstance(m, types.Message)]


async def list_messages(ctx: TG, args) -> dict:
    started = time.monotonic()
    await ctx.connect()
    try:
        resolved = await ctx.resolve(args.chat)
        items = []
        search = getattr(args, "search", None)
        topic = getattr(args, "topic", None)
        if search and topic:
            messages = await _search_in_topic(ctx, resolved.entity, search, args.limit, topic)
        else:
            messages = []
            async for message in ctx.client.iter_messages(
                resolved.entity,
                limit=args.limit,
                offset_id=getattr(args, "offset_id", 0) or 0,
                search=search,
                reply_to=topic,
                reverse=bool(getattr(args, "reverse", False)),
            ):
                messages.append(message)
        for message in messages:
            items.append(message_to_dict(message, max_text=getattr(args, "max_text", None)))
        return emit_read(
            "messages list",
            {"chat": resolved.as_dict(), "count": len(items), "items": items},
            started,
            **ctx.account_meta(),
        )
    finally:
        await ctx.close()


async def search_messages(ctx: TG, args) -> dict:
    started = time.monotonic()
    await ctx.connect()
    try:
        resolved = await ctx.resolve(args.chat)
        query = (args.query or "").strip()
        if not query:
            raise CliError("VALIDATION", "--query 不能为空。")
        items = []
        if getattr(args, "topic", None):
            messages = await _search_in_topic(ctx, resolved.entity, query, args.limit, args.topic)
        else:
            messages = []
            async for message in ctx.client.iter_messages(
                resolved.entity,
                limit=args.limit,
                search=query,
            ):
                messages.append(message)
        for message in messages:
            items.append(message_to_dict(message, max_text=args.max_text))
        return emit_read(
            "messages search",
            {"chat": resolved.as_dict(), "query": query, "count": len(items), "items": items},
            started,
            **ctx.account_meta(),
        )
    finally:
        await ctx.close()


async def get_message(ctx: TG, args) -> dict:
    started = time.monotonic()
    await ctx.connect()
    try:
        resolved = await ctx.resolve(args.chat)
        message = await ctx.client.get_messages(resolved.entity, ids=args.id)
        if message is None:
            raise CliError("NOT_FOUND", f"找不到消息 {args.id}。", details={"chat": resolved.as_dict()})
        return emit_read(
            "messages get",
            {"chat": resolved.as_dict(), "message": message_to_dict(message)},
            started,
            **ctx.account_meta(),
        )
    finally:
        await ctx.close()


async def send_message(ctx: TG, args) -> dict:
    await ctx.connect()
    try:
        resolved = await ctx.resolve(args.chat)
        text = _text_from_args(args, caption=bool(args.file))
        if not text and not args.file:
            raise CliError("VALIDATION", "需要 --text 或 --file。")
        if args.topic and args.reply_to:
            raise CliError("VALIDATION", "--topic 和 --reply-to 只能二选一。")
        # The General topic (id=1) is the group's main history; sending with
        # reply_to=1 is invalid, so omit the reply target there.
        reply_to = None if args.topic == 1 else (args.topic or args.reply_to)

        async def execute():
            if args.file:
                path = Path(args.file).expanduser()
                if not path.is_file():
                    raise CliError("VALIDATION", f"找不到文件：{path}")
                if path.stat().st_size > 2 * 1024**3:
                    raise CliError("VALIDATION", "文件超过 2 GB 用户会话上限；请先压缩或分片。")
                message = await ctx.client.send_file(
                    resolved.entity,
                    str(path),
                    caption=text or None,
                    reply_to=reply_to,
                    silent=bool(args.silent),
                    force_document=bool(args.as_document),
                )
            else:
                message = await ctx.client.send_message(
                    resolved.entity,
                    text,
                    reply_to=reply_to,
                    silent=bool(args.silent),
                    link_preview=not bool(args.no_link_preview),
                )
            if isinstance(message, list):
                message = message[0]
            return {"chat": resolved.as_dict(), "message": message_to_dict(message)}

        preview = {
            "chat": resolved.as_dict(),
            "text_preview": text[:200],
            "text_length": len(text),
            "file": args.file,
            "topic": args.topic,
            "reply_to": args.reply_to,
            "silent": bool(args.silent),
        }
        return await run_write(
            ctx,
            args,
            command="messages send",
            action="messages.send",
            params=preview,
            preview=preview,
            execute=execute,
        )
    finally:
        await ctx.close()


async def edit_message(ctx: TG, args) -> dict:
    await ctx.connect()
    try:
        resolved = await ctx.resolve(args.chat)
        text = _text_from_args(args)
        if not text:
            raise CliError("VALIDATION", "编辑后的文本不能为空。")

        async def execute():
            message = await ctx.client.edit_message(resolved.entity, args.id, text)
            return {"chat": resolved.as_dict(), "message": message_to_dict(message)}

        return await run_write(
            ctx,
            args,
            command="messages edit",
            action="messages.edit",
            params={"chat": resolved.as_dict(), "message_id": args.id, "text_preview": text[:200]},
            preview={"chat": resolved.as_dict(), "message_id": args.id, "text_preview": text[:200]},
            execute=execute,
        )
    finally:
        await ctx.close()


async def delete_messages(ctx: TG, args) -> dict:
    await ctx.connect()
    try:
        resolved = await ctx.resolve(args.chat)
        ids = [int(x.strip()) for x in str(args.ids).split(",") if x.strip()]
        if not ids:
            raise CliError("VALIDATION", "--ids 不能为空。")
        revoke = not bool(args.keep_for_me)

        async def execute():
            await ctx.client.delete_messages(resolved.entity, ids, revoke=revoke)
            return {"chat": resolved.as_dict(), "deleted_ids": ids, "revoke": revoke}

        return await run_write(
            ctx,
            args,
            command="messages delete",
            action="messages.delete",
            params={"chat": resolved.as_dict(), "ids": ids, "revoke": revoke},
            preview={"chat": resolved.as_dict(), "ids": ids, "revoke": revoke},
            execute=execute,
            destructive=True,
            confirm_label=",".join(str(i) for i in ids),
        )
    finally:
        await ctx.close()


async def pin_message(ctx: TG, args) -> dict:
    await ctx.connect()
    try:
        resolved = await ctx.resolve(args.chat)

        async def execute():
            await ctx.client.pin_message(resolved.entity, args.id, notify=bool(args.notify))
            return {"chat": resolved.as_dict(), "message_id": args.id, "pinned": True, "notify": bool(args.notify)}

        return await run_write(
            ctx,
            args,
            command="messages pin",
            action="messages.pin",
            params={"chat": resolved.as_dict(), "message_id": args.id, "notify": bool(args.notify)},
            preview={"chat": resolved.as_dict(), "message_id": args.id, "notify": bool(args.notify)},
            execute=execute,
        )
    finally:
        await ctx.close()


async def unpin_message(ctx: TG, args) -> dict:
    await ctx.connect()
    try:
        resolved = await ctx.resolve(args.chat)

        async def execute():
            await ctx.client.unpin_message(resolved.entity, args.id, notify=bool(args.notify))
            return {"chat": resolved.as_dict(), "message_id": args.id, "pinned": False, "notify": bool(args.notify)}

        return await run_write(
            ctx,
            args,
            command="messages unpin",
            action="messages.unpin",
            params={"chat": resolved.as_dict(), "message_id": args.id, "notify": bool(args.notify)},
            preview={"chat": resolved.as_dict(), "message_id": args.id, "notify": bool(args.notify)},
            execute=execute,
        )
    finally:
        await ctx.close()


async def mark_read(ctx: TG, args) -> dict:
    await ctx.connect()
    try:
        resolved = await ctx.resolve(args.chat)

        async def execute():
            changed = await ctx.client.send_read_acknowledge(
                resolved.entity, max_id=getattr(args, "max_id", None)
            )
            return {"chat": resolved.as_dict(), "changed": changed, "max_id": getattr(args, "max_id", None)}

        return await run_write(
            ctx,
            args,
            command="messages mark-read",
            action="messages.mark_read",
            params={"chat": resolved.as_dict(), "max_id": getattr(args, "max_id", None)},
            preview={"chat": resolved.as_dict(), "max_id": getattr(args, "max_id", None)},
            execute=execute,
        )
    finally:
        await ctx.close()
