"""Argument parser and async dispatch for telegram-native."""
from __future__ import annotations

import argparse
import asyncio
import sys
import time
from dataclasses import replace
from pathlib import Path

from . import __version__
from .client import TG
from .config import account_session_path, load_accounts, load_settings, remove_account
from .envelope import emit_failure, emit_preview, emit_read, emit_write
from .errors import CliError, map_exception
from .commands import (
    account,
    admin,
    auth,
    bots,
    chats,
    comments,
    contacts,
    groups,
    invites,
    join_requests,
    media,
    members,
    messages,
    search,
    topics,
)
from .commands.common import add_chat, add_limit, add_write_flags


def _add_write(parser, *, destructive: bool = False) -> None:
    add_write_flags(parser, destructive=destructive)


def _add_type_filter(parser) -> None:
    parser.add_argument(
        "--type",
        action="append",
        choices=["private", "bot", "group", "supergroup", "channel", "forum"],
        help="只看指定类型，可重复。",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="telegram-native",
        description="Agent-first Telegram CLI (separate session, no GUI automation).",
    )
    parser.add_argument("--account", default=None, help="使用命名账号（独立 session）。")
    parser.add_argument("--session", default=None, help="覆盖 session 文件路径。")
    parser.add_argument("--timeout", type=float, default=20.0, help="网络超时秒数（默认 20）。")
    parser.add_argument("--version", action="version", version=f"telegram-native {__version__}")
    sub = parser.add_subparsers(dest="group", required=True)

    # auth -----------------------------------------------------------------
    p = sub.add_parser("auth", help="登录、扫码、状态、退出")
    a = p.add_subparsers(dest="action", required=True)
    q = a.add_parser("login", help="手机号 + 验证码登录（一次性）")
    q.add_argument("--phone", default=None, help="国际格式手机号；也可用 TG_PHONE。")
    q.set_defaults(handler=auth.login)
    q = a.add_parser("qr", help="用手机 Telegram 扫码授权（一次性）")
    q.add_argument("--wait", type=float, default=180.0, help="等待扫码秒数（默认 180）。")
    q.add_argument("--save", default=None, help="二维码 PNG 保存路径。")
    q.set_defaults(handler=auth.qr_login)
    q = a.add_parser("password", help="完成待确认的两步验证（扫码后如需要）")
    q.add_argument("--password-stdin", action="store_true", help="从 stdin 读一行密码，不写参数/环境。")
    q.set_defaults(handler=auth.password)
    q = a.add_parser("status", help="查看当前 session 是否已授权")
    q.set_defaults(handler=auth.status)
    q = a.add_parser("logout", help="撤销当前 CLI session")
    q.add_argument("--execute", action="store_true", help="真正撤销。")
    q.set_defaults(handler=auth.logout)

    # chats ----------------------------------------------------------------
    p = sub.add_parser("chats", help="会话列表")
    a = p.add_subparsers(dest="action", required=True)
    q = a.add_parser("list", help="列出私聊/群/频道")
    _add_type_filter(q)
    add_limit(q, default=100)
    q.add_argument("--search", default=None, help="按标题或用户名过滤。")
    q.add_argument("--archived", action="store_true", help="只看归档。")
    q.add_argument("--all", action="store_true", help="包含归档与非归档。")
    q.add_argument("--with-last", action="store_true", help="附带最后一条消息摘要。")
    q.set_defaults(handler=chats.list_chats)

    p = sub.add_parser("chats-info", help="兼容别名：等同于 chat info")
    add_chat(p)
    p.set_defaults(handler=chats.info)

    # chat -----------------------------------------------------------------
    p = sub.add_parser("chat", help="单个会话信息与设置")
    a = p.add_subparsers(dest="action", required=True)
    q = a.add_parser("info", help="会话详情")
    add_chat(q)
    q.set_defaults(handler=chats.info)
    q = a.add_parser("set-title", help="改群/频道标题")
    add_chat(q)
    q.add_argument("--title", required=True)
    _add_write(q)
    q.set_defaults(handler=chats.set_title)
    q = a.add_parser("set-about", help="改群/频道简介")
    add_chat(q)
    q.add_argument("--about", required=True)
    _add_write(q)
    q.set_defaults(handler=chats.set_about)
    q = a.add_parser("set-username", help="设置/清除公开用户名")
    add_chat(q)
    q.add_argument("--username", required=True, help="不含 @；传空字符串表示清除。")
    _add_write(q)
    q.set_defaults(handler=chats.set_username)
    q = a.add_parser("set-photo", help="设置群/频道头像")
    add_chat(q)
    q.add_argument("--photo", required=True)
    _add_write(q)
    q.set_defaults(handler=chats.set_photo)
    q = a.add_parser("set-slowmode", help="设置超级群慢速模式（0=关闭）")
    add_chat(q)
    q.add_argument("--seconds", type=int, required=True)
    _add_write(q)
    q.set_defaults(handler=chats.set_slowmode)
    q = a.add_parser("set-signatures", help="频道署名开关")
    add_chat(q)
    g = q.add_mutually_exclusive_group(required=True)
    g.add_argument("--enabled", dest="enabled", action="store_true")
    g.add_argument("--disabled", dest="enabled", action="store_false")
    q.set_defaults(enabled=None)
    _add_write(q)
    q.set_defaults(handler=chats.set_signatures)
    q = a.add_parser("set-discussion", help="频道关联/取消关联讨论群")
    add_chat(q)
    g = q.add_mutually_exclusive_group(required=True)
    g.add_argument("--group", help="作为讨论群的超级群。")
    g.add_argument("--clear", action="store_true", help="取消关联。")
    _add_write(q)
    q.set_defaults(handler=chats.set_discussion)
    q = a.add_parser("set-permissions", help="默认成员权限")
    add_chat(q)
    q.add_argument("--preset", choices=["open", "readonly", "media-only"], default=None)
    q.add_argument("--restrict", default=None, help="逗号分隔的限制字段，例如 send_plain,send_polls。")
    _add_write(q)
    q.set_defaults(handler=admin.set_permissions)
    q = a.add_parser("leave", help="退出群/频道")
    add_chat(q)
    _add_write(q, destructive=True)
    q.set_defaults(handler=chats.leave)
    q = a.add_parser("delete-dialog", help="从自己的会话列表删除（可同时清历史）")
    add_chat(q)
    q.add_argument("--revoke", action="store_true", help="同时为对方/群成员清除历史。")
    _add_write(q, destructive=True)
    q.set_defaults(handler=chats.delete_dialog)
    q = a.add_parser("mute", help="免打扰")
    add_chat(q)
    q.add_argument("--seconds", type=int, default=0, help="0 表示长期。")
    _add_write(q)
    q.set_defaults(handler=chats.mute)
    q = a.add_parser("unmute", help="取消免打扰")
    add_chat(q)
    _add_write(q)
    q.set_defaults(handler=chats.unmute)
    q = a.add_parser("archive", help="归档")
    add_chat(q)
    _add_write(q)
    q.set_defaults(handler=chats.archive)
    q = a.add_parser("unarchive", help="取消归档")
    add_chat(q)
    _add_write(q)
    q.set_defaults(handler=chats.unarchive)
    q = a.add_parser("pin", help="置顶会话")
    add_chat(q)
    _add_write(q)
    q.set_defaults(handler=chats.pin_dialog)
    q = a.add_parser("unpin", help="取消置顶")
    add_chat(q)
    _add_write(q)
    q.set_defaults(handler=chats.unpin_dialog)
    q = a.add_parser("block", help="拉黑私聊/Bot")
    add_chat(q)
    _add_write(q, destructive=True)
    q.set_defaults(handler=chats.block)
    q = a.add_parser("unblock", help="解除拉黑")
    add_chat(q)
    _add_write(q)
    q.set_defaults(handler=chats.unblock)

    # group / channel ------------------------------------------------------
    p = sub.add_parser("group", help="群组管理")
    a = p.add_subparsers(dest="action", required=True)
    q = a.add_parser("create", help="创建群（默认超级群，可开话题）")
    q.add_argument("--title", required=True)
    q.add_argument("--about", default="")
    q.add_argument("--users", default="", help="逗号分隔的 @username / id。")
    q.add_argument("--forum", action="store_true", help="创建时直接开启话题。")
    q.add_argument("--basic", action="store_true", help="创建普通群（不支持话题）。")
    q.add_argument("--username", default=None, help="超级群的公开用户名。")
    q.add_argument("--photo", default=None)
    _add_write(q)
    q.set_defaults(handler=groups.create_group)
    q = a.add_parser("set-join-request", help="进群需管理员批准")
    add_chat(q)
    g = q.add_mutually_exclusive_group(required=True)
    g.add_argument("--enabled", dest="enabled", action="store_true")
    g.add_argument("--disabled", dest="enabled", action="store_false")
    q.add_argument("--apply-to-invites", action="store_true")
    q.set_defaults(enabled=None)
    _add_write(q)
    q.set_defaults(handler=groups.set_join_request)
    q = a.add_parser("set-join-to-send", help="必须先加入才能发言")
    add_chat(q)
    g = q.add_mutually_exclusive_group(required=True)
    g.add_argument("--enabled", dest="enabled", action="store_true")
    g.add_argument("--disabled", dest="enabled", action="store_false")
    q.set_defaults(enabled=None)
    _add_write(q)
    q.set_defaults(handler=groups.set_join_to_send)
    q = a.add_parser("migrate", help="普通群 → 超级群（不可逆）")
    add_chat(q)
    _add_write(q, destructive=True)
    q.set_defaults(handler=groups.migrate_group)
    q = a.add_parser("delete", help="删除群/超级群（仅创建者）")
    add_chat(q)
    _add_write(q, destructive=True)
    q.set_defaults(handler=groups.delete_group_or_channel)

    p = sub.add_parser("channel", help="频道管理")
    a = p.add_subparsers(dest="action", required=True)
    q = a.add_parser("create", help="创建广播频道")
    q.add_argument("--title", required=True)
    q.add_argument("--about", default="")
    q.add_argument("--username", default=None)
    q.add_argument("--photo", default=None)
    q.add_argument("--users", default="", help="创建后邀请的用户。")
    _add_write(q)
    q.set_defaults(handler=groups.create_channel)
    q = a.add_parser("delete", help="删除频道（仅创建者）")
    add_chat(q)
    _add_write(q, destructive=True)
    q.set_defaults(handler=groups.delete_group_or_channel)

    # forum ----------------------------------------------------------------
    p = sub.add_parser("forum", help="话题（Topics）总开关")
    a = p.add_subparsers(dest="action", required=True)
    q = a.add_parser("status", help="查看是否开启话题")
    add_chat(q)
    q.set_defaults(handler=topics.status)
    q = a.add_parser("enable", help="开启话题")
    add_chat(q)
    _add_write(q)
    q.set_defaults(handler=topics.enable)
    q = a.add_parser("disable", help="关闭话题（会隐藏所有话题）")
    add_chat(q)
    _add_write(q, destructive=True)
    q.set_defaults(handler=topics.disable)
    q = a.add_parser("set-tabs", help="话题 UI：tabs / 列表")
    add_chat(q)
    g = q.add_mutually_exclusive_group(required=True)
    g.add_argument("--enabled", dest="enabled", action="store_true")
    g.add_argument("--disabled", dest="enabled", action="store_false")
    q.set_defaults(enabled=None)
    _add_write(q)
    q.set_defaults(handler=topics.set_tabs)

    # topics ---------------------------------------------------------------
    p = sub.add_parser("topic", help="话题管理")
    a = p.add_subparsers(dest="action", required=True)
    q = a.add_parser("list", help="列出话题")
    add_chat(q)
    add_limit(q, default=100)
    q.add_argument("--search", default=None)
    q.add_argument("--offset-topic", type=int, default=0)
    q.add_argument("--all", action="store_true", help="自动翻页，最多取 --limit 条。")
    q.set_defaults(handler=topics.list_topics)
    q = a.add_parser("create", help="创建话题")
    add_chat(q)
    q.add_argument("--title", required=True)
    q.add_argument("--icon-color", type=int, default=None)
    q.add_argument("--icon-emoji-id", type=int, default=None)
    _add_write(q)
    q.set_defaults(handler=topics.create_topic)
    q = a.add_parser("edit", help="编辑话题标题/图标")
    add_chat(q)
    q.add_argument("--topic-id", type=int, required=True)
    q.add_argument("--title", default=None)
    q.add_argument("--icon-emoji-id", type=int, default=None)
    q.add_argument("--icon-color", type=int, default=None)
    _add_write(q)
    q.set_defaults(handler=topics.edit_topic)
    q = a.add_parser("close", help="关闭话题")
    add_chat(q)
    q.add_argument("--topic-id", type=int, required=True)
    _add_write(q)
    q.set_defaults(handler=topics.close_topic)
    q = a.add_parser("reopen", help="重新打开话题")
    add_chat(q)
    q.add_argument("--topic-id", type=int, required=True)
    _add_write(q)
    q.set_defaults(handler=topics.reopen_topic)
    q = a.add_parser("pin", help="置顶话题")
    add_chat(q)
    q.add_argument("--topic-id", type=int, required=True)
    _add_write(q)
    q.set_defaults(handler=topics.pin_topic)
    q = a.add_parser("unpin", help="取消置顶话题")
    add_chat(q)
    q.add_argument("--topic-id", type=int, required=True)
    _add_write(q)
    q.set_defaults(handler=topics.unpin_topic)
    q = a.add_parser("delete", help="删除话题及其消息")
    add_chat(q)
    q.add_argument("--topic-id", type=int, required=True)
    _add_write(q, destructive=True)
    q.set_defaults(handler=topics.delete_topic)
    q = a.add_parser("read", help="标记话题已读")
    add_chat(q)
    q.add_argument("--topic-id", type=int, required=True)
    q.add_argument("--max-id", type=int, default=None)
    _add_write(q)
    q.set_defaults(handler=topics.read_topic)
    q = a.add_parser("hide", help="隐藏/显示 General 话题")
    add_chat(q)
    q.add_argument("--topic-id", type=int, default=1)
    g = q.add_mutually_exclusive_group(required=True)
    g.add_argument("--hide", dest="hidden", action="store_true")
    g.add_argument("--show", dest="hidden", action="store_false")
    q.set_defaults(hidden=None)
    _add_write(q)
    q.set_defaults(handler=topics.hide_topic)
    q = a.add_parser("reorder", help="重排置顶话题")
    add_chat(q)
    q.add_argument("--topic-ids", required=True, help="逗号分隔，按目标顺序。")
    q.add_argument("--force", action="store_true")
    _add_write(q)
    q.set_defaults(handler=topics.reorder_topics)

    # members --------------------------------------------------------------
    p = sub.add_parser("members", help="成员管理")
    a = p.add_subparsers(dest="action", required=True)
    q = a.add_parser("list", help="成员列表")
    add_chat(q)
    add_limit(q, default=100)
    q.add_argument("--filter", choices=["all", "admins", "bots", "recent", "kicked"], default="all")
    q.add_argument("--search", default="")
    q.set_defaults(handler=members.list_members)
    q = a.add_parser("add", help="邀请成员")
    add_chat(q)
    q.add_argument("--users", required=True, help="逗号分隔。")
    _add_write(q)
    q.set_defaults(handler=members.add_members)
    q = a.add_parser("remove", help="移出成员（可再次加入）")
    add_chat(q)
    q.add_argument("--users", required=True)
    _add_write(q, destructive=True)
    q.set_defaults(handler=members.remove_members)
    q = a.add_parser("ban", help="封禁成员")
    add_chat(q)
    q.add_argument("--users", required=True)
    q.add_argument("--days", type=int, default=None, help="不填表示永久。")
    _add_write(q, destructive=True)
    q.set_defaults(handler=members.ban_members)
    q = a.add_parser("unban", help="解除封禁")
    add_chat(q)
    q.add_argument("--users", required=True)
    _add_write(q)
    q.set_defaults(handler=members.unban_members)

    # admins ---------------------------------------------------------------
    p = sub.add_parser("admins", help="管理员管理")
    a = p.add_subparsers(dest="action", required=True)
    q = a.add_parser("list", help="管理员列表")
    add_chat(q)
    add_limit(q, default=100)
    q.add_argument("--search", default="")
    q.set_defaults(handler=members.list_admins)
    q = a.add_parser("promote", help="设为管理员/调整权限")
    add_chat(q)
    q.add_argument("--users", required=True)
    q.add_argument("--rights", default=None, help="逗号分隔；默认邀请/置顶/删消息/封禁(+话题)。")
    q.add_argument("--rank", default=None, help="自定义头衔。")
    _add_write(q)
    q.set_defaults(handler=members.promote_admins)
    q = a.add_parser("demote", help="撤销管理员")
    add_chat(q)
    q.add_argument("--users", required=True)
    _add_write(q, destructive=True)
    q.set_defaults(handler=members.demote_admins)

    p = sub.add_parser("admin-log", help="管理员操作日志")
    a = p.add_subparsers(dest="action", required=True)
    q = a.add_parser("list", help="读取管理员日志")
    add_chat(q)
    add_limit(q, default=100)
    q.add_argument("--query", default="")
    q.add_argument("--full", action="store_true", help="输出完整 action（可能较大）")
    q.set_defaults(handler=admin.admin_log)

    # invite ---------------------------------------------------------------
    p = sub.add_parser("invite", help="邀请链接")
    a = p.add_subparsers(dest="action", required=True)
    q = a.add_parser("link", help="创建/导出邀请链接")
    add_chat(q)
    q.add_argument("--expire-days", type=int, default=None)
    q.add_argument("--usage-limit", type=int, default=None)
    q.add_argument("--request-needed", action="store_true", help="需要管理员批准。")
    q.add_argument("--title", default=None)
    _add_write(q)
    q.set_defaults(handler=groups.invite_link)
    q = a.add_parser("revoke", help="撤销邀请链接")
    add_chat(q)
    q.add_argument("--link", required=True)
    _add_write(q, destructive=True)
    q.set_defaults(handler=groups.invite_revoke)
    q = a.add_parser("list", help="列出已创建邀请链接")
    add_chat(q)
    add_limit(q, default=100)
    q.add_argument("--revoked", action="store_true", help="只看已撤销链接。")
    q.set_defaults(handler=invites.list_invites)
    q = a.add_parser("edit", help="修改邀请链接有效期/次数/审批/标题")
    add_chat(q)
    q.add_argument("--link", required=True)
    q.add_argument("--expire-days", type=int, default=None)
    q.add_argument("--no-expire", action="store_true", help="清除过期时间。")
    q.add_argument("--usage-limit", type=int, default=None)
    g = q.add_mutually_exclusive_group()
    g.add_argument("--request-needed", dest="request_needed", action="store_true")
    g.add_argument("--no-request", dest="request_needed", action="store_false")
    q.set_defaults(request_needed=None)
    q.add_argument("--title", default=None)
    _add_write(q)
    q.set_defaults(handler=invites.edit_invite)
    q = a.add_parser("delete", help="删除邀请链接（不可恢复）")
    add_chat(q)
    q.add_argument("--link", required=True)
    _add_write(q, destructive=True)
    q.set_defaults(handler=invites.delete_invite)
    q = a.add_parser("importers", help="查看邀请链接导入者")
    add_chat(q)
    q.add_argument("--link", default=None, help="不填表示所有链接。")
    q.add_argument("--pending", action="store_true", help="只看待批准申请。")
    add_limit(q, default=100)
    q.set_defaults(handler=invites.list_importers)
    q = a.add_parser("join", help="加入群/频道")
    q.add_argument("--link", required=True)
    _add_write(q)
    q.set_defaults(handler=groups.invite_join)
    q = a.add_parser("info", help="查看邀请链接")
    q.add_argument("--link", required=True)
    q.set_defaults(handler=groups.invite_info)

    # join requests --------------------------------------------------------
    p = sub.add_parser("join-requests", help="进群申请：列出/批准/拒绝")
    a = p.add_subparsers(dest="action", required=True)
    q = a.add_parser("list", help="待批准申请")
    add_chat(q)
    add_limit(q, default=100)
    q.add_argument("--search", default="")
    q.set_defaults(handler=join_requests.list_requests)
    q = a.add_parser("approve", help="批准申请")
    add_chat(q)
    q.add_argument("--users", required=True)
    _add_write(q)
    q.set_defaults(handler=join_requests.approve_requests)
    q = a.add_parser("reject", help="拒绝申请")
    add_chat(q)
    q.add_argument("--users", required=True)
    _add_write(q, destructive=True)
    q.set_defaults(handler=join_requests.reject_requests)
    q = a.add_parser("approve-all", help="批准全部待处理申请")
    add_chat(q)
    q.add_argument("--link", default=None, help="只处理某个邀请链接的申请。")
    _add_write(q, destructive=True)
    q.set_defaults(handler=join_requests.approve_all)
    q = a.add_parser("reject-all", help="拒绝全部待处理申请")
    add_chat(q)
    q.add_argument("--link", default=None)
    _add_write(q, destructive=True)
    q.set_defaults(handler=join_requests.reject_all)

    # comments -------------------------------------------------------------
    p = sub.add_parser("comments", help="频道帖子评论区")
    a = p.add_subparsers(dest="action", required=True)
    q = a.add_parser("info", help="查看评论区对应讨论群")
    add_chat(q)
    q.add_argument("--post-id", type=int, required=True)
    q.set_defaults(handler=comments.info)
    q = a.add_parser("list", help="列出评论")
    add_chat(q)
    q.add_argument("--post-id", type=int, required=True)
    add_limit(q, default=50)
    q.add_argument("--offset-id", type=int, default=0)
    q.set_defaults(handler=comments.list_comments)
    q = a.add_parser("reply", help="回复频道帖子/评论")
    add_chat(q)
    q.add_argument("--post-id", type=int, required=True)
    q.add_argument("--text", required=True)
    q.add_argument("--reply-to", type=int, default=None, help="回复某条评论的 id。")
    _add_write(q)
    q.set_defaults(handler=comments.reply)

    # messages -------------------------------------------------------------
    p = sub.add_parser("messages", help="消息读写")
    a = p.add_subparsers(dest="action", required=True)
    q = a.add_parser("list", help="最近消息")
    add_chat(q)
    add_limit(q, default=50)
    q.add_argument("--offset-id", type=int, default=0)
    q.add_argument("--search", default=None)
    q.add_argument("--topic", type=int, default=None)
    q.add_argument("--reverse", action="store_true")
    q.add_argument("--max-text", type=int, default=None, help="每条正文截断长度。")
    q.set_defaults(handler=messages.list_messages)
    q = a.add_parser("search", help="全文搜索")
    add_chat(q)
    add_limit(q, default=50)
    q.add_argument("--query", required=True)
    q.add_argument("--topic", type=int, default=None)
    q.add_argument("--max-text", type=int, default=None)
    q.set_defaults(handler=messages.search_messages)
    q = a.add_parser("get", help="按 id 取一条")
    add_chat(q)
    q.add_argument("--id", type=int, required=True)
    q.set_defaults(handler=messages.get_message)
    q = a.add_parser("send", help="发送文本或文件")
    add_chat(q)
    q.add_argument("--text", default=None)
    q.add_argument("--text-file", default=None)
    q.add_argument("--file", default=None)
    q.add_argument("--reply-to", type=int, default=None)
    q.add_argument("--topic", type=int, default=None)
    q.add_argument("--silent", action="store_true")
    q.add_argument("--no-link-preview", action="store_true")
    q.add_argument("--as-document", action="store_true")
    _add_write(q)
    q.set_defaults(handler=messages.send_message)
    q = a.add_parser("edit", help="编辑自己发送的消息")
    add_chat(q)
    q.add_argument("--id", type=int, required=True)
    q.add_argument("--text", default=None)
    q.add_argument("--text-file", default=None)
    _add_write(q)
    q.set_defaults(handler=messages.edit_message)
    q = a.add_parser("delete", help="删除消息")
    add_chat(q)
    q.add_argument("--ids", required=True, help="逗号分隔的消息 id。")
    q.add_argument("--keep-for-me", action="store_true", help="只从自己这边删除。")
    _add_write(q, destructive=True)
    q.set_defaults(handler=messages.delete_messages)
    q = a.add_parser("pin", help="置顶消息")
    add_chat(q)
    q.add_argument("--id", type=int, required=True)
    q.add_argument("--notify", action="store_true")
    _add_write(q)
    q.set_defaults(handler=messages.pin_message)
    q = a.add_parser("unpin", help="取消置顶")
    add_chat(q)
    q.add_argument("--id", type=int, required=True)
    q.add_argument("--notify", action="store_true")
    _add_write(q)
    q.set_defaults(handler=messages.unpin_message)
    q = a.add_parser("mark-read", help="标记已读")
    add_chat(q)
    q.add_argument("--max-id", type=int, default=None)
    _add_write(q)
    q.set_defaults(handler=messages.mark_read)
    q = a.add_parser("download", help="下载一条消息的媒体")
    add_chat(q)
    q.add_argument("--id", type=int, required=True)
    q.add_argument("--output", default=None, help="文件或目录路径；默认私有 downloads/。")
    q.set_defaults(handler=media.download)
    q = a.add_parser("forward", help="转发消息（可整组相册）")
    q.add_argument("--from", dest="from_chat", required=True)
    q.add_argument("--to", dest="to_chat", required=True)
    q.add_argument("--ids", required=True, help="逗号分隔的消息 id。")
    q.add_argument("--silent", action="store_true")
    _add_write(q)
    q.set_defaults(handler=media.forward)
    q = a.add_parser("send-album", help="发送 1–10 个文件的相册")
    add_chat(q)
    q.add_argument("--files", required=True, help="逗号分隔的本地文件。")
    q.add_argument("--caption", default=None)
    q.add_argument("--silent", action="store_true")
    q.add_argument("--topic", type=int, default=None)
    q.add_argument("--reply-to", type=int, default=None)
    _add_write(q)
    q.set_defaults(handler=media.send_album)

    # bots -----------------------------------------------------------------
    p = sub.add_parser("bot", help="BotFather：创建/编辑/管理 Bot")
    a = p.add_subparsers(dest="action", required=True)
    q = a.add_parser("create", help="通过 @BotFather 创建 Bot")
    q.add_argument("--name", required=True, help="Bot 显示名。")
    q.add_argument("--username", required=True, help="以 bot 结尾的全局用户名。")
    _add_write(q)
    q.set_defaults(handler=bots.create_bot)
    q = a.add_parser("list", help="列出账号下的 Bot")
    q.set_defaults(handler=bots.list_bots)
    q = a.add_parser("token", help="读取 Bot token")
    q.add_argument("--username", required=True)
    q.add_argument("--show-secret", action="store_true", help="在输出中显示完整 token；默认掩码。")
    _add_write(q)
    q.set_defaults(handler=bots.get_token)
    q = a.add_parser("set-name", help="改 Bot 显示名")
    q.add_argument("--username", required=True)
    q.add_argument("--name", required=True)
    _add_write(q)
    q.set_defaults(handler=bots.set_name)
    q = a.add_parser("set-about", help="改 Bot about")
    q.add_argument("--username", required=True)
    q.add_argument("--about", required=True)
    _add_write(q)
    q.set_defaults(handler=bots.set_about)
    q = a.add_parser("set-description", help="改 Bot description")
    q.add_argument("--username", required=True)
    q.add_argument("--description", required=True)
    _add_write(q)
    q.set_defaults(handler=bots.set_description)
    q = a.add_parser("set-commands", help="设置命令列表")
    q.add_argument("--username", required=True)
    q.add_argument("--commands-file", required=True, help="每行 `command - description`。")
    _add_write(q)
    q.set_defaults(handler=bots.set_commands)
    q = a.add_parser("revoke", help="撤销并重发 token")
    q.add_argument("--username", required=True)
    _add_write(q, destructive=True)
    q.set_defaults(handler=bots.revoke_token)
    q = a.add_parser("delete", help="删除 Bot")
    q.add_argument("--username", required=True)
    _add_write(q, destructive=True)
    q.set_defaults(handler=bots.delete_bot)

    # search ---------------------------------------------------------------
    p = sub.add_parser("search", help="全局搜索")
    a = p.add_subparsers(dest="action", required=True)
    q = a.add_parser("global", help="跨会话搜索消息")
    q.add_argument("--query", required=True)
    add_limit(q, default=50)
    q.add_argument("--type", choices=["private", "group", "channel"], default=None)
    q.set_defaults(handler=search.global_search)

    # contacts -------------------------------------------------------------
    p = sub.add_parser("contacts", help="联系人")
    a = p.add_subparsers(dest="action", required=True)
    q = a.add_parser("list", help="联系人列表")
    add_limit(q, default=200)
    q.set_defaults(handler=contacts.list_contacts)
    q = a.add_parser("search", help="按用户名/姓名搜索")
    q.add_argument("--query", required=True)
    add_limit(q, default=50)
    q.set_defaults(handler=contacts.search_contacts)
    q = a.add_parser("resolve", help="按手机号/用户名解析")
    g = q.add_mutually_exclusive_group(required=True)
    g.add_argument("--phone")
    g.add_argument("--username")
    q.set_defaults(handler=contacts.resolve_contact)
    q = a.add_parser("add", help="按手机号导入联系人")
    q.add_argument("--phone", required=True)
    q.add_argument("--first-name", default="")
    q.add_argument("--last-name", default="")
    _add_write(q)
    q.set_defaults(handler=contacts.add_contact)
    q = a.add_parser("delete", help="删除联系人")
    q.add_argument("--users", required=True, help="逗号分隔的 @username / id。")
    _add_write(q)
    q.set_defaults(handler=contacts.delete_contacts)

    # account --------------------------------------------------------------
    p = sub.add_parser("account", help="账号资料与登录设备")
    a = p.add_subparsers(dest="action", required=True)
    q = a.add_parser("profile", help="查看自己的资料")
    q.set_defaults(handler=account.profile)
    q = a.add_parser("set-name", help="修改姓名")
    q.add_argument("--first-name", default=None)
    q.add_argument("--last-name", default=None)
    _add_write(q)
    q.set_defaults(handler=account.set_name)
    q = a.add_parser("set-bio", help="修改 bio")
    q.add_argument("--about", required=True)
    _add_write(q)
    q.set_defaults(handler=account.set_bio)
    q = a.add_parser("set-username", help="修改公开用户名")
    q.add_argument("--username", required=True, help="不含 @；空串表示清除。")
    _add_write(q)
    q.set_defaults(handler=account.set_username)
    q = a.add_parser("set-photo", help="设置头像")
    q.add_argument("--photo", required=True)
    _add_write(q)
    q.set_defaults(handler=account.set_photo)
    q = a.add_parser("devices", help="登录设备")
    da = q.add_subparsers(dest="devices_action", required=True)
    dq = da.add_parser("list", help="设备列表")
    dq.set_defaults(handler=account.devices_list)
    dq = da.add_parser("revoke", help="撤销一个设备")
    dq.add_argument("--hash", type=int, required=True)
    _add_write(dq, destructive=True)
    dq.set_defaults(handler=account.devices_revoke)
    dq = da.add_parser("revoke-others", help="撤销除当前 CLI 外的所有设备")
    _add_write(dq, destructive=True)
    dq.set_defaults(handler=account.devices_revoke_others)
    q = a.add_parser("web-sessions", help="网页登录会话")
    wa = q.add_subparsers(dest="web_action", required=True)
    wq = wa.add_parser("list", help="网页会话列表")
    wq.set_defaults(handler=account.web_sessions_list)
    wq = wa.add_parser("revoke", help="撤销一个网页会话")
    wq.add_argument("--hash", type=int, required=True)
    _add_write(wq, destructive=True)
    wq.set_defaults(handler=account.web_sessions_revoke)
    wq = wa.add_parser("revoke-all", help="撤销全部网页会话")
    _add_write(wq, destructive=True)
    wq.set_defaults(handler=account.web_sessions_revoke_all)

    # local helpers --------------------------------------------------------
    p = sub.add_parser("config", help="本地配置")
    a = p.add_subparsers(dest="action", required=True)
    q = a.add_parser("show", help="显示本地配置（api_hash 掩码）")
    q.set_defaults(handler=cmd_config_show)
    q = a.add_parser("accounts", help="命名账号注册表")
    aa = q.add_subparsers(dest="accounts_action", required=True)
    aq = aa.add_parser("list", help="列出命名账号")
    aq.set_defaults(handler=cmd_accounts_list)
    aq = aa.add_parser("remove", help="从注册表移除账号（不删 session 文件）")
    aq.add_argument("--name", required=True)
    aq.add_argument("--execute", action="store_true")
    aq.set_defaults(handler=cmd_accounts_remove)

    p = sub.add_parser("docs", help="输出 USAGE.md 路径/内容")
    p.add_argument("--full", action="store_true", help="输出完整内容。")
    p.set_defaults(handler=cmd_docs)

    p = sub.add_parser("commands", help="列出命令清单（agent 可发现性）")
    p.set_defaults(handler=cmd_commands)

    return parser


