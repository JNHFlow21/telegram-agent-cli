from __future__ import annotations

import unittest
from datetime import datetime, timezone
from pathlib import Path

from telethon.tl import types

from telegram_native.cli import build_parser
from telegram_native.commands.account import _authorization_to_dict, _web_auth_to_dict
from telegram_native.commands.invites import _invite_to_dict, normalize_invite_link
from telegram_native.commands.media import _parse_ids, _safe_name
from unittest.mock import patch

from telegram_native.config import parse_proxy, save_account, load_accounts, remove_account, account_session_path


class InviteLinkTests(unittest.TestCase):
    def test_normalize(self):
        self.assertEqual(normalize_invite_link("t.me/+AbC"), "https://t.me/+AbC")
        self.assertEqual(normalize_invite_link("+AbC"), "https://t.me/+AbC")
        self.assertEqual(
            normalize_invite_link("tg://join?invite=AbC"), "https://t.me/+AbC"
        )
        self.assertEqual(
            normalize_invite_link("https://t.me/joinchat/AbC"),
            "https://t.me/joinchat/AbC",
        )

    def test_invite_dict(self):
        invite = types.ChatInviteExported(
            link="https://t.me/+AbC",
            admin_id=1,
            date=datetime(2026, 1, 2, tzinfo=timezone.utc),
            revoked=False,
            request_needed=True,
            usage_limit=10,
            usage=2,
            title="test",
        )
        data = _invite_to_dict(invite)
        self.assertEqual(data["link"], "https://t.me/+AbC")
        self.assertTrue(data["request_needed"])
        self.assertEqual(data["usage_limit"], 10)


class MediaTests(unittest.TestCase):
    def test_parse_ids(self):
        self.assertEqual(_parse_ids("1, 2,3"), [1, 2, 3])
        with self.assertRaises(Exception):
            _parse_ids("")

    def test_safe_name(self):
        self.assertEqual(_safe_name("../../etc/passwd", "fallback"), "passwd")
        self.assertEqual(_safe_name("", "fallback"), "fallback")


class AccountTests(unittest.TestCase):
    def test_authorization_dict(self):
        auth = types.Authorization(
            hash=7,
            device_model="Mac",
            platform="macOS",
            system_version="14",
            api_id=9,
            app_name="telegram-native",
            app_version="0.1.0",
            date_created=datetime(2026, 1, 1, tzinfo=timezone.utc),
            date_active=None,
            ip="127.0.0.1",
            country="US",
            region="",
            current=True,
        )
        data = _authorization_to_dict(auth)
        self.assertEqual(data["hash"], 7)
        self.assertTrue(data["current"])
        self.assertEqual(data["device_model"], "Mac")

    def test_web_auth_dict(self):
        auth = types.WebAuthorization(
            hash=3,
            bot_id=0,
            domain="example.com",
            browser="Safari",
            platform="macOS",
            date_created=None,
            date_active=None,
            ip="127.0.0.1",
            region="",
        )
        data = _web_auth_to_dict(auth)
        self.assertEqual(data["hash"], 3)
        self.assertEqual(data["domain"], "example.com")


class ProxyTests(unittest.TestCase):
    def test_parse_proxy(self):
        self.assertEqual(
            parse_proxy("socks5://user:pass@127.0.0.1:7890"),
            ("socks5", "127.0.0.1", 7890, "user", "pass"),
        )
        self.assertIsNone(parse_proxy(None))
        with self.assertRaises(ValueError):
            parse_proxy("ftp://127.0.0.1:21")


class AccountRegistryTests(unittest.TestCase):
    def test_registry_roundtrip(self):
        import tempfile
        import telegram_native.config as cfg

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with patch.object(cfg, "DATA_DIR", root), patch.object(
                cfg, "ACCOUNTS_FILE", root / "accounts.json"
            ):
                path = account_session_path("work")
                save_account("work", path, {"account_id": 1, "username": "u"})
                data = load_accounts()
                self.assertEqual(data["work"]["account_id"], 1)
                self.assertEqual(data["work"]["session"], str(path))
                self.assertTrue(remove_account("work"))
                self.assertEqual(load_accounts(), {})


class ParserTests(unittest.TestCase):
    def test_new_commands_parse(self):
        cases = [
            (["group", "migrate", "demo"], "migrate_group"),
            (["invite", "list", "demo"], "list_invites"),
            (["messages", "download", "demo", "--id", "1"], "download"),
            (["messages", "forward", "--from", "a", "--to", "b", "--ids", "1"], "forward"),
            (["messages", "send-album", "demo", "--files", "a.png,b.png"], "send_album"),
            (["search", "global", "--query", "x"], "global_search"),
            (["contacts", "search", "--query", "x"], "search_contacts"),
            (["account", "profile"], "profile"),
            (["account", "devices", "list"], "devices_list"),
            (["account", "web-sessions", "list"], "web_sessions_list"),
            (["comments", "list", "demo", "--post-id", "1"], "list_comments"),
            (["comments", "reply", "demo", "--post-id", "1", "--text", "x"], "reply"),
            (["admin-log", "list", "demo"], "admin_log"),
            (["chat", "set-permissions", "demo", "--preset", "open"], "set_permissions"),
            (["topic", "read", "demo", "--topic-id", "1"], "read_topic"),
            (["topic", "hide", "demo", "--hide"], "hide_topic"),
            (["topic", "reorder", "demo", "--topic-ids", "1,2"], "reorder_topics"),
            (["forum", "set-tabs", "demo", "--enabled"], "set_tabs"),
            (["join-requests", "approve-all", "demo"], "approve_all"),
            (["config", "accounts", "list"], "cmd_accounts_list"),
        ]
        for argv, handler_name in cases:
            args = build_parser().parse_args(argv)
            self.assertEqual(args.handler.__name__, handler_name, argv)


if __name__ == "__main__":
    unittest.main()
