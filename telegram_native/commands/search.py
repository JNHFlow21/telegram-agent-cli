"""Global message search."""
from __future__ import annotations

import time

from telethon.tl import functions, types

from ..client import TG, kind_detail
from ..envelope import emit_read
from ..errors import CliError
from ..serialize import entity_to_dict, message_to_dict, user_to_dict
from .common import add_limit


async def global_search(ctx: TG, args) -> dict:
    started = time.monotonic()
    await ctx.connect()
    try:
        await ctx.require_authorized()
        query = (args.query or "").strip()
        if not query:
            raise CliError("VALIDATION", "--query 不能为空。")
        type_filter = getattr(args, "type", None)
        result = await ctx.client(
            functions.messages.SearchGlobalRequest(
                q=query,
                filter=types.InputMessagesFilterEmpty(),
                min_date=None,
                max_date=None,
                offset_rate=0,
                offset_peer=types.InputPeerEmpty(),
                offset_id=0,
                limit=args.limit,
                broadcasts_only=True if type_filter == "channel" else None,
                groups_only=True if type_filter == "group" else None,
                users_only=True if type_filter == "private" else None,
            )
        )
        chats = {c.id: c for c in (getattr(result, "chats", None) or [])}
        users = {u.id: u for u in (getattr(result, "users", None) or [])}
        items = []
        for message in getattr(result, "messages", None) or []:
            if not isinstance(message, types.Message):
                continue
            item = message_to_dict(message)
            peer = getattr(message, "peer_id", None)
            chat = None
            if isinstance(peer, types.PeerChannel):
                chat = chats.get(peer.channel_id)
            elif isinstance(peer, types.PeerChat):
                chat = chats.get(peer.chat_id)
            elif isinstance(peer, types.PeerUser):
                chat = users.get(peer.user_id)
            if chat is not None:
                item["chat"] = entity_to_dict(chat)
            sender = getattr(message, "from_id", None)
            if isinstance(sender, types.PeerUser):
                user = users.get(sender.user_id)
                if user is not None:
                    item["sender"] = user_to_dict(user)
            items.append(item)
        return emit_read(
            "search global",
            {"query": query, "count": len(items), "items": items},
            started,
            **ctx.account_meta(),
        )
    finally:
        await ctx.close()