def _command_name(args) -> str:
    parts = [getattr(args, "group", "telegram-native")]
    action = getattr(args, "action", None)
    if action:
        parts.append(action)
    for attr in ("devices_action", "web_action", "accounts_action"):
        nested = getattr(args, attr, None)
        if nested:
            parts.append(nested)
    return " ".join(parts)


async def _dispatch(args) -> dict:
    account_name = getattr(args, "account", None)
    if account_name and getattr(args, "session", None):
        return emit_failure(
            _command_name(args),
            "VALIDATION",
            "--account 和 --session 只能二选一。",
            getattr(args, "_started", None),
        )
    try:
        settings = load_settings()
        if account_name:
            settings = replace(settings, session_path=account_session_path(account_name))
        elif getattr(args, "session", None):
            settings = replace(settings, session_path=Path(args.session).expanduser())
        ctx = TG(
            settings,
            timeout=float(getattr(args, "timeout", 20.0)),
            account_name=account_name,
        )
    except Exception as exc:  # noqa: BLE001 - config/proxy validation
        mapped = map_exception(exc)
        return emit_failure(
            _command_name(args),
            mapped.code,
            mapped.message,
            getattr(args, "_started", None),
            retriable=mapped.retriable,
            details=mapped.details,
            outcome=mapped.outcome,
        )
    try:
        return await args.handler(ctx, args)
    except Exception as exc:  # noqa: BLE001
        mapped = map_exception(exc)
        return emit_failure(
            _command_name(args),
            mapped.code,
            mapped.message,
            getattr(args, "_started", None),
            retriable=mapped.retriable,
            details=mapped.details,
            outcome=mapped.outcome,
            **ctx.account_meta(),
        )
    finally:
        await ctx.close()


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    args._started = time.monotonic()
    try:
        envelope = asyncio.run(_dispatch(args))
    except KeyboardInterrupt:
        envelope = emit_failure(_command_name(args), "UNAVAILABLE", "用户中断。", args._started)
    return 0 if envelope.get("ok") else 1


