from __future__ import annotations

import argparse
import contextlib
import io
import tempfile
import unittest
from pathlib import Path

from telegram_native.commands.common import run_write
from telegram_native.config import Settings
from telegram_native.safety import read_receipt


class FakeCtx:
    def __init__(self, root: Path):
        self.settings = Settings(
            api_id=9,
            api_hash="x" * 32,
            session_path=root / "s.session",
            data_dir=root,
            config_dir=root,
        )

    def account_meta(self):
        return {"account_id": 1}


def _args(**kwargs):
    base = {"execute": False, "request_id": None, "yes": False, "confirm": None}
    base.update(kwargs)
    return argparse.Namespace(**base)


class WriteGateTests(unittest.TestCase):
    def test_preview_does_not_execute(self):
        with tempfile.TemporaryDirectory() as tmp:
            ctx = FakeCtx(Path(tmp))
            called = []

            async def execute():
                called.append(True)

            with contextlib.redirect_stdout(io.StringIO()):
                env = __import__("asyncio").run(
                    run_write(
                        ctx,
                        _args(),
                        command="demo",
                        action="demo.write",
                        params={"x": 1},
                        preview={"x": 1},
                        execute=execute,
                    )
                )
            self.assertTrue(env["ok"])
            self.assertEqual(env["outcome"], "preview")
            self.assertEqual(called, [])

    def test_execute_once_and_idempotent_replay(self):
        with tempfile.TemporaryDirectory() as tmp:
            ctx = FakeCtx(Path(tmp))
            called = []

            async def execute():
                called.append(True)
                return {"value": 7}

            args = _args(execute=True, request_id="req-test-1")
            with contextlib.redirect_stdout(io.StringIO()):
                first = __import__("asyncio").run(
                    run_write(
                        ctx,
                        args,
                        command="demo",
                        action="demo.write",
                        params={"x": 1},
                        preview={"x": 1},
                        execute=execute,
                    )
                )
                second = __import__("asyncio").run(
                    run_write(
                        ctx,
                        args,
                        command="demo",
                        action="demo.write",
                        params={"x": 1},
                        preview={"x": 1},
                        execute=execute,
                    )
                )
            self.assertEqual(first["outcome"], "executed")
            self.assertEqual(second["outcome"], "noop")
            self.assertEqual(len(called), 1)
            self.assertIsNotNone(read_receipt("req-test-1", ctx.settings))

    def test_unknown_is_locked(self):
        with tempfile.TemporaryDirectory() as tmp:
            ctx = FakeCtx(Path(tmp))

            async def execute():
                raise TimeoutError("connection lost")

            args = _args(execute=True, request_id="req-test-unknown")
            with contextlib.redirect_stdout(io.StringIO()):
                first = __import__("asyncio").run(
                    run_write(
                        ctx,
                        args,
                        command="demo",
                        action="demo.write",
                        params={"x": 1},
                        preview={"x": 1},
                        execute=execute,
                    )
                )
                second = __import__("asyncio").run(
                    run_write(
                        ctx,
                        args,
                        command="demo",
                        action="demo.write",
                        params={"x": 1},
                        preview={"x": 1},
                        execute=execute,
                    )
                )
            self.assertFalse(first["ok"])
            self.assertEqual(first["error"]["code"], "UNAVAILABLE")
            self.assertEqual(first["outcome"], "unknown")
            self.assertEqual(second["error"]["code"], "OUTCOME_UNKNOWN")
            self.assertEqual(read_receipt("req-test-unknown", ctx.settings)["status"], "unknown")

    def test_destructive_requires_confirmation(self):
        with tempfile.TemporaryDirectory() as tmp:
            ctx = FakeCtx(Path(tmp))

            async def execute():
                return {}

            with contextlib.redirect_stdout(io.StringIO()):
                env = __import__("asyncio").run(
                    run_write(
                        ctx,
                        _args(execute=True),
                        command="demo",
                        action="demo.delete",
                        params={},
                        preview={},
                        execute=execute,
                        destructive=True,
                        confirm_label="target",
                    )
                )
            self.assertFalse(env["ok"])
            self.assertEqual(env["error"]["code"], "BLOCKED")

    def test_request_id_reuse_with_different_params_is_blocked(self):
        with tempfile.TemporaryDirectory() as tmp:
            ctx = FakeCtx(Path(tmp))
            calls = []

            async def execute():
                calls.append(True)
                return {"value": 1}

            args = _args(execute=True, request_id="req-mismatch")
            with contextlib.redirect_stdout(io.StringIO()):
                first = __import__("asyncio").run(
                    run_write(
                        ctx, args, command="demo", action="demo.write",
                        params={"x": 1}, preview={"x": 1}, execute=execute,
                    )
                )
                second = __import__("asyncio").run(
                    run_write(
                        ctx, args, command="demo", action="demo.write",
                        params={"x": 2}, preview={"x": 2}, execute=execute,
                    )
                )
            self.assertTrue(first["ok"])
            self.assertFalse(second["ok"])
            self.assertEqual(second["error"]["code"], "BLOCKED")
            self.assertEqual(len(calls), 1)


if __name__ == "__main__":
    unittest.main()
