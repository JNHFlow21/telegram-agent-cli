# Agent instructions (telegram-native)

Guide for AI coding agents (Claude Code, Codex, Cursor, etc.) working in or
with this repository. The ready-made agent skill lives in
`skills/telegram-native/SKILL.md`.

`telegram-native` is a standalone Telegram CLI. Its output contract is `tg.v1`,
documented in `USAGE.md`; every command prints exactly one JSON envelope on
stdout. Start with `telegram-native auth status`, then `telegram-native commands`
for the command list. Any new command must emit through
`telegram_native/envelope.py` rather than printing its own JSON shape.

- Treat the Telegram session file, `api_hash`, bot tokens and chat content as
  private. Never commit them, never print them in logs, and never pass them as
  command-line arguments.
- The CLI is a separate Telegram client authorization under the same account.
  It does **not** drive the Telegram macOS GUI and does not read the app's
  encrypted Postbox database.
- Read commands are safe. Every write command defaults to a preview and only
  performs the action with `--execute`. Destructive commands also require
  `--yes` or an exact `--confirm` value.
- Do not send test messages to real people or groups. Live smoke tests are
  limited to `auth status`, `chats list`, `chat info`, `messages list`,
  `topic list`, and explicitly owner-authorized reversible self-only settings
  (for example `chat mute me` / `chat unmute me`) after the owner has
  authorized a session.
- BotFather actions create or mutate real bots. They require explicit owner
  authorization and a fresh `--request-id`; never run them as part of install
  or automated tests.
- Run the offline tests with `python -m unittest discover -s tests`; they never
  touch the network or a real account.
- If a write result is uncertain (timeout / connection loss), do not retry with
  a new request id. Inspect the receipt and the Telegram client first.
