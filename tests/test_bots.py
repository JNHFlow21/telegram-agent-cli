"""Offline tests for the BotFather command module.

Everything here uses a fake Telegram client.  No network, no real Telegram
session and no message to @BotFather is ever sent.
"""
from __future__ import annotations

import argparse
import asyncio
import contextlib
import io
import json
import stat
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from telethon.errors import FloodWaitError

from telegram_native.commands import bots
from telegram_native.config import Settings
from telegram_native.errors import CliError
from telegram_native.safety import read_receipt

TOKEN = "123456789:" + "A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7"
NEW_TOKEN = "987654321:" + "Z9y8X7w6V5u4T3s2R1q0P9o8N7m6L5k4J3"


# ---------------------------------------------------------------------------
# Fake Telethon surface
# ---------------------------------------------------------------------------


class FakeButton:
    def __init__(self, label: str, on_click=None):
        self.text = label
        self._on_click = on_click
        self.clicks = 0

    async def click(self):
        self.clicks += 1
        if self._on_click is not None:
            result = self._on_click()
            if asyncio.iscoroutine(result):
                await result
        return None


class FakeMessage:
    def __init__(self, message_id: int, text: str = "", *, out: bool = False, buttons=None):
        self.id = message_id
        self.message = text
        self.text = text
        self.out = out
        self.buttons = buttons
        self.edit_date = None
        self.date = datetime.now(timezone.utc)

    def edit(self, text: str, *, buttons=None) -> "FakeMessage":
        self.message = text
        self.text = text
        if buttons is not None:
            self.buttons = buttons
        self.edit_date = datetime.now(timezone.utc)
        return self


class FakeBotFather:
    """A tiny scripted BotFather: reply rules run when a message is sent."""

    def __init__(self):
        self.messages: list[FakeMessage] = []
        self.sent: list[str] = []
        self.rules: list[tuple[object, object]] = []
        self.get_calls: list[dict] = []
        self.send_error: Exception | None = None
        self._next_id = 1000

    def _id(self) -> int:
        self._next_id += 1
        return self._next_id

    def add(self, text: str = "", *, buttons=None, out: bool = False) -> FakeMessage:
        message = FakeMessage(self._id(), text, out=out, buttons=buttons)
        self.messages.append(message)
        return message

    def on(self, expected, callback) -> "FakeBotFather":
        self.rules.append((expected, callback))
        return self

    async def send_message(self, entity, text):
        assert entity == "BotFather"
        self.sent.append(text)
        if self.send_error is not None:
            raise self.send_error
        message = self.add(text, out=True)
        for expected, callback in list(self.rules):
            if callable(expected):
                matched = bool(expected(text))
            else:
                matched = str(expected).casefold() == text.casefold()
            if matched:
                callback(self)
        return message

    async def get_messages(self, entity, *, min_id=0, limit=50, ids=None):
        assert entity == "BotFather"
        self.get_calls.append({"min_id": min_id, "limit": limit, "ids": ids})
        if ids is not None:
            wanted = set(ids) if isinstance(ids, (list, tuple, set)) else {ids}
            return [message for message in self.messages if message.id in wanted]
        messages = [message for message in self.messages if message.id > min_id]
        return messages[-limit:]


class FakeCtx:
    def __init__(self, root: Path, client: FakeBotFather | None = None):
        self.settings = Settings(
            api_id=9,
            api_hash="x" * 32,
            session_path=root / "s.session",
            data_dir=root,
            config_dir=root,
        )
        self.client = client or FakeBotFather()
        self.me = SimpleNamespace(id=1, username="tester", phone="+15551234567")
        self.connected = False
        self.connect_count = 0

    async def connect(self):
        self.connected = True
        self.connect_count += 1
        return self

    async def close(self):
        self.connected = False

    async def authorized(self):
        return True

    async def require_authorized(self):
        if not self.connected:
            raise AssertionError("require_authorized() called before connect()")
        return self.me

    def account_meta(self):
        if not self.connected:
            return {}
        return {
            "account_id": 1,
            "account_username": "tester",
            "phone_masked": "+1*******67",
            "dc_id": 2,
        }


