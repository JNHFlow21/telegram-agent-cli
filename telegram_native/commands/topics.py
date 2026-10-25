"""Forum topics: inspect, enable/disable, create/edit/close/delete."""
from __future__ import annotations

import random
import time

from telethon.tl import functions, types

from ..client import TG
from ..envelope import emit_read
from ..errors import CliError
from ..serialize import topic_to_dict
from .common import add_chat, add_limit, add_write_flags, run_write

# Telegram's permitted forum-topic icon colors (RGB).
FORUM_ICON_COLORS = {
    0x6FB9F0,
    0xFFD67E,
    0xCB86DB,
    0x8EEE98,
    0xFF93B2,
    0xFB6F5F,
}
MAX_TOPIC_TITLE_BYTES = 128


def _validate_title(title: str) -> str:
    value = (title or "").strip()
    if not value:
        raise CliError("VALIDATION", "--title 不能为空。")
    if len(value.encode("utf-8")) > MAX_TOPIC_TITLE_BYTES:
        raise CliError("VALIDATION", f"话题标题不能超过 {MAX_TOPIC_TITLE_BYTES} 字节（UTF-8）。")
    return value


def _validate_icon_color(value: int | None) -> None:
    if value is not None and value not in FORUM_ICON_COLORS:
        raise CliError(
            "VALIDATION",
            "icon_color 必须是 Telegram 支持的主题色之一。",
            details={"allowed": [f"0x{x:06X}" for x in sorted(FORUM_ICON_COLORS)]},
        )


def _require_forum(resolved) -> None:
    if not resolved.is_forum:
        raise CliError(
            "UNSUPPORTED",
            f"`{resolved.title}` 不是已开启话题的超级群；先运行 `telegram-native forum enable`。",
            details={"chat": resolved.as_dict()},
        )


async def status(ctx: TG, args) -> dict:
    started = time.monotonic()
    await ctx.connect()
    try:
        resolved = await ctx.resolve(args.chat, kinds=["group", "supergroup"])
        if resolved.kind != "supergroup":
            return emit_read(
                "forum status",
                {"chat": resolved.as_dict(), "forum": False, "reason": "basic_group"},
                started,
                **ctx.account_meta(),
            )
        return emit_read(
            "forum status",
            {"chat": resolved.as_dict(), "forum": resolved.is_forum},
            started,
            **ctx.account_meta(),
        )
    finally:
        await ctx.close()


async def _toggle(ctx: TG, args, *, enabled: bool) -> dict:
    await ctx.connect()
    try:
        resolved = await ctx.resolve(args.chat, kinds=["group", "supergroup"])
        if resolved.kind != "supergroup":
            raise CliError("UNSUPPORTED", "只有超级群可以开启/关闭话题。")
        if resolved.is_forum == enabled:
            # Still use the write gate so --execute semantics are explicit, but
            # the execute body is a no-op.
            async def execute_noop():
                return {"chat": resolved.as_dict(), "forum": enabled, "no_change": True}

            return await run_write(
                ctx,
                args,
                command="forum enable" if enabled else "forum disable",
                action="forum.enable" if enabled else "forum.disable",
                params={"chat": resolved.as_dict()},
                preview={"forum": enabled, "no_change": True},
                execute=execute_noop,
                destructive=not enabled,
                confirm_label=resolved.title,
            )

        async def execute():
            await ctx.client(
                functions.channels.ToggleForumRequest(
                    channel=resolved.entity, enabled=enabled, tabs=False
                )
            )
            return {"chat": resolved.as_dict(), "forum": enabled}

        return await run_write(
            ctx,
            args,
            command="forum enable" if enabled else "forum disable",
            action="forum.enable" if enabled else "forum.disable",
            params={"chat": resolved.as_dict()},
            preview={"before": resolved.is_forum, "after": enabled},
            execute=execute,
            destructive=not enabled,
            confirm_label=resolved.title,
        )
    finally:
        await ctx.close()


async def enable(ctx: TG, args) -> dict:
    return await _toggle(ctx, args, enabled=True)


async def disable(ctx: TG, args) -> dict:
    return await _toggle(ctx, args, enabled=False)


async def list_topics(ctx: TG, args) -> dict:
    started = time.monotonic()
    await ctx.connect()
    try:
        resolved = await ctx.resolve(args.chat, kinds=["group", "supergroup"])
        _require_forum(resolved)
        offset_date = None
        offset_id = 0
        offset_topic = getattr(args, "offset_topic", 0) or 0
        remaining = int(args.limit)
        raw_topics = []
        while remaining > 0:
            batch_size = min(100, remaining)
            result = await ctx.client(
                functions.messages.GetForumTopicsRequest(
                    peer=resolved.entity,
                    offset_date=offset_date,
                    offset_id=offset_id,
                    offset_topic=offset_topic,
                    limit=batch_size,
                    q=args.search,
                )
            )
            batch = [
                topic
                for topic in (getattr(result, "topics", None) or [])
                if isinstance(topic, types.ForumTopic)
            ]
            raw_topics.extend(batch)
            if not getattr(args, "all", False) or len(batch) < batch_size:
                break
            last = batch[-1]
            offset_date = last.date
            offset_id = last.top_message
            offset_topic = last.id
            remaining = int(args.limit) - len(raw_topics)
        topics = [topic_to_dict(topic) for topic in raw_topics[: args.limit]]
        return emit_read(
            "topic list",
            {"chat": resolved.as_dict(), "count": len(topics), "items": topics},
            started,
            **ctx.account_meta(),
        )
    finally:
        await ctx.close()


