"""Session authorization commands."""
from __future__ import annotations

import asyncio
import getpass
import os
import sys
import time
from pathlib import Path

import qrcode
from telethon.errors import SessionPasswordNeededError

from ..client import TG
from ..config import mask_phone
from ..envelope import emit_failure, emit_preview, emit_read, emit_write
from ..errors import CliError


def _register_account(ctx: TG, me) -> None:
    if not ctx.account_name:
        return
    from ..config import save_account

    save_account(
        ctx.account_name,
        ctx.settings.session_path,
        {
            "account_id": me.id,
            "username": me.username,
            "first_name": me.first_name,
            "last_name": me.last_name,
            "phone_masked": mask_phone(me.phone),
        },
    )


def _prompt_phone(args) -> str:
    phone = getattr(args, "phone", None) or os.environ.get("TG_PHONE")
    if phone:
        return phone.strip()
    return input("Telegram phone (international format, e.g. +8613800000000): ").strip()


def _prompt_code() -> str:
    return input("Login code: ").strip()


def _prompt_password() -> str:
    return getpass.getpass("Two-step verification password: ")


async def status(ctx: TG, args) -> dict:
    started = time.monotonic()
    await ctx.connect()
    try:
        if not await ctx.authorized():
            return emit_read(
                "auth status",
                {
                    "authorized": False,
                    "account": ctx.account_name,
                    "next_action": "telegram-native auth qr   # or: telegram-native auth login",
                    "session": str(ctx.settings.session_path),
                },
                started,
            )
        me = await ctx.require_authorized()
        return emit_read(
            "auth status",
            {
                "authorized": True,
                "account": ctx.account_name,
                "account_id": me.id,
                "username": me.username,
                "first_name": me.first_name,
                "last_name": me.last_name,
                "phone_masked": mask_phone(me.phone),
                "dc_id": getattr(ctx.client.session, "dc_id", None),
                "session": str(ctx.settings.session_path),
            },
            started,
        )
    finally:
        await ctx.close()


async def login(ctx: TG, args) -> dict:
    started = time.monotonic()
    await ctx.connect()
    try:
        if await ctx.authorized():
            me = await ctx.require_authorized()
            return emit_write(
                "auth login",
                {
                    "status": "already_authorized",
                    "account_id": me.id,
                    "username": me.username,
                },
                started,
            )
        phone = _prompt_phone(args)
        sent = await ctx.client.send_code_request(phone)
        print(
            "Telegram sent a login code. If the code arrives in the existing app, "
            "enter it here; the code itself is never printed or stored.",
            file=sys.stderr,
            flush=True,
        )
        code = _prompt_code()
        try:
            await ctx.client.sign_in(phone=phone, code=code, phone_code_hash=sent.phone_code_hash)
        except SessionPasswordNeededError:
            password = _prompt_password()
            await ctx.client.sign_in(password=password)
        me = await ctx.client.get_me()
        if me is None:
            raise CliError("REJECTED", "登录未完成，Telegram 未返回账号信息。")
        ctx.me = me
        try:
            ctx.settings.session_path.chmod(0o600)
        except OSError:
            pass
        _register_account(ctx, me)
        return emit_write(
            "auth login",
            {
                "status": "authorized",
                "account_id": me.id,
                "username": me.username,
                "phone_masked": mask_phone(me.phone),
            },
            started,
        )
    finally:
        await ctx.close()


def _print_qr(url: str, png_path: Path) -> None:
    qr = qrcode.QRCode(border=1)
    qr.add_data(url)
    qr.make(fit=True)
    qr.print_ascii(invert=True, out=sys.stderr)
    png_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    img = qr.make_image(fill_color="black", back_color="white")
    img.save(png_path)
    try:
        os.chmod(png_path, 0o600)
    except OSError:
        pass
    print(f"QR image: {png_path}", file=sys.stderr, flush=True)


