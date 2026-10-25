"""Account profile and login-device management."""
from __future__ import annotations

import time
from pathlib import Path

from telethon.tl import functions, types

from ..client import TG
from ..envelope import emit_read
from ..errors import CliError
from ..serialize import iso, user_to_dict
from .common import add_write_flags, run_write


async def profile(ctx: TG, args) -> dict:
    started = time.monotonic()
    await ctx.connect()
    try:
        me = await ctx.require_authorized()
        full = await ctx.client(functions.users.GetFullUserRequest(id=me))
        return emit_read(
            "account profile",
            {
                "me": user_to_dict(me),
                "about": getattr(full.full_user, "about", None),
                "common_chats_count": getattr(full.full_user, "common_chats_count", None),
                "phone_calls_available": getattr(full.full_user, "phone_calls_available", None),
                "phone_calls_private": getattr(full.full_user, "phone_calls_private", None),
                "premium": bool(getattr(me, "premium", False)),
                "birthday": getattr(full.full_user, "birthday", None),
            },
            started,
            **ctx.account_meta(),
        )
    finally:
        await ctx.close()


async def set_name(ctx: TG, args) -> dict:
    await ctx.connect()
    try:
        await ctx.require_authorized()
        if args.first_name is None and args.last_name is None:
            raise CliError("VALIDATION", "至少提供 --first-name 或 --last-name。")

        async def execute():
            await ctx.client(
                functions.account.UpdateProfileRequest(
                    first_name=args.first_name, last_name=args.last_name
                )
            )
            me = await ctx.client.get_me()
            return {"me": user_to_dict(me)}

        params = {"first_name": args.first_name, "last_name": args.last_name}
        return await run_write(
            ctx,
            args,
            command="account set-name",
            action="account.set_name",
            params=params,
            preview=params,
            execute=execute,
        )
    finally:
        await ctx.close()


async def set_bio(ctx: TG, args) -> dict:
    await ctx.connect()
    try:
        await ctx.require_authorized()
        if args.about is None:
            raise CliError("VALIDATION", "--about 不能为空。")

        async def execute():
            await ctx.client(functions.account.UpdateProfileRequest(about=args.about))
            return {"about": args.about}

        params = {"about": args.about}
        return await run_write(
            ctx,
            args,
            command="account set-bio",
            action="account.set_bio",
            params=params,
            preview=params,
            execute=execute,
        )
    finally:
        await ctx.close()


async def set_username(ctx: TG, args) -> dict:
    await ctx.connect()
    try:
        await ctx.require_authorized()
        username = (args.username or "").lstrip("@")

        async def execute():
            await ctx.client(functions.account.UpdateUsernameRequest(username=username))
            return {"username": username or None}

        params = {"username": username or None}
        return await run_write(
            ctx,
            args,
            command="account set-username",
            action="account.set_username",
            params=params,
            preview=params,
            execute=execute,
        )
    finally:
        await ctx.close()


async def set_photo(ctx: TG, args) -> dict:
    await ctx.connect()
    try:
        await ctx.require_authorized()
        path = Path(args.photo).expanduser()
        if not path.is_file():
            raise CliError("VALIDATION", f"找不到图片文件：{path}")
        if path.stat().st_size > 10 * 1024 * 1024:
            raise CliError("VALIDATION", "头像文件不能超过 10 MB。")

        async def execute():
            uploaded = await ctx.client.upload_file(str(path))
            result = await ctx.client(
                functions.photos.UploadProfilePhotoRequest(file=uploaded)
            )
            photo = getattr(result, "photo", None)
            return {"photo_id": getattr(photo, "id", None), "photo": str(path)}

        params = {"photo": str(path), "size": path.stat().st_size}
        return await run_write(
            ctx,
            args,
            command="account set-photo",
            action="account.set_photo",
            params=params,
            preview=params,
            execute=execute,
        )
    finally:
        await ctx.close()


def _authorization_to_dict(auth: types.Authorization) -> dict:
    return {
        "hash": auth.hash,
        "current": bool(auth.current),
        "device_model": auth.device_model,
        "platform": auth.platform,
        "system_version": auth.system_version,
        "api_id": auth.api_id,
        "app_name": auth.app_name,
        "app_version": auth.app_version,
        "date_created": iso(auth.date_created),
        "date_active": iso(auth.date_active),
        "ip": auth.ip,
        "country": auth.country,
        "region": auth.region,
        "official_app": bool(auth.official_app),
        "password_pending": bool(auth.password_pending),
        "encrypted_requests_disabled": bool(auth.encrypted_requests_disabled),
        "call_requests_disabled": bool(auth.call_requests_disabled),
    }


