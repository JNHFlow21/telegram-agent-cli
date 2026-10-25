# Changelog

## 0.1.0 — 2026-10-25

First public release.

- Telegram CLI for AI agents over the official MTProto API (Telethon), logging in as your own user account; QR or code login, multi-account (`--account`) and proxy (`TG_PROXY`) support.
- Every command prints one `tg.v1` JSON envelope with `ok`, `outcome`, `data` and a stable `error.code`.
- Writes are a dry-run preview until `--execute`; destructive actions also need `--yes` or an exact `--confirm` label; idempotent execution with `--request-id` and local receipts, and no automatic retry of writes.
- Coverage: groups, supergroups, channels, forum topics, members, admins, invite links, join requests, messages, media, global search, contacts, devices, and the bot lifecycle through @BotFather.
- Bundled agent skill (`skills/telegram-native`), `install_global.py` installer with `--with-skill`, offline unit tests and CI.