def _topic_id_from_updates(result) -> int | None:
    for update in getattr(result, "updates", None) or []:
        message = getattr(update, "message", None)
        if message is None:
            continue
        action = getattr(message, "action", None)
        if isinstance(action, types.MessageActionTopicCreate):
            return message.id
        reply = getattr(message, "reply_to", None)
        top = getattr(reply, "reply_to_top_id", None) or getattr(reply, "reply_to_msg_id", None)
        if top:
            return top
    return None


async def create_topic(ctx: TG, args) -> dict:
    await ctx.connect()
    try:
        resolved = await ctx.resolve(args.chat, kinds=["group", "supergroup"])
        _require_forum(resolved)
        title = _validate_title(args.title)
        _validate_icon_color(args.icon_color)

        async def execute():
            result = await ctx.client(
                functions.messages.CreateForumTopicRequest(
                    peer=resolved.entity,
                    title=title,
                    icon_color=args.icon_color,
                    icon_emoji_id=args.icon_emoji_id,
                    random_id=random.getrandbits(63),
                )
            )
            topic_id = _topic_id_from_updates(result)
            data = {"chat": resolved.as_dict(), "topic_id": topic_id, "title": title}
            if topic_id is None:
                data["warnings"] = [
                    {
                        "step": "resolve_topic_id",
                        "error": "话题已提交，但未能从 Updates 中确定 topic_id；请用 topic list 读回确认。",
                    }
                ]
            return data

        return await run_write(
            ctx,
            args,
            command="topic create",
            action="topic.create",
            params={"chat": resolved.as_dict(), "title": title, "icon_color": args.icon_color},
            preview={"title": title, "icon_color": args.icon_color},
            execute=execute,
        )
    finally:
        await ctx.close()


async def edit_topic(ctx: TG, args) -> dict:
    await ctx.connect()
    try:
        resolved = await ctx.resolve(args.chat, kinds=["group", "supergroup"])
        _require_forum(resolved)
        if args.icon_color is not None:
            raise CliError(
                "UNSUPPORTED",
                "当前 Telegram API 层不支持编辑话题 icon_color；只能在 topic create 时设置。",
            )
        if args.title is None and args.icon_emoji_id is None:
            raise CliError("VALIDATION", "至少提供 --title 或 --icon-emoji-id 之一。")
        title = _validate_title(args.title) if args.title is not None else None

        async def execute():
            kwargs = {}
            if title is not None:
                kwargs["title"] = title
            if args.icon_emoji_id is not None:
                kwargs["icon_emoji_id"] = args.icon_emoji_id
            await ctx.client(
                functions.messages.EditForumTopicRequest(
                    peer=resolved.entity, topic_id=args.topic_id, **kwargs
                )
            )
            return {"chat": resolved.as_dict(), "topic_id": args.topic_id, "updated": list(kwargs)}

        return await run_write(
            ctx,
            args,
            command="topic edit",
            action="topic.edit",
            params={"chat": resolved.as_dict(), "topic_id": args.topic_id, "title": args.title},
            preview={"topic_id": args.topic_id, "title": args.title},
            execute=execute,
        )
    finally:
        await ctx.close()


async def _set_closed(ctx: TG, args, *, closed: bool) -> dict:
    await ctx.connect()
    try:
        resolved = await ctx.resolve(args.chat, kinds=["group", "supergroup"])
        _require_forum(resolved)

        async def execute():
            await ctx.client(
                functions.messages.EditForumTopicRequest(
                    peer=resolved.entity, topic_id=args.topic_id, closed=closed
                )
            )
            return {"chat": resolved.as_dict(), "topic_id": args.topic_id, "closed": closed}

        command = "topic close" if closed else "topic reopen"
        return await run_write(
            ctx,
            args,
            command=command,
            action=f"topic.{command.split()[-1]}",
            params={"chat": resolved.as_dict(), "topic_id": args.topic_id},
            preview={"topic_id": args.topic_id, "closed": closed},
            execute=execute,
        )
    finally:
        await ctx.close()


async def close_topic(ctx: TG, args) -> dict:
    return await _set_closed(ctx, args, closed=True)


async def reopen_topic(ctx: TG, args) -> dict:
    return await _set_closed(ctx, args, closed=False)