async def devices_list(ctx: TG, args) -> dict:
    started = time.monotonic()
    await ctx.connect()
    try:
        await ctx.require_authorized()
        result = await ctx.client(functions.account.GetAuthorizationsRequest())
        items = [_authorization_to_dict(a) for a in (getattr(result, "authorizations", None) or [])]
        return emit_read(
            "account devices list",
            {"count": len(items), "items": items},
            started,
            **ctx.account_meta(),
        )
    finally:
        await ctx.close()


async def devices_revoke(ctx: TG, args) -> dict:
    await ctx.connect()
    try:
        await ctx.require_authorized()
        result = await ctx.client(functions.account.GetAuthorizationsRequest())
        target = next(
            (a for a in (getattr(result, "authorizations", None) or []) if a.hash == args.hash),
            None,
        )
        if target is None:
            raise CliError("NOT_FOUND", f"找不到设备授权 hash={args.hash}。")
        if target.current:
            raise CliError(
                "BLOCKED",
                "这是当前 CLI 会话；请用 `auth logout --execute` 撤销它。",
            )

        async def execute():
            await ctx.client(functions.account.ResetAuthorizationRequest(hash=args.hash))
            return {"hash": args.hash, "revoked": True, "device_model": target.device_model}

        params = {"hash": args.hash, "device_model": target.device_model}
        return await run_write(
            ctx,
            args,
            command="account devices revoke",
            action="account.devices.revoke",
            params=params,
            preview=params,
            execute=execute,
            destructive=True,
            confirm_label=str(args.hash),
        )
    finally:
        await ctx.close()


async def devices_revoke_others(ctx: TG, args) -> dict:
    await ctx.connect()
    try:
        await ctx.require_authorized()
        result = await ctx.client(functions.account.GetAuthorizationsRequest())
        others = [
            a for a in (getattr(result, "authorizations", None) or []) if not a.current
        ]

        async def execute():
            hashes = []
            for auth in others:
                await ctx.client(
                    functions.account.ResetAuthorizationRequest(hash=auth.hash)
                )
                hashes.append(auth.hash)
            return {"revoked": hashes, "count": len(hashes)}

        params = {"count": len(others), "hashes": [a.hash for a in others]}
        return await run_write(
            ctx,
            args,
            command="account devices revoke-others",
            action="account.devices.revoke_others",
            params=params,
            preview=params,
            execute=execute,
            destructive=True,
            confirm_label="others",
        )
    finally:
        await ctx.close()


def _web_auth_to_dict(auth: types.WebAuthorization) -> dict:
    return {
        "hash": auth.hash,
        "bot_id": auth.bot_id,
        "domain": auth.domain,
        "browser": auth.browser,
        "platform": auth.platform,
        "date_created": iso(auth.date_created),
        "date_active": iso(auth.date_active),
        "ip": auth.ip,
        "region": auth.region,
    }


async def web_sessions_list(ctx: TG, args) -> dict:
    started = time.monotonic()
    await ctx.connect()
    try:
        await ctx.require_authorized()
        result = await ctx.client(functions.account.GetWebAuthorizationsRequest())
        items = [
            _web_auth_to_dict(a)
            for a in (getattr(result, "authorizations", None) or [])
        ]
        return emit_read(
            "account web-sessions list",
            {"count": len(items), "items": items},
            started,
            **ctx.account_meta(),
        )
    finally:
        await ctx.close()


async def web_sessions_revoke(ctx: TG, args) -> dict:
    await ctx.connect()
    try:
        await ctx.require_authorized()

        async def execute():
            await ctx.client(functions.account.ResetWebAuthorizationRequest(hash=args.hash))
            return {"hash": args.hash, "revoked": True}

        params = {"hash": args.hash}
        return await run_write(
            ctx,
            args,
            command="account web-sessions revoke",
            action="account.web_sessions.revoke",
            params=params,
            preview=params,
            execute=execute,
            destructive=True,
            confirm_label=str(args.hash),
        )
    finally:
        await ctx.close()


async def web_sessions_revoke_all(ctx: TG, args) -> dict:
    await ctx.connect()
    try:
        await ctx.require_authorized()

        async def execute():
            await ctx.client(functions.account.ResetWebAuthorizationsRequest())
            return {"revoked_all": True}

        return await run_write(
            ctx,
            args,
            command="account web-sessions revoke-all",
            action="account.web_sessions.revoke_all",
            params={},
            preview={"revoke_all_web_sessions": True},
            execute=execute,
            destructive=True,
            confirm_label="all",
        )
    finally:
        await ctx.close()