async def qr_login(ctx: TG, args) -> dict:
    started = time.monotonic()
    await ctx.connect()
    try:
        if await ctx.authorized():
            me = await ctx.require_authorized()
            return emit_write(
                "auth qr",
                {"status": "already_authorized", "account_id": me.id, "username": me.username},
                started,
            )
        timeout = float(getattr(args, "wait", 180) or 180)
        png_path = Path(
            getattr(args, "save", None) or (ctx.settings.data_dir / "login-qr.png")
        ).expanduser()
        print(
            "Scan the QR with Telegram on your phone (Settings > Devices > Link Desktop Device). "
            "This authorizes a separate telegram-native session; the macOS app is untouched.",
            file=sys.stderr,
            flush=True,
        )
        qr = await ctx.client.qr_login()
        _print_qr(qr.url, png_path)
        deadline = time.monotonic() + timeout
        while True:
            remaining = max(1.0, deadline - time.monotonic())
            try:
                me = await qr.wait(timeout=min(remaining, 60.0))
                break
            except asyncio.TimeoutError:
                if time.monotonic() >= deadline:
                    raise CliError(
                        "UNAVAILABLE",
                        "二维码登录等待超时，未完成授权。",
                        retriable=True,
                        details={"qr_path": str(png_path)},
                    ) from None
                await qr.recreate()
                _print_qr(qr.url, png_path)
            except SessionPasswordNeededError:
                try:
                    password = _prompt_password()
                except EOFError:
                    raise CliError(
                        "CONFIRMATION_REQUIRED",
                        "扫码已确认，但账号启用了两步验证。请接着运行 `auth password --password-stdin` 并用隐藏输入提供密码。",
                        details={"qr_pending": True, "qr_path": str(png_path)},
                    ) from None
                me = await ctx.client.sign_in(password=password)
                break
        if me is None:
            me = await ctx.client.get_me()
        ctx.me = me
        try:
            ctx.settings.session_path.chmod(0o600)
        except OSError:
            pass
        _register_account(ctx, me)
        try:
            png_path.unlink(missing_ok=True)
        except OSError:
            pass
        return emit_write(
            "auth qr",
            {
                "status": "authorized",
                "account_id": me.id,
                "username": me.username,
                "phone_masked": mask_phone(me.phone),
            },
            started,
        )
    finally:
        await ctx.close()


async def logout(ctx: TG, args) -> dict:
    started = time.monotonic()
    await ctx.connect()
    try:
        if not await ctx.authorized():
            return emit_write("auth logout", {"status": "noop", "reason": "not_authorized"}, started)
        if not getattr(args, "execute", False):
            return emit_preview(
                "auth logout",
                {"status": "preview", "action": "revoke current telegram-native session"},
                started,
            )
        await ctx.client.log_out()
        for suffix in ("", "-journal", "-wal", "-shm"):
            try:
                Path(str(ctx.settings.session_path) + suffix).unlink(missing_ok=True)
            except OSError:
                pass
        return emit_write("auth logout", {"status": "logged_out"}, started)
    finally:
        await ctx.close()


async def password(ctx: TG, args) -> dict:
    """Complete a pending QR/phone login that needs two-step verification."""
    started = time.monotonic()
    await ctx.connect()
    try:
        if await ctx.authorized():
            me = await ctx.require_authorized()
            return emit_write(
                "auth password",
                {"status": "already_authorized", "account_id": me.id, "username": me.username},
                started,
            )
        if getattr(args, "password_stdin", False):
            secret = sys.stdin.readline().rstrip("\n")
        else:
            secret = getpass.getpass("Two-step verification password: ")
        if not secret:
            raise CliError("VALIDATION", "两步验证密码不能为空。")
        try:
            me = await ctx.client.sign_in(password=secret)
        except SessionPasswordNeededError:
            raise CliError(
                "CONFIRMATION_REQUIRED",
                "两步验证密码未被接受，或登录状态已过期；请重新运行 auth qr。",
            ) from None
        if me is None:
            me = await ctx.client.get_me()
        if me is None:
            raise CliError("REJECTED", "两步验证后仍未获得账号信息；请重新运行 auth qr。")
        ctx.me = me
        try:
            ctx.settings.session_path.chmod(0o600)
        except OSError:
            pass
        _register_account(ctx, me)
        return emit_write(
            "auth password",
            {
                "status": "authorized",
                "account_id": me.id,
                "username": me.username,
                "phone_masked": mask_phone(me.phone),
            },
            started,
        )
    finally:
        await ctx.close()