async def cmd_config_show(ctx: TG, args) -> dict:
    started = time.monotonic()
    settings = ctx.settings
    masked_hash = settings.api_hash[:4] + "…" + settings.api_hash[-4:] if len(settings.api_hash) > 8 else "…"
    return emit_read(
        "config show",
        {
            "api_id": settings.api_id,
            "api_hash_masked": masked_hash,
            "session": str(settings.session_path),
            "data_dir": str(settings.data_dir),
            "config_dir": str(settings.config_dir),
            "account": ctx.account_name,
            "proxy_configured": bool(settings.proxy),
            "proxy_scheme": settings.proxy[0] if settings.proxy else None,
        },
        started,
    )


async def cmd_accounts_list(ctx: TG, args) -> dict:
    started = time.monotonic()
    return emit_read("config accounts list", {"accounts": load_accounts()}, started)


async def cmd_accounts_remove(ctx: TG, args) -> dict:
    started = time.monotonic()
    if not getattr(args, "execute", False):
        return emit_preview(
            "config accounts remove",
            {"status": "preview", "name": args.name, "note": "只移除注册表，不删除 session 文件。"},
            started,
        )
    removed = remove_account(args.name)
    return emit_write(
        "config accounts remove",
        {"status": "removed" if removed else "noop", "name": args.name},
        started,
    )


