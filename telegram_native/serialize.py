"""Small, explicit JSON shapes. Never dump raw Telethon objects."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from telethon.tl import types
from telethon.tl.custom import Dialog
from telethon.tl.custom.message import Message


def iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def entity_kind(entity: Any) -> str:
    if isinstance(entity, types.User):
        return "bot" if entity.bot else "private"
    if isinstance(entity, types.Chat):
        return "group"
    if isinstance(entity, types.Channel):
        return "channel" if entity.broadcast else "group"
    return "unknown"


def is_forum(entity: Any) -> bool:
    return bool(isinstance(entity, types.Channel) and getattr(entity, "forum", False))


def peer_id(entity: Any) -> int | None:
    if isinstance(entity, types.User):
        return entity.id
    if isinstance(entity, types.Chat):
        return -entity.id
    if isinstance(entity, types.Channel):
        return int(f"-100{entity.id}")
    return None


def username(entity: Any) -> str | None:
    return getattr(entity, "username", None)


def display_name(entity: Any) -> str:
    if isinstance(entity, types.User):
        name = " ".join(x for x in (entity.first_name, entity.last_name) if x)
        return name or (f"@{entity.username}" if entity.username else str(entity.id))
    return getattr(entity, "title", None) or str(getattr(entity, "id", "?"))


def admin_rights(value: Any) -> dict | None:
    if value is None:
        return None
    names = (
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
        "other",
        "manage_topics",
        "post_stories",
        "edit_stories",
        "delete_stories",
        "manage_direct_messages",
        "manage_ranks",
        "manage_linked_peers",
        "manage_welcome_messages",
    )
    return {name: bool(getattr(value, name, False)) for name in names if getattr(value, name, None)}


def banned_rights(value: Any) -> dict | None:
    if value is None:
        return None
    names = (
        "view_messages",
        "send_messages",
        "send_media",
        "send_stickers",
        "send_gifs",
        "send_games",
        "send_inline",
        "embed_links",
        "send_polls",
        "change_info",
        "invite_users",
        "pin_messages",
        "manage_topics",
        "send_photos",
        "send_videos",
        "send_roundvideos",
        "send_audios",
        "send_voices",
        "send_docs",
        "send_plain",
        "send_reactions",
    )
    out = {name: bool(getattr(value, name, False)) for name in names if getattr(value, name, None)}
    until = getattr(value, "until_date", None)
    if until is not None:
        out["until_date"] = iso(until)
    return out


def entity_to_dict(entity: Any, *, include_about: str | None = None) -> dict:
    out: dict[str, Any] = {
        "id": getattr(entity, "id", None),
        "peer_id": peer_id(entity),
        "kind": entity_kind(entity),
        "title": display_name(entity),
        "username": username(entity),
        "usernames": [u.username for u in getattr(entity, "usernames", None) or []],
        "is_forum": is_forum(entity),
        "verified": bool(getattr(entity, "verified", False)),
        "scam": bool(getattr(entity, "scam", False)),
        "fake": bool(getattr(entity, "fake", False)),
        "restricted": bool(getattr(entity, "restricted", False)),
        "creator": bool(getattr(entity, "creator", False)),
        "left": bool(getattr(entity, "left", False)),
        "participants_count": getattr(entity, "participants_count", None),
        "date": iso(getattr(entity, "date", None)),
    }
    rights = admin_rights(getattr(entity, "admin_rights", None))
    if rights is not None:
        out["admin_rights"] = rights
    banned = banned_rights(getattr(entity, "default_banned_rights", None))
    if banned is not None:
        out["default_banned_rights"] = banned
    if include_about is not None:
        out["about"] = include_about
    if isinstance(entity, types.User):
        out["bot"] = bool(entity.bot)
        out["first_name"] = entity.first_name
        out["last_name"] = entity.last_name
        out["phone_masked"] = _mask(entity.phone) if entity.phone else None
    if isinstance(entity, types.Channel):
        out["broadcast"] = bool(entity.broadcast)
        out["megagroup"] = bool(entity.megagroup)
        out["slowmode_enabled"] = bool(getattr(entity, "slowmode_enabled", False))
        out["join_request"] = bool(getattr(entity, "join_request", False))
        out["join_to_send"] = bool(getattr(entity, "join_to_send", False))
        out["noforwards"] = bool(getattr(entity, "noforwards", False))
        out["linked_chat_id"] = getattr(entity, "linked_chat_id", None)
    return out


def _mask(phone: str) -> str:
    if len(phone) <= 4:
        return "*" * len(phone)
    return phone[:2] + "*" * max(0, len(phone) - 5) + phone[-3:]


def user_to_dict(user: Any) -> dict:
    out = {
        "id": getattr(user, "id", None),
        "username": getattr(user, "username", None),
        "first_name": getattr(user, "first_name", None),
        "last_name": getattr(user, "last_name", None),
        "bot": bool(getattr(user, "bot", False)),
        "verified": bool(getattr(user, "verified", False)),
        "deleted": bool(getattr(user, "deleted", False)),
        "phone_masked": _mask(user.phone) if getattr(user, "phone", None) else None,
    }
    rights = admin_rights(getattr(user, "admin_rights", None))
    if rights is not None:
        out["admin_rights"] = rights
    return out


def dialog_to_dict(dialog: Dialog, *, include_last: bool = False) -> dict:
    entity = dialog.entity
    out = entity_to_dict(entity)
    out.update(
        {
            "pinned": bool(dialog.pinned),
            "archived": bool(dialog.archived),
            "unread_count": dialog.unread_count,
            "unread_mentions": dialog.unread_mentions_count,
            "folder_id": dialog.folder_id,
            "dialog_date": iso(dialog.date),
        }
    )
    if include_last and dialog.message is not None:
        out["last_message"] = message_to_dict(dialog.message, max_text=280)
    return out


def message_to_dict(msg: Message, *, max_text: int | None = None) -> dict:
    text = msg.message or ""
    clipped = text if max_text is None else text[:max_text]
    out: dict[str, Any] = {
        "id": msg.id,
        "date": iso(msg.date),
        "text": clipped,
        "text_length": len(text),
        "outgoing": bool(msg.out),
        "sender_id": getattr(msg, "sender_id", None),
        "post": bool(getattr(msg, "post", False)),
        "pinned": bool(getattr(msg, "pinned", False)),
        "silent": bool(getattr(msg, "silent", False)),
        "views": getattr(msg, "views", None),
        "forwards": getattr(msg, "forwards", None),
        "edit_date": iso(getattr(msg, "edit_date", None)),
        "grouped_id": getattr(msg, "grouped_id", None),
        "media_type": type(msg.media).__name__ if msg.media is not None else None,
    }
    reply = getattr(msg, "reply_to", None)
    if reply is not None:
        out["reply_to_msg_id"] = getattr(reply, "reply_to_msg_id", None)
        forum_topic = bool(getattr(reply, "forum_topic", False))
        out["forum_topic"] = forum_topic
        if forum_topic:
            out["topic_id"] = getattr(reply, "reply_to_top_id", None) or getattr(reply, "reply_to_msg_id", None)
    sender = getattr(msg, "sender", None)
    if sender is not None:
        out["sender_name"] = display_name(sender)
        out["sender_username"] = username(sender)
    return out


def topic_to_dict(topic: Any) -> dict:
    return {
        "id": topic.id,
        "title": topic.title,
        "date": iso(topic.date),
        "closed": bool(topic.closed),
        "pinned": bool(topic.pinned),
        "hidden": bool(topic.hidden),
        "unread_count": topic.unread_count,
        "unread_mentions": topic.unread_mentions_count,
        "top_message": topic.top_message,
        "icon_color": topic.icon_color,
        "icon_emoji_id": topic.icon_emoji_id,
    }


def participant_to_dict(participant: Any, user: Any = None) -> dict:
    if user is None:
        user = getattr(participant, "user", None)
    if user is None and isinstance(participant, types.User):
        user = participant
    out = user_to_dict(user) if user is not None else {}
    raw_type = type(participant).__name__
    participant_type = {
        "ChannelParticipant": "member",
        "ChannelParticipantSelf": "self",
        "ChannelParticipantAdmin": "admin",
        "ChannelParticipantCreator": "creator",
        "ChannelParticipantBanned": "banned",
        "ChannelParticipantLeft": "left",
    }.get(raw_type, raw_type)
    out["participant_type"] = participant_type
    rights = admin_rights(getattr(participant, "admin_rights", None))
    if rights is not None:
        out["admin_rights"] = rights
    banned = banned_rights(getattr(participant, "banned_rights", None))
    if banned is not None:
        out["banned_rights"] = banned
    out["rank"] = getattr(participant, "rank", None)
    return out


def jsonable(value: Any) -> Any:
    if isinstance(value, datetime):
        return iso(value)
    if isinstance(value, bytes):
        return f"<{len(value)} bytes>"
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, dict):
        return {str(k): jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [jsonable(v) for v in value]
    if hasattr(value, "to_dict"):
        return jsonable(value.to_dict())
    return str(value)
