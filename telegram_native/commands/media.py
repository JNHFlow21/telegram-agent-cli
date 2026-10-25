"""Media download, forwarding and album upload."""
from __future__ import annotations

import os
import re
import time
from pathlib import Path

from ..client import TG
from ..envelope import emit_read
from ..errors import CliError
from ..serialize import message_to_dict
from .common import add_chat, add_write_flags, run_write


def _safe_name(value: str | None, fallback: str) -> str:
    name = Path(value or "").name
    name = re.sub(r"[^A-Za-z0-9._-]+", "_", name).strip("._")
    return name or fallback


def _parse_ids(value: str) -> list[int]:
    ids = [int(part.strip()) for part in str(value).split(",") if part.strip()]
    if not ids:
        raise CliError("VALIDATION", "--ids 不能为空。")
    return ids


async def download(ctx: TG, args) -> dict:
    started = time.monotonic()
    await ctx.connect()
    try:
        resolved = await ctx.resolve(args.chat)
        message = await ctx.client.get_messages(resolved.entity, ids=args.id)
        if message is None:
            raise CliError("NOT_FOUND", f"找不到消息 {args.id}。")
        if message.media is None:
            raise CliError("VALIDATION", f"消息 {args.id} 没有媒体。")
        file_info = getattr(message, "file", None)
        file_name = getattr(file_info, "name", None)
        fallback = f"telegram-{resolved.peer_id or 'chat'}-{args.id}"
        name = _safe_name(file_name, fallback)
        if args.output:
            target = Path(args.output).expanduser()
            if target.is_dir():
                target = target / name
        else:
            target = ctx.settings.data_dir / "downloads" / _safe_name(resolved.title, "chat") / name
        target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        result = await ctx.client.download_media(message, file=str(target))
        if result is None:
            raise CliError("UNAVAILABLE", "Telegram 未返回可下载媒体。", retriable=True)
        path = Path(result)
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
        return emit_read(
            "messages download",
            {
                "chat": resolved.as_dict(),
                "message_id": args.id,
                "path": str(path),
                "size": path.stat().st_size,
                "media_type": type(message.media).__name__,
                "file_name": file_name,
                "mime_type": getattr(file_info, "mime_type", None),
            },
            started,
            **ctx.account_meta(),
        )
    finally:
        await ctx.close()


async def forward(ctx: TG, args) -> dict:
    await ctx.connect()
    try:
        source = await ctx.resolve(args.from_chat)
        target = await ctx.resolve(args.to_chat)
        ids = _parse_ids(args.ids)

        async def execute():
            messages = await ctx.client.forward_messages(
                target.entity,
                ids,
                source.entity,
                silent=bool(args.silent),
            )
            return {
                "from": source.as_dict(),
                "to": target.as_dict(),
                "forwarded": [message_to_dict(m) for m in messages],
            }

        params = {
            "from": source.as_dict(),
            "to": target.as_dict(),
            "ids": ids,
            "silent": bool(args.silent),
        }
        return await run_write(
            ctx,
            args,
            command="messages forward",
            action="messages.forward",
            params=params,
            preview=params,
            execute=execute,
        )
    finally:
        await ctx.close()


async def send_album(ctx: TG, args) -> dict:
    await ctx.connect()
    try:
        resolved = await ctx.resolve(args.chat)
        paths = [Path(p.strip()).expanduser() for p in str(args.files).split(",") if p.strip()]
        if not 1 <= len(paths) <= 10:
            raise CliError("VALIDATION", "相册需要 1–10 个文件。")
        for path in paths:
            if not path.is_file():
                raise CliError("VALIDATION", f"找不到文件：{path}")
            if path.stat().st_size > 2 * 1024**3:
                raise CliError("VALIDATION", f"文件超过 2 GB：{path}")
        caption = args.caption or ""
        if len(caption.encode("utf-16-le")) // 2 > 1024:
            raise CliError("VALIDATION", "相册 caption 不能超过 1024（UTF-16）。")
        if args.topic and args.reply_to:
            raise CliError("VALIDATION", "--topic 和 --reply-to 只能二选一。")
        reply_to = None if args.topic == 1 else (args.topic or args.reply_to)

        async def execute():
            messages = await ctx.client.send_file(
                resolved.entity,
                [str(p) for p in paths],
                caption=caption or None,
                silent=bool(args.silent),
                reply_to=reply_to,
            )
            if not isinstance(messages, list):
                messages = [messages]
            return {
                "chat": resolved.as_dict(),
                "count": len(messages),
                "messages": [message_to_dict(m) for m in messages],
            }

        params = {
            "chat": resolved.as_dict(),
            "files": [str(p) for p in paths],
            "caption_preview": caption[:200],
            "silent": bool(args.silent),
            "topic": args.topic,
        }
        return await run_write(
            ctx,
            args,
            command="messages send-album",
            action="messages.send_album",
            params=params,
            preview=params,
            execute=execute,
        )
    finally:
        await ctx.close()