async def _set_pinned(ctx: TG, args, *, pinned: bool) -> dict:
    await ctx.connect()
    try:
        resolved = await ctx.resolve(args.chat, kinds=["group", "supergroup"])
        _require_forum(resolved)

        async def execute():
            await ctx.client(
                functions.messages.UpdatePinnedForumTopicRequest(
                    peer=resolved.entity, topic_id=args.topic_id, pinned=pinned
                )
            )
            return {"chat": resolved.as_dict(), "topic_id": args.topic_id, "pinned": pinned}

        command = "topic pin" if pinned else "topic unpin"
        return await run_write(
            ctx,
            args,
            command=command,
            action=f"topic.{command.split()[-1]}",
            params={"chat": resolved.as_dict(), "topic_id": args.topic_id},
            preview={"topic_id": args.topic_id, "pinned": pinned},
            execute=execute,
        )
    finally:
        await ctx.close()


async def pin_topic(ctx: TG, args) -> dict:
    return await _set_pinned(ctx, args, pinned=True)


async def unpin_topic(ctx: TG, args) -> dict:
    return await _set_pinned(ctx, args, pinned=False)


async def delete_topic(ctx: TG, args) -> dict:
    await ctx.connect()
    try:
        resolved = await ctx.resolve(args.chat, kinds=["group", "supergroup"])
        _require_forum(resolved)
        if args.topic_id == 1:
            raise CliError(
                "UNSUPPORTED",
                "General 话题（id=1）是系统话题，不能删除；可以 close 或清空消息。",
            )

        async def execute():
            await ctx.client(
                functions.messages.DeleteTopicHistoryRequest(
                    peer=resolved.entity, top_msg_id=args.topic_id
                )
            )
            return {"chat": resolved.as_dict(), "topic_id": args.topic_id, "deleted": True}

        return await run_write(
            ctx,
            args,
            command="topic delete",
            action="topic.delete",
            params={"chat": resolved.as_dict(), "topic_id": args.topic_id},
            preview={"delete_topic": args.topic_id},
            execute=execute,
            destructive=True,
            confirm_label=str(args.topic_id),
        )
    finally:
        await ctx.close()


async def read_topic(ctx: TG, args) -> dict:
    await ctx.connect()
    try:
        resolved = await ctx.resolve(args.chat, kinds=["group", "supergroup"])
        _require_forum(resolved)
        max_id = int(args.max_id or 0)

        async def execute():
            await ctx.client(
                functions.messages.ReadDiscussionRequest(
                    peer=resolved.entity, msg_id=args.topic_id, read_max_id=max_id
                )
            )
            return {"chat": resolved.as_dict(), "topic_id": args.topic_id, "read_max_id": max_id}

        params = {"chat": resolved.as_dict(), "topic_id": args.topic_id, "max_id": max_id}
        return await run_write(
            ctx,
            args,
            command="topic read",
            action="topic.read",
            params=params,
            preview=params,
            execute=execute,
        )
    finally:
        await ctx.close()


async def hide_topic(ctx: TG, args) -> dict:
    await ctx.connect()
    try:
        resolved = await ctx.resolve(args.chat, kinds=["group", "supergroup"])
        _require_forum(resolved)

        async def execute():
            await ctx.client(
                functions.messages.EditForumTopicRequest(
                    peer=resolved.entity, topic_id=args.topic_id, hidden=args.hidden
                )
            )
            return {"chat": resolved.as_dict(), "topic_id": args.topic_id, "hidden": args.hidden}

        params = {"chat": resolved.as_dict(), "topic_id": args.topic_id, "hidden": args.hidden}
        return await run_write(
            ctx,
            args,
            command="topic hide",
            action="topic.hide",
            params=params,
            preview=params,
            execute=execute,
        )
    finally:
        await ctx.close()


async def reorder_topics(ctx: TG, args) -> dict:
    await ctx.connect()
    try:
        resolved = await ctx.resolve(args.chat, kinds=["group", "supergroup"])
        _require_forum(resolved)
        order = [int(x.strip()) for x in str(args.topic_ids).split(",") if x.strip()]
        if not order:
            raise CliError("VALIDATION", "--topic-ids 不能为空。")

        async def execute():
            await ctx.client(
                functions.messages.ReorderPinnedForumTopicsRequest(
                    peer=resolved.entity, order=order, force=bool(args.force)
                )
            )
            return {"chat": resolved.as_dict(), "order": order, "force": bool(args.force)}

        params = {"chat": resolved.as_dict(), "order": order, "force": bool(args.force)}
        return await run_write(
            ctx,
            args,
            command="topic reorder",
            action="topic.reorder",
            params=params,
            preview=params,
            execute=execute,
        )
    finally:
        await ctx.close()


async def set_tabs(ctx: TG, args) -> dict:
    await ctx.connect()
    try:
        resolved = await ctx.resolve(args.chat, kinds=["group", "supergroup"])
        _require_forum(resolved)
        enabled = bool(args.enabled)

        async def execute():
            await ctx.client(
                functions.channels.ToggleForumRequest(
                    channel=resolved.entity, enabled=True, tabs=enabled
                )
            )
            return {"chat": resolved.as_dict(), "tabs": enabled}

        params = {"chat": resolved.as_dict(), "tabs": enabled}
        return await run_write(
            ctx,
            args,
            command="forum set-tabs",
            action="forum.set_tabs",
            params=params,
            preview=params,
            execute=execute,
        )
    finally:
        await ctx.close()