async def cmd_docs(ctx: TG, args) -> dict:
    started = time.monotonic()
    root = Path(__file__).resolve().parents[1]
    path = root / "USAGE.md"
    data = {"path": str(path), "exists": path.is_file()}
    if args.full and path.is_file():
        data["content"] = path.read_text("utf-8")
    return emit_read("docs", data, started)


COMMANDS = [
    ("auth login", "一次性手机号登录"),
    ("auth password", "完成待确认的两步验证"),
    ("auth qr", "一次性扫码登录"),
    ("auth status", "查看授权状态"),
    ("auth logout", "撤销 CLI session"),
    ("chats list", "列出私聊/群/频道"),
    ("chat info", "会话详情"),
    ("chat set-title|set-about|set-username|set-photo", "群/频道资料"),
    ("chat set-slowmode|set-signatures|set-discussion|set-permissions", "群/频道高级设置"),
    ("chat mute|unmute|archive|unarchive|pin|unpin|block|unblock", "会话设置"),
    ("group create|migrate|delete|set-join-request|set-join-to-send", "群组创建/迁移/删除/加入策略"),
    ("channel create|delete", "频道创建/删除"),
    ("forum status|enable|disable|set-tabs", "话题总开关/UI"),
    ("topic list|create|edit|close|reopen|pin|unpin|delete|read|hide|reorder", "话题管理"),
    ("members list|add|remove|ban|unban", "成员管理"),
    ("admins list|promote|demote", "管理员管理"),
    ("invite link|list|edit|delete|importers|revoke|join|info", "邀请链接全生命周期"),
    ("join-requests list|approve|reject|approve-all|reject-all", "进群申请审批"),
    ("messages list|search|get|send|send-album|download|forward|edit|delete|pin|unpin|mark-read", "消息读写与媒体"),
    ("comments info|list|reply", "频道帖子评论区"),
    ("admin-log list", "管理员操作日志"),
    ("bot create|list|token|set-name|set-about|set-description|set-commands|revoke|delete", "BotFather"),
    ("search global", "跨会话全局搜索"),
    ("contacts list|search|resolve|add|delete", "联系人"),
    ("account profile|set-name|set-bio|set-username|set-photo", "账号资料"),
    ("account devices list|revoke|revoke-others", "登录设备"),
    ("account web-sessions list|revoke|revoke-all", "网页会话"),
    ("config show", "本地配置/代理状态"),
    ("config accounts list|remove", "命名账号注册表"),
    ("docs", "使用说明"),
]


async def cmd_commands(ctx: TG, args) -> dict:
    started = time.monotonic()
    return emit_read(
        "commands",
        {"count": len(COMMANDS), "items": [{"command": c, "description": d} for c, d in COMMANDS]},
        started,
    )