def _args(**kwargs):
    base = {"execute": False, "request_id": None, "yes": False, "confirm": None}
    base.update(kwargs)
    return argparse.Namespace(**base)


def _run(coro):
    return asyncio.run(coro)


def _call(coro):
    """Run a command, capture its single stdout envelope, and parse it."""
    output = io.StringIO()
    with contextlib.redirect_stdout(output):
        envelope = _run(coro)
    lines = [line for line in output.getvalue().splitlines() if line.strip()]
    assert len(lines) == 1, f"expected one JSON line, got {lines!r}"
    parsed = json.loads(lines[0])
    assert parsed["schema"] == "tg.v1"
    return envelope, parsed


def _add_bot_button(bf: FakeBotFather, label: str = "alpha_bot", *, command_text: str, prompt: str, on_click):
    def setup(_client):
        _client.add(prompt, buttons=[[FakeButton(label, on_click)]])

    bf.on(command_text, setup)


def _create_script(bf: FakeBotFather, *, username: str = "my_test_bot", token: str = TOKEN):
    bf.on(
        "/newbot",
        lambda b: b.add(
            "Alright, a new bot. How are we going to call it? Please choose a name for your bot."
        ),
    )
    bf.on(
        "My Bot",
        lambda b: b.add(
            "Good. Now let's choose a username for your bot. It must end in `bot`. "
            "Like this, for example: TetrisBot or tetris_bot."
        ),
    )
    bf.on(
        username,
        lambda b: b.add(
            f"Done! Congratulations on your new bot. You will find it at t.me/{username}.\n\n"
            f"Use this token to access the HTTP API:\n{token}\n\nKeep your token secure."
        ),
    )


