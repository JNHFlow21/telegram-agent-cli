from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from telethon.tl import types

from telegram_native.cli import build_parser, _command_name
from telegram_native.client import _parse_link, kind_detail
from telegram_native.config import DEFAULT_API_HASH, DEFAULT_API_ID, Settings
from telegram_native.envelope import build, error
from telegram_native.safety import read_receipt, write_receipt
from telegram_native.serialize import entity_to_dict, iso, peer_id


class EnvelopeTests(unittest.TestCase):
    def test_success_shape(self):
        env = build("chats list", True, "readonly", data={"items": []})
        self.assertEqual(env["schema"], "tg.v1")
        self.assertTrue(env["ok"])
        self.assertEqual(env["outcome"], "readonly")
        self.assertIsNone(env["error"])
        self.assertEqual(env["data"], {"items": []})

    def test_failure_shape(self):
        env = build("chat info", False, "noop", err=error("NOT_FOUND", "missing"))
        self.assertFalse(env["ok"])
        self.assertIsNone(env["data"])
        self.assertEqual(env["error"]["code"], "NOT_FOUND")

    def test_unknown_code_rejected(self):
        with self.assertRaises(ValueError):
            error("NOPE", "x")


class RefTests(unittest.TestCase):
    def test_links(self):
        self.assertEqual(_parse_link("@abc"), "@abc")
        self.assertEqual(_parse_link("t.me/abc"), "@abc")
        self.assertEqual(_parse_link("https://t.me/abc/"), "@abc")
        self.assertEqual(_parse_link("t.me/abc/123"), "@abc")
        self.assertEqual(_parse_link("https://t.me/abc/123"), "@abc")
        self.assertEqual(_parse_link("https://t.me/c/123456/7"), "-100123456")
        self.assertEqual(_parse_link("tg://resolve?domain=abc"), "@abc")

    def test_kind_and_peer_id(self):
        user = types.User(id=1)
        chat = types.Chat(id=2, title="g", photo=None, participants_count=1, date=None, version=1)
        channel = types.Channel(id=3, title="c", photo=None, date=None, broadcast=True)
        forum = types.Channel(id=4, title="f", photo=None, date=None, megagroup=True, forum=True)
        self.assertEqual(kind_detail(user), "private")
        self.assertEqual(kind_detail(chat), "group")
        self.assertEqual(kind_detail(channel), "channel")
        self.assertEqual(kind_detail(forum), "supergroup")
        self.assertEqual(peer_id(user), 1)
        self.assertEqual(peer_id(chat), -2)
        self.assertEqual(peer_id(channel), -1003)

    def test_entity_dict(self):
        forum = types.Channel(id=4, title="f", photo=None, date=None, megagroup=True, forum=True, username="fchat")
        data = entity_to_dict(forum)
        self.assertEqual(data["peer_id"], -1004)
        self.assertEqual(data["username"], "fchat")
        self.assertTrue(data["is_forum"])
        self.assertTrue(data["megagroup"])


class ParserTests(unittest.TestCase):
    def test_group_create_defaults_to_preview(self):
        args = build_parser().parse_args(["group", "create", "--title", "demo"])
        self.assertFalse(args.execute)
        self.assertFalse(args.forum)
        self.assertEqual(_command_name(args), "group create")

    def test_topic_requires_topic_id(self):
        with self.assertRaises(SystemExit):
            build_parser().parse_args(["topic", "close", "demo"])

    def test_bot_parser(self):
        args = build_parser().parse_args(["bot", "create", "--name", "Demo", "--username", "demo_bot"])
        self.assertEqual(args.name, "Demo")
        self.assertEqual(args.username, "demo_bot")
        self.assertFalse(args.execute)

    def test_read_command_has_no_execute(self):
        args = build_parser().parse_args(["chats", "list"])
        self.assertFalse(hasattr(args, "execute"))


class SerializeTests(unittest.TestCase):
    def test_iso(self):
        self.assertEqual(iso(datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc)), "2026-01-02T03:04:05Z")


class ConfigTests(unittest.TestCase):
    def test_defaults(self):
        self.assertEqual(DEFAULT_API_ID, 9)
        self.assertEqual(len(DEFAULT_API_HASH), 32)


class ReceiptTests(unittest.TestCase):
    def test_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            settings = Settings(
                api_id=9,
                api_hash="x" * 32,
                session_path=root / "s.session",
                data_dir=root,
                config_dir=root,
            )
            write_receipt(
                "req-1",
                action="messages.send",
                params={"chat": "demo"},
                status="executed",
                result={"message_id": 7},
                settings=settings,
            )
            record = read_receipt("req-1", settings)
            self.assertIsNotNone(record)
            self.assertEqual(record["status"], "executed")
            self.assertEqual(record["result"]["message_id"], 7)


if __name__ == "__main__":
    unittest.main()
