"""Contacts: list, search, resolve, add, delete."""
from __future__ import annotations

import random
import time

from telethon.tl import functions, types

from ..client import TG
from ..envelope import emit_read
from ..errors import CliError
from ..serialize import entity_to_dict, user_to_dict
from .common import add_limit, add_write_flags, csv, run_write


async def list_contacts(ctx: TG, args) -> dict:
    started = time.monotonic()
    await ctx.connect()
    try:
        await ctx.require_authorized()
        result = await ctx.client(functions.contacts.GetContactsRequest(hash=0))
        mutual = {
            c.user_id: bool(getattr(c, "mutual", False))
            for c in (getattr(result, "contacts", None) or [])
        }
        items = []
        for user in getattr(result, "users", None) or []:
            if not isinstance(user, types.User):
                continue
            item = user_to_dict(user)
            item["mutual"] = mutual.get(user.id, False)
            items.append(item)
            if len(items) >= args.limit:
                break
        return emit_read(
            "contacts list",
            {"count": len(items), "items": items},
            started,
            **ctx.account_meta(),
        )
    finally:
        await ctx.close()


async def search_contacts(ctx: TG, args) -> dict:
    started = time.monotonic()
    await ctx.connect()
    try:
        await ctx.require_authorized()
        query = (args.query or "").strip()
        if not query:
            raise CliError("VALIDATION", "--query 不能为空。")
        result = await ctx.client(
            functions.contacts.SearchRequest(q=query, limit=args.limit)
        )
        users = {u.id: u for u in (getattr(result, "users", None) or [])}
        chats = {c.id: c for c in (getattr(result, "chats", None) or [])}

        def peer_dict(peer):
            if isinstance(peer, types.PeerUser):
                user = users.get(peer.user_id)
                return {"kind": "user", "entity": user_to_dict(user)} if user else {"kind": "user", "id": peer.user_id}
            if isinstance(peer, types.PeerChat):
                chat = chats.get(peer.chat_id)
                return {"kind": "group", "entity": entity_to_dict(chat)} if chat else {"kind": "group", "id": peer.chat_id}
            if isinstance(peer, types.PeerChannel):
                chat = chats.get(peer.channel_id)
                return {"kind": "channel", "entity": entity_to_dict(chat)} if chat else {"kind": "channel", "id": peer.channel_id}
            return {"kind": type(peer).__name__}

        return emit_read(
            "contacts search",
            {
                "query": query,
                "my_results": [peer_dict(p) for p in (getattr(result, "my_results", None) or [])],
                "results": [peer_dict(p) for p in (getattr(result, "results", None) or [])],
            },
            started,
            **ctx.account_meta(),
        )
    finally:
        await ctx.close()


async def resolve_contact(ctx: TG, args) -> dict:
    started = time.monotonic()
    await ctx.connect()
    try:
        await ctx.require_authorized()
        if bool(args.phone) == bool(args.username):
            raise CliError("VALIDATION", "--phone 和 --username 必须二选一。")
        if args.phone:
            result = await ctx.client(functions.contacts.ResolvePhoneRequest(phone=args.phone))
        else:
            result = await ctx.client(
                functions.contacts.ResolveUsernameRequest(username=args.username.lstrip("@"))
            )
        return emit_read(
            "contacts resolve",
            {
                "peer": type(result.peer).__name__,
                "users": [user_to_dict(u) for u in (getattr(result, "users", None) or [])],
                "chats": [entity_to_dict(c) for c in (getattr(result, "chats", None) or [])],
            },
            started,
            **ctx.account_meta(),
        )
    finally:
        await ctx.close()


async def add_contact(ctx: TG, args) -> dict:
    await ctx.connect()
    try:
        await ctx.require_authorized()
        phone = (args.phone or "").strip()
        if not phone:
            raise CliError("VALIDATION", "--phone 不能为空。")
        first_name = args.first_name or ""
        last_name = args.last_name or ""

        async def execute():
            result = await ctx.client(
                functions.contacts.ImportContactsRequest(
                    contacts=[
                        types.InputPhoneContact(
                            client_id=random.getrandbits(63),
                            phone=phone,
                            first_name=first_name,
                            last_name=last_name,
                        )
                    ]
                )
            )
            return {
                "users": [user_to_dict(u) for u in (getattr(result, "users", None) or [])],
                "imported": [
                    {"user_id": i.user_id, "client_id": i.client_id}
                    for i in (getattr(result, "imported", None) or [])
                ],
                "retry_contacts": list(getattr(result, "retry_contacts", None) or []),
            }

        params = {"phone": phone, "first_name": first_name, "last_name": last_name}
        return await run_write(
            ctx,
            args,
            command="contacts add",
            action="contacts.add",
            params=params,
            preview=params,
            execute=execute,
        )
    finally:
        await ctx.close()


async def delete_contacts(ctx: TG, args) -> dict:
    await ctx.connect()
    try:
        users = []
        for value in csv(args.users):
            resolved = await ctx.resolve(value, kinds=["private", "bot"])
            if not isinstance(resolved.entity, types.User):
                raise CliError("VALIDATION", f"`{value}` 不是用户。")
            users.append(resolved)
        if not users:
            raise CliError("VALIDATION", "--users 不能为空。")

        async def execute():
            await ctx.client(
                functions.contacts.DeleteContactsRequest(id=[u.entity for u in users])
            )
            return {"removed": [u.as_dict() for u in users]}

        params = {"users": [u.as_dict() for u in users]}
        return await run_write(
            ctx,
            args,
            command="contacts delete",
            action="contacts.delete",
            params=params,
            preview=params,
            execute=execute,
        )
    finally:
        await ctx.close()