def _token_script(
    bf: FakeBotFather,
    *,
    username: str = "alpha_bot",
    token: str = TOKEN,
    label: str | None = None,
    reply_text: str | None = None,
):
    def reply():
        bf.add(
            reply_text
            or f"Here is the token for bot @{username}:\n{token}\n\nKeep it secure."
        )

    _add_bot_button(
        bf,
        label or username,
        command_text="/token",
        prompt="Choose a bot to get the token for.",
        on_click=reply,
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class TokenHelperTests(unittest.TestCase):
    def test_extract_and_mask(self):
        self.assertEqual(bots._extract_token(f"token:\n{TOKEN}\nend"), TOKEN)
        self.assertIsNone(bots._extract_token("no token here"))
        masked = bots._mask_token(TOKEN)
        self.assertNotEqual(masked, TOKEN)
        self.assertNotIn(TOKEN, masked)
        self.assertTrue(masked.startswith("123456789:"))
        self.assertIn("…", masked)
        self.assertEqual(bots._bot_id_from_token(TOKEN), 123456789)

    def test_short_or_invalid_token_is_not_accepted(self):
        self.assertIsNone(bots._extract_token("123456789:short"))
        self.assertEqual(bots._mask_token("not-a-token"), "***")

    def test_safe_text_scrubs_short_token_like_strings(self):
        text = bots._safe_text("reply 123456789:short-secret-12345 end")
        self.assertNotIn("short-secret-12345", text)


class CommandFileTests(unittest.TestCase):
    def test_parse_and_format(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "commands.txt"
            path.write_text(
                "# comment\n\nstart - Start the bot\nhelp - Show help\n",
                encoding="utf-8",
            )
            items, body = bots._parse_commands_file(str(path))
            self.assertEqual(
                items,
                [("start", "Start the bot"), ("help", "Show help")],
            )
            self.assertEqual(body, "start - Start the bot\nhelp - Show help")

    def test_rejects_bad_line(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "commands.txt"
            path.write_text("start: no separator\n", encoding="utf-8")
            with self.assertRaises(CliError) as raised:
                bots._parse_commands_file(str(path))
            self.assertEqual(raised.exception.code, "VALIDATION")


class CreateBotTests(unittest.TestCase):
    def test_preview_is_offline_and_takes_no_write(self):
        with tempfile.TemporaryDirectory() as tmp:
            ctx = FakeCtx(Path(tmp))
            args = _args(name="My Bot", username="my_test_bot")
            envelope, parsed = _call(bots.create_bot(ctx, args))
            self.assertEqual(parsed["outcome"], "preview")
            self.assertEqual(envelope["data"]["params"]["username"], "my_test_bot")
            self.assertIn("token_file", envelope["data"]["preview"])
            self.assertEqual(ctx.client.sent, [])
            self.assertFalse(ctx.connected)

    def test_create_success_stores_token_with_0600(self):
        with tempfile.TemporaryDirectory() as tmp:
            ctx = FakeCtx(Path(tmp))
            _create_script(ctx.client)
            args = _args(name="My Bot", username="my_test_bot", execute=True, request_id="rid-create-1")
            envelope, parsed = _call(bots.create_bot(ctx, args))

            self.assertTrue(parsed["ok"])
            self.assertEqual(parsed["outcome"], "executed")
            result = envelope["data"]["result"]
            self.assertEqual(result["bot"]["id"], 123456789)
            self.assertEqual(result["bot"]["username"], "my_test_bot")
            self.assertEqual(result["token_masked"], bots._mask_token(TOKEN))
            self.assertNotIn(TOKEN, json.dumps(parsed, ensure_ascii=False))
            self.assertEqual(ctx.client.sent, ["/newbot", "My Bot", "my_test_bot"])

            path = Path(result["token_file"])
            self.assertTrue(path.is_file())
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
            record = json.loads(path.read_text("utf-8"))
            self.assertEqual(record["token"], TOKEN)
            self.assertEqual(record["bot_id"], 123456789)
            self.assertEqual(record["source"], "create")

            receipt = read_receipt("rid-create-1", ctx.settings)
            self.assertNotIn(TOKEN, json.dumps(receipt, ensure_ascii=False))

    def test_username_taken_is_rejected_without_token_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            ctx = FakeCtx(Path(tmp))
            _create_script(ctx.client)
            ctx.client.on(
                "my_test_bot",
                lambda b: b.add("Sorry, this username is already taken. Please try something different."),
            )
            args = _args(name="My Bot", username="my_test_bot", execute=True, request_id="rid-taken")
            envelope, parsed = _call(bots.create_bot(ctx, args))

            self.assertFalse(parsed["ok"])
            self.assertEqual(parsed["error"]["code"], "REJECTED")
            self.assertIn("botfather_message", parsed["error"]["details"])
            self.assertEqual(ctx.client.sent, ["/newbot", "My Bot", "my_test_bot"])
            self.assertFalse((ctx.settings.bots_dir / "my_test_bot.json").exists())

    def test_timeout_is_unknown_and_retry_does_not_resend(self):
        with tempfile.TemporaryDirectory() as tmp:
            ctx = FakeCtx(Path(tmp))
            _create_script(ctx.client)
            # Remove the success rule for the username: BotFather goes silent.
            ctx.client.rules = [rule for rule in ctx.client.rules if rule[0] != "my_test_bot"]

            args = _args(name="My Bot", username="my_test_bot", execute=True, request_id="rid-timeout")
            with mock.patch.object(bots, "BOTFATHER_TIMEOUT", 0.05), mock.patch.object(
                bots, "BOTFATHER_POLL_INTERVAL", 0.001
            ):
                first, first_parsed = _call(bots.create_bot(ctx, args))
                self.assertFalse(first_parsed["ok"])
                self.assertEqual(first_parsed["error"]["code"], "OUTCOME_UNKNOWN")
                self.assertEqual(first_parsed["outcome"], "unknown")
                sent_after_first = list(ctx.client.sent)
                self.assertEqual(len(sent_after_first), 3)

                second, second_parsed = _call(bots.create_bot(ctx, args))
                self.assertEqual(second_parsed["error"]["code"], "OUTCOME_UNKNOWN")
                self.assertEqual(ctx.client.sent, sent_after_first)

            receipt = read_receipt("rid-timeout", ctx.settings)
            self.assertEqual(receipt["status"], "unknown")

    def test_initial_timeout_is_retriable_unavailable(self):
        with tempfile.TemporaryDirectory() as tmp:
            ctx = FakeCtx(Path(tmp))
            # /newbot gets no reply at all.
            args = _args(name="My Bot", username="my_test_bot", execute=True, request_id="rid-initial-timeout")
            with mock.patch.object(bots, "BOTFATHER_TIMEOUT", 0.05), mock.patch.object(
                bots, "BOTFATHER_POLL_INTERVAL", 0.001
            ):
                _envelope, parsed = _call(bots.create_bot(ctx, args))
            self.assertEqual(parsed["error"]["code"], "UNAVAILABLE")
            self.assertEqual(ctx.client.sent, ["/newbot"])

    def test_invalid_username_is_validation_error_before_any_send(self):
        with tempfile.TemporaryDirectory() as tmp:
            ctx = FakeCtx(Path(tmp))
            args = _args(name="My Bot", username="not-a-bot", execute=True)
            with self.assertRaises(CliError) as raised:
                _run(bots.create_bot(ctx, args))
            self.assertEqual(raised.exception.code, "VALIDATION")
            self.assertEqual(ctx.client.sent, [])


class ListBotsTests(unittest.TestCase):
    def test_parses_buttons_case_insensitively(self):
        with tempfile.TemporaryDirectory() as tmp:
            ctx = FakeCtx(Path(tmp))
            ctx.client.on(
                "/mybots",
                lambda b: b.add(
                    "Choose a bot from the list below:",
                    buttons=[[FakeButton("Alpha_Bot"), FakeButton("beta_bot")]],
                ),
            )
            envelope, parsed = _call(bots.list_bots(ctx, argparse.Namespace()))
            self.assertTrue(parsed["ok"])
            self.assertEqual(parsed["outcome"], "readonly")
            data = envelope["data"]
            self.assertEqual([item["username"] for item in data["bots"]], ["Alpha_Bot", "beta_bot"])
            self.assertEqual(data["usernames"], ["Alpha_Bot", "beta_bot"])
            self.assertEqual(data["available_buttons"], ["Alpha_Bot", "beta_bot"])
            self.assertEqual(ctx.client.sent, ["/mybots"])

    def test_no_bots_returns_empty_list(self):
        with tempfile.TemporaryDirectory() as tmp:
            ctx = FakeCtx(Path(tmp))
            ctx.client.on("/mybots", lambda b: b.add("You have no bots."))
            envelope, _parsed = _call(bots.list_bots(ctx, argparse.Namespace()))
            self.assertEqual(envelope["data"]["count"], 0)
            self.assertEqual(envelope["data"]["bots"], [])

    def test_buttons_without_username_are_reported(self):
        with tempfile.TemporaryDirectory() as tmp:
            ctx = FakeCtx(Path(tmp))
            ctx.client.on(
                "/mybots",
                lambda b: b.add("Choose a bot:", buttons=[[FakeButton("Some Display Name")]]),
            )
            with self.assertRaises(CliError) as raised:
                _run(bots.list_bots(ctx, argparse.Namespace()))
            self.assertEqual(raised.exception.code, "NOT_FOUND")
            self.assertEqual(raised.exception.details["available_buttons"], ["Some Display Name"])


class GetTokenTests(unittest.TestCase):
    def test_preview_is_offline(self):
        with tempfile.TemporaryDirectory() as tmp:
            ctx = FakeCtx(Path(tmp))
            args = _args(username="alpha_bot", show_secret=False)
            envelope, parsed = _call(bots.get_token(ctx, args))
            self.assertEqual(parsed["outcome"], "preview")
            self.assertEqual(envelope["data"]["params"]["token_file"], str(ctx.settings.bots_dir / "alpha_bot.json"))
            self.assertEqual(ctx.client.sent, [])

    def test_stores_and_masks_then_show_secret_is_not_in_receipt(self):
        with tempfile.TemporaryDirectory() as tmp:
            ctx = FakeCtx(Path(tmp))
            _token_script(ctx.client)

            args = _args(username="alpha_bot", show_secret=False, execute=True, request_id="rid-token-1")
            envelope, parsed = _call(bots.get_token(ctx, args))
            self.assertTrue(parsed["ok"])
            self.assertEqual(parsed["outcome"], "executed")
            result = envelope["data"]["result"]
            self.assertEqual(result["token_masked"], bots._mask_token(TOKEN))
            self.assertEqual(result["token"], bots._mask_token(TOKEN))
            self.assertIsNone(result["token_rotated"])
            self.assertIn("rotation_note", result)
            self.assertNotIn(TOKEN, json.dumps(parsed, ensure_ascii=False))
            path = Path(result["token_file"])
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
            self.assertEqual(json.loads(path.read_text("utf-8"))["token"], TOKEN)

            secret_args = _args(
                username="alpha_bot",
                show_secret=True,
                execute=True,
                request_id="rid-token-secret",
            )
            secret_envelope, _secret_parsed = _call(bots.get_token(ctx, secret_args))
            self.assertEqual(secret_envelope["data"]["result"]["token"], TOKEN)
            receipt = read_receipt("rid-token-secret", ctx.settings)
            self.assertNotIn(TOKEN, json.dumps(receipt, ensure_ascii=False))

    def test_token_rotation_is_reported_when_botfather_says_so(self):
        with tempfile.TemporaryDirectory() as tmp:
            ctx = FakeCtx(Path(tmp))
            _token_script(
                ctx.client,
                token=NEW_TOKEN,
                reply_text=(
                    "Success! The token has been revoked. "
                    f"Here is a new token:\n{NEW_TOKEN}"
                ),
            )
            args = _args(username="alpha_bot", execute=True, request_id="rid-token-rotated")
            envelope, parsed = _call(bots.get_token(ctx, args))
            self.assertTrue(parsed["ok"])
            result = envelope["data"]["result"]
            self.assertTrue(result["token_rotated"])
            self.assertNotIn("rotation_note", result)
            self.assertEqual(json.loads(Path(result["token_file"]).read_text("utf-8"))["token"], NEW_TOKEN)

    def test_token_timeout_after_selection_is_unknown(self):
        with tempfile.TemporaryDirectory() as tmp:
            ctx = FakeCtx(Path(tmp))
            _add_bot_button(
                ctx.client,
                command_text="/token",
                prompt="Choose a bot to get the token for.",
                on_click=lambda: None,
            )
            args = _args(username="alpha_bot", execute=True, request_id="rid-token-timeout")
            with mock.patch.object(bots, "BOTFATHER_TIMEOUT", 0.05), mock.patch.object(
                bots, "BOTFATHER_POLL_INTERVAL", 0.001
            ):
                _envelope, parsed = _call(bots.get_token(ctx, args))
            self.assertEqual(parsed["error"]["code"], "OUTCOME_UNKNOWN")
            self.assertEqual(parsed["outcome"], "unknown")
            self.assertEqual(ctx.client.sent, ["/token"])

    def test_unknown_bot_lists_available_buttons(self):
        with tempfile.TemporaryDirectory() as tmp:
            ctx = FakeCtx(Path(tmp))
            _token_script(ctx.client, username="alpha_bot")
            args = _args(username="missing_bot", execute=True, request_id="rid-token-missing")
            _envelope, parsed = _call(bots.get_token(ctx, args))
            self.assertFalse(parsed["ok"])
            self.assertEqual(parsed["error"]["code"], "NOT_FOUND")
            self.assertEqual(parsed["error"]["details"]["available_buttons"], ["alpha_bot"])
            self.assertEqual(ctx.client.sent, ["/token"])


class SetterTests(unittest.TestCase):
    def test_set_name_handles_edited_prompt_message(self):
        with tempfile.TemporaryDirectory() as tmp:
            ctx = FakeCtx(Path(tmp))
            holder: dict[str, FakeMessage] = {}

            def setup(b):
                button = FakeButton("alpha_bot", lambda: holder["msg"].edit("OK. Send me the new name."))
                holder["msg"] = b.add(
                    "Choose a bot to change the name of.",
                    buttons=[[button]],
                )

            ctx.client.on("/setname", setup)
            ctx.client.on(
                "New Name",
                lambda b: b.add("Success! Name of the bot has been changed."),
            )

            args = _args(username="alpha_bot", name="New Name", execute=True, request_id="rid-name")
            envelope, parsed = _call(bots.set_name(ctx, args))
            self.assertTrue(parsed["ok"])
            self.assertEqual(envelope["data"]["result"]["status"], "updated")
            self.assertEqual(ctx.client.sent, ["/setname", "New Name"])
            self.assertIsNotNone(holder["msg"].edit_date)

    def test_set_about_and_description(self):
        with tempfile.TemporaryDirectory() as tmp:
            ctx = FakeCtx(Path(tmp))
            _add_bot_button(
                ctx.client,
                command_text="/setabouttext",
                prompt="Choose a bot to change the 'about' text.",
                on_click=lambda: ctx.client.add(
                    "OK. Send me the new 'about' text. People will see this text on the bot's profile page."
                ),
            )
            ctx.client.on(
                "About text",
                lambda b: b.add("Success! The about text has been updated."),
            )
            args = _args(username="alpha_bot", about="About text", execute=True, request_id="rid-about")
            _envelope, parsed = _call(bots.set_about(ctx, args))
            self.assertTrue(parsed["ok"])
            self.assertEqual(ctx.client.sent[0], "/setabouttext")

            ctx.client.sent.clear()
            ctx.client.messages.clear()
            _add_bot_button(
                ctx.client,
                command_text="/setdescription",
                prompt="Choose a bot to change the description.",
                on_click=lambda: ctx.client.add(
                    "OK. Send me the new description for your bot. "
                    "People will see this description before they start your bot."
                ),
            )
            ctx.client.on(
                "Description text",
                lambda b: b.add("Success! The description has been updated."),
            )
            args = _args(
                username="alpha_bot",
                description="Description text",
                execute=True,
                request_id="rid-desc",
            )
            _envelope, parsed = _call(bots.set_description(ctx, args))
            self.assertTrue(parsed["ok"])

    def test_set_commands_formats_lines(self):
        with tempfile.TemporaryDirectory() as tmp:
            ctx = FakeCtx(Path(tmp))
            _add_bot_button(
                ctx.client,
                command_text="/setcommands",
                prompt="Choose a bot to change the list of commands.",
                on_click=lambda: ctx.client.add(
                    "OK. Send me a list of commands in this format: command1 - Description"
                ),
            )
            expected_body = "start - Start the bot\nhelp - Show help"
            ctx.client.on(
                expected_body,
                lambda b: b.add("Success! Commands have been updated."),
            )
            path = Path(tmp) / "commands.txt"
            path.write_text(
                "# ignored\nstart - Start the bot\n\nhelp - Show help\n",
                encoding="utf-8",
            )
            args = _args(
                username="alpha_bot",
                commands_file=str(path),
                execute=True,
                request_id="rid-commands",
            )
            envelope, parsed = _call(bots.set_commands(ctx, args))
            self.assertTrue(parsed["ok"])
            self.assertEqual(envelope["data"]["result"]["count"], 2)
            self.assertEqual(ctx.client.sent[-1], expected_body)

    def test_cancel_reply_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            ctx = FakeCtx(Path(tmp))
            _add_bot_button(
                ctx.client,
                command_text="/setname",
                prompt="Choose a bot to change the name of.",
                on_click=lambda: ctx.client.add("Cancelled."),
            )
            args = _args(username="alpha_bot", name="New Name", execute=True, request_id="rid-cancel")
            _envelope, parsed = _call(bots.set_name(ctx, args))
            self.assertEqual(parsed["error"]["code"], "REJECTED")


class DestructiveTests(unittest.TestCase):
    def test_delete_requires_confirmation_and_then_deletes(self):
        with tempfile.TemporaryDirectory() as tmp:
            ctx = FakeCtx(Path(tmp))
            stored = bots._store_bot_token(ctx.settings, "alpha_bot", TOKEN, source="seed")
            assert stored.is_file()

            def setup(b):
                b.add(
                    "Choose a bot to delete.",
                    buttons=[[FakeButton("alpha_bot", lambda: b.add(
                        "Are you sure you want to delete @alpha_bot? This action cannot be undone.",
                        buttons=[[
                            FakeButton("Yes, delete bot", lambda: b.add("Bot deleted.")),
                            FakeButton("Cancel"),
                        ]],
                    ))]],
                )

            ctx.client.on("/deletebot", setup)

            blocked_args = _args(username="alpha_bot", execute=True, request_id="rid-delete-blocked")
            _envelope, blocked = _call(bots.delete_bot(ctx, blocked_args))
            self.assertEqual(blocked["error"]["code"], "BLOCKED")
            self.assertEqual(ctx.client.sent, [])

            args = _args(
                username="alpha_bot",
                execute=True,
                request_id="rid-delete",
                yes=True,
            )
            envelope, parsed = _call(bots.delete_bot(ctx, args))
            self.assertTrue(parsed["ok"])
            self.assertTrue(envelope["data"]["result"]["deleted"])
            self.assertTrue(envelope["data"]["result"]["token_file_removed"])
            self.assertFalse(stored.exists())
            self.assertEqual(ctx.client.sent, ["/deletebot"])
            labels = [
                button.text
                for message in ctx.client.messages
                for row in (message.buttons or [])
                for button in row
            ]
            self.assertIn("Yes, delete bot", labels)

    def test_delete_text_confirmation_sentence(self):
        with tempfile.TemporaryDirectory() as tmp:
            ctx = FakeCtx(Path(tmp))
            stored = bots._store_bot_token(ctx.settings, "alpha_bot", TOKEN, source="seed")

            def setup(b):
                b.add(
                    "Choose a bot to delete.",
                    buttons=[[FakeButton("alpha_bot", lambda: b.add(
                        "OK, you selected @alpha_bot. Are you sure?\n\n"
                        "Send 'Yes, I am totally sure.' to confirm you really want to delete this bot."
                    ))]],
                )

            ctx.client.on("/deletebot", setup)
            ctx.client.on("Yes, I am totally sure.", lambda b: b.add("Bot deleted."))
            args = _args(username="alpha_bot", execute=True, request_id="rid-delete-text", yes=True)
            envelope, parsed = _call(bots.delete_bot(ctx, args))
            self.assertTrue(parsed["ok"])
            self.assertTrue(envelope["data"]["result"]["deleted"])
            self.assertFalse(stored.exists())
            self.assertIn("Yes, I am totally sure.", ctx.client.sent)

    def test_revoke_stores_new_token_and_masks_result(self):
        with tempfile.TemporaryDirectory() as tmp:
            ctx = FakeCtx(Path(tmp))
            bots._store_bot_token(ctx.settings, "alpha_bot", TOKEN, source="seed")

            def setup(b):
                b.add(
                    "Choose a bot to revoke its token.",
                    buttons=[[FakeButton("alpha_bot", lambda: b.add(
                        "Are you sure you want to revoke the token for @alpha_bot?",
                        buttons=[[
                            FakeButton(
                                "Yes, revoke token",
                                lambda: b.add(f"Success! Here is your new token:\n{NEW_TOKEN}"),
                            ),
                            FakeButton("Cancel"),
                        ]],
                    ))]],
                )

            ctx.client.on("/revoke", setup)
            args = _args(
                username="alpha_bot",
                execute=True,
                request_id="rid-revoke",
                yes=True,
            )
            envelope, parsed = _call(bots.revoke_token(ctx, args))
            self.assertTrue(parsed["ok"])
            result = envelope["data"]["result"]
            self.assertTrue(result["revoked"])
            self.assertTrue(result["token_rotated"])
            self.assertTrue(result["confirmation_required"])
            self.assertEqual(result["token_masked"], bots._mask_token(NEW_TOKEN))
            self.assertNotIn(NEW_TOKEN, json.dumps(parsed, ensure_ascii=False))
            record = json.loads(Path(result["token_file"]).read_text("utf-8"))
            self.assertEqual(record["token"], NEW_TOKEN)
            self.assertEqual(record["source"], "revoke")


    def test_revoke_accepts_direct_token_without_confirmation(self):
        with tempfile.TemporaryDirectory() as tmp:
            ctx = FakeCtx(Path(tmp))
            bots._store_bot_token(ctx.settings, "alpha_bot", TOKEN, source="seed")

            def setup(b):
                b.add(
                    "Choose a bot to revoke its token.",
                    buttons=[[FakeButton(
                        "alpha_bot",
                        lambda: b.add(
                            "Success! The token has been revoked. "
                            f"Here is a new token:\n{NEW_TOKEN}"
                        ),
                    )]],
                )

            ctx.client.on("/revoke", setup)
            args = _args(
                username="alpha_bot",
                execute=True,
                request_id="rid-revoke-direct",
                yes=True,
            )
            envelope, parsed = _call(bots.revoke_token(ctx, args))
            self.assertTrue(parsed["ok"])
            result = envelope["data"]["result"]
            self.assertTrue(result["token_rotated"])
            self.assertFalse(result["confirmation_required"])
            self.assertEqual(ctx.client.sent, ["/revoke"])
            self.assertEqual(json.loads(Path(result["token_file"]).read_text("utf-8"))["token"], NEW_TOKEN)

    def test_revoke_timeout_after_selection_is_unknown(self):
        with tempfile.TemporaryDirectory() as tmp:
            ctx = FakeCtx(Path(tmp))
            _add_bot_button(
                ctx.client,
                command_text="/revoke",
                prompt="Choose a bot to revoke its token.",
                on_click=lambda: None,
            )
            args = _args(
                username="alpha_bot",
                execute=True,
                request_id="rid-revoke-timeout",
                yes=True,
            )
            with mock.patch.object(bots, "BOTFATHER_TIMEOUT", 0.05), mock.patch.object(
                bots, "BOTFATHER_POLL_INTERVAL", 0.001
            ):
                _envelope, parsed = _call(bots.revoke_token(ctx, args))
            self.assertEqual(parsed["error"]["code"], "OUTCOME_UNKNOWN")
            self.assertEqual(parsed["outcome"], "unknown")
            self.assertEqual(ctx.client.sent, ["/revoke"])


class ErrorMappingTests(unittest.TestCase):
    def test_flood_wait_maps_to_throttled(self):
        with tempfile.TemporaryDirectory() as tmp:
            ctx = FakeCtx(Path(tmp))
            ctx.client.send_error = FloodWaitError(request=None, capture=7)
            args = _args(name="My Bot", username="my_test_bot", execute=True, request_id="rid-flood")
            _envelope, parsed = _call(bots.create_bot(ctx, args))
            self.assertEqual(parsed["error"]["code"], "THROTTLED")
            self.assertTrue(parsed["error"]["retriable"])


if __name__ == "__main__":
    unittest.main()
