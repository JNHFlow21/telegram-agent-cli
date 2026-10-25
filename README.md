<div align="center">

# telegram-agent-cli

**A Telegram CLI built for AI agents.** Let Claude Code, Codex or any agent manage your groups, channels, forum topics, members and bots — over Telegram's official MTProto API, with JSON output and writes that only happen when you say `--execute`.

[![CI](https://github.com/JNHFlow21/telegram-agent-cli/actions/workflows/ci.yml/badge.svg)](https://github.com/JNHFlow21/telegram-agent-cli/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-2AABEE.svg)](LICENSE)
![Python 3.12+](https://img.shields.io/badge/python-3.12%2B-3776AB.svg)
![MTProto](https://img.shields.io/badge/Telegram-MTProto%20via%20Telethon-26A5E4.svg)
[![GitHub stars](https://img.shields.io/github/stars/JNHFlow21/telegram-agent-cli?style=social)](https://github.com/JNHFlow21/telegram-agent-cli/stargazers)

English · [简体中文](README.zh-CN.md)

![telegram-native demo: an AI agent lists chats, previews and then creates a forum group, adds a topic, and is blocked from an unconfirmed ban — all as tg.v1 JSON (fake demo data)](docs/demo.gif)

</div>

## Why

Agents are good at "set up a group for the launch, add a Releases topic, invite these three people, make Alice an admin". They are bad at clicking through the Telegram app. Bot API bots cannot create groups, manage topics as you, or talk to @BotFather.

**telegram-native** gives an agent your own account's abilities as plain shell commands:

- **No GUI automation** — talks MTProto directly (via [Telethon](https://github.com/LonamiWebs/Telethon)) from its own client session. It never drives or reads the Telegram app.
- **One JSON envelope per command** — every command prints a single `tg.v1` object with `ok`, `outcome`, `data` and a stable `error.code`. Easy for an agent to parse.
- **Safe by default** — every write is a dry-run *preview* until you add `--execute`. Destructive actions (delete, ban, revoke …) also need `--yes` or an exact `--confirm` label.
- **Idempotent writes** — each execute has a `--request-id` and a local receipt. Replaying the same id returns `noop` instead of sending twice; unknown results are locked, never blindly retried.
- **Broad coverage** — groups, supergroups, channels, forum topics, members, admins, invite links, join-request approval, messages, media, global search, contacts, devices, and full bot lifecycle through @BotFather.
- **Ships an agent skill** — [`skills/telegram-native/SKILL.md`](skills/telegram-native/SKILL.md) teaches Claude Code, Codex and other agents the rules out of the box.
- **Multi-account and proxy** — `--account work` keeps a separate session; `TG_PROXY` supports SOCKS5/SOCKS4/HTTP.

## Install

```sh
git clone https://github.com/JNHFlow21/telegram-agent-cli && cd telegram-agent-cli && python3 -m venv .venv && .venv/bin/pip install -r requirements.txt && python3 install_global.py --with-skill
```

This links `telegram-native` into `~/.local/bin` and (with `--with-skill`) copies the agent skill into `~/.claude/skills/` and `~/.agents/skills/`. Existing files are backed up, never overwritten. Leave out `--with-skill` if you only want the CLI.

Then log in once (you, not the agent):

```sh
telegram-native auth qr        # scan in the phone app: Settings → Devices → Link Desktop Device
telegram-native auth status    # authorized: true
telegram-native chats list     # read-only check
```

Prefer a code? `telegram-native auth login`. Two-step verification: `telegram-native auth password --password-stdin`. Codes and passwords are only read from hidden stdin.

Requirements: macOS or Linux, Python 3.12+. Windows is not supported yet.

## Use with Claude Code / Codex / other agents

The repo ships a standard [Agent Skill](skills/telegram-native/SKILL.md) (YAML front matter + instructions) covering: check `auth status` first, preview before `--execute`, the `--yes` / `--confirm` gate, reading the `tg.v1` envelope, FloodWait handling and common recipes.

Install it any of these ways:

```sh
# 1. with the installer above
python3 install_global.py --with-skill

# 2. with the skills CLI (https://github.com/vercel-labs/skills)
npx skills add JNHFlow21/telegram-agent-cli -g -a claude-code -a codex

# 3. by hand
cp -r skills/telegram-native ~/.claude/skills/    # Claude Code
cp -r skills/telegram-native ~/.agents/skills/    # Codex and other agents
```

Then just ask: *"Create a forum group called Launch Crew, add a Releases topic and invite @alice."* The agent will preview each step, show you, and only then run it with `--execute`.

Agents working inside this repo also get [AGENTS.md](AGENTS.md).

## Commands

Run `telegram-native commands` for the machine-readable list, and `<command> --help` for flags. Full reference: [USAGE.md](USAGE.md) (Chinese).

| Area | Commands |
|---|---|
| Auth | `auth qr` · `auth login` · `auth password` · `auth status` · `auth logout` |
| Discover | `chats list` · `chat info` · `search global` |
| Groups & channels | `group create\|migrate\|delete\|set-join-request\|set-join-to-send` · `channel create\|delete` |
| Chat settings | `chat set-title\|set-about\|set-username\|set-photo\|set-slowmode\|set-signatures\|set-discussion\|set-permissions` · `chat mute\|unmute\|archive\|unarchive\|pin\|unpin\|block\|unblock` |
| Forum topics | `forum status\|enable\|disable\|set-tabs` · `topic list\|create\|edit\|close\|reopen\|pin\|unpin\|delete\|read\|hide\|reorder` |
| Members & admins | `members list\|add\|remove\|ban\|unban` · `admins list\|promote\|demote` · `admin-log list` |
| Invites & approvals | `invite link\|list\|edit\|delete\|importers\|revoke\|join\|info` · `join-requests list\|approve\|reject\|approve-all\|reject-all` |
| Messages & media | `messages list\|search\|get\|send\|send-album\|download\|forward\|edit\|delete\|pin\|unpin\|mark-read` · `comments info\|list\|reply` |
| Bots (@BotFather) | `bot create\|list\|token\|set-name\|set-about\|set-description\|set-commands\|revoke\|delete` |
| Contacts & account | `contacts list\|search\|resolve\|add\|delete` · `account profile\|set-name\|set-bio\|set-username\|set-photo` · `account devices …` · `account web-sessions …` |
| Local | `config show` · `config accounts list\|remove` · `docs` · `commands` |

Most commands take a `CHAT` target: `@username`, a `t.me/…` link, a numeric peer id, an exact title, or `me` (Saved Messages).

## How it works

```text
agent ──shell──▶ telegram-native ──MTProto (Telethon)──▶ Telegram servers
                     │
                     ├─ stdout: one tg.v1 JSON envelope
                     ├─ ~/.local/share/telegram-native/  session (0700), receipts
                     └─ ~/.config/telegram-native/       optional config.json (0600)
```

1. **Own session.** `auth qr` creates a new authorized device on your account, named `telegram-native`. It appears in Telegram under Settings → Devices and can be revoked there or with `auth logout --execute`.
2. **Preview → execute.** A write without `--execute` resolves the target and returns `outcome: "preview"` with the exact parameters. With `--execute` it commits, confirms, and returns `outcome: "executed"`.
3. **Receipts.** Each execute writes a receipt keyed by `--request-id`. The same id replays as `noop`. A timeout becomes `OUTCOME_UNKNOWN` and is locked until a human checks.
4. **Stable errors.** `THROTTLED` (FloodWait, with `retry_after`), `PERMISSION_DENIED`, `TARGET_AMBIGUOUS` (with candidates), `BLOCKED` (missing confirmation), `NOT_FOUND`, and more. The CLI never auto-retries a write.

```json
{"schema": "tg.v1", "command": "group create", "ok": true, "outcome": "preview",
 "data": {"status": "preview", "params": {"title": "Launch Crew", "forum": true}, "requires_execute": true},
 "error": null, "warnings": [], "meta": {"action": "group.create", "request_id": "group-create-…", "elapsed_ms": 3}}
```

## FAQ

### How do I let Claude Code control Telegram?

Install telegram-native with `--with-skill` (or `npx skills add JNHFlow21/telegram-agent-cli`), log in once with `telegram-native auth qr`, and ask Claude Code in plain language. The skill tells it to check `auth status`, preview every write, and wait for your go-ahead before adding `--execute`. The same works for Codex, Cursor and any agent that can run shell commands.

### How does my AI agent learn to use telegram-native?

Three layers: the [agent skill](skills/telegram-native/SKILL.md) (rules and recipes), `telegram-native commands` (a JSON list of every command), and `--help` on each command for exact flags. The `tg.v1` envelope means the agent only needs to read `ok`, `outcome` and `error.code`.

### Does it need a bot token?

No. It logs in as **your user account** through MTProto, which is what lets it create groups, manage forum topics and talk to @BotFather — things the Bot API cannot do. It can *create* bots for you and fetch their tokens (masked unless you pass `--show-secret`).

### Is it safe? Will my account get banned?

It uses the official MTProto protocol, the same one Telegram apps use, and does nothing a normal client cannot. But it is an unofficial client: Telegram's anti-spam rules and FloodWait limits still apply, and mass messaging or mass adding members can get an account restricted. telegram-native never auto-retries writes, returns `THROTTLED` with the wait time, and requires `--execute` for every change. Use it like you would use the app yourself; the risk is the account owner's.

### Where is my session stored, and what can see it?

In `~/.local/share/telegram-native/` (directory mode 0700). The session file gives full access to your account — never commit or share it. The CLI never prints it, never puts secrets in command-line arguments, and receipts never contain bot tokens. Revoke the session any time from Telegram's Devices screen.

### What are the bundled api_id / api_hash?

By default it uses the public `api_id`/`api_hash` published in the open-source Telegram for macOS client; they identify the client app, not you. To use your own app from [my.telegram.org](https://my.telegram.org), set `TG_API_ID` and `TG_API_HASH`, or put `api_id` / `api_hash` in `~/.config/telegram-native/config.json`.

### Does it control the Telegram desktop app?

No. It does not click the app, read its database, or need it installed. It is a separate client. It therefore cannot see Secret Chats or drafts that only exist on another device.

### Can it be used as an MCP server?

Not currently. It is a CLI with JSON output, which agents like Claude Code and Codex can call directly through their shell tool.

### Can I use a proxy or several accounts?

Yes. `TG_PROXY=socks5://user:pass@host:port` (also `socks4://`, `http://`), or `"proxy"` in the config file. `telegram-native --account work auth qr` creates an independent named session; add `--account work` to any command.

## Uninstall

```sh
telegram-native auth logout --execute          # revoke the session on Telegram's side
rm ~/.local/bin/telegram-native
rm -rf ~/.claude/skills/telegram-native ~/.agents/skills/telegram-native
rm -rf ~/.local/share/telegram-native ~/.config/telegram-native   # session, receipts, config
```

Then delete the cloned folder.

## Development

```sh
.venv/bin/python -m unittest discover -s tests   # offline tests, no network, no account
.venv/bin/python docs/render.py                  # regenerate docs/demo.gif (fake data; needs Chrome + ffmpeg)
```

More background: [docs/FEATURES.md](docs/FEATURES.md), [docs/API-NOTES.md](docs/API-NOTES.md), [docs/BOTFATHER.md](docs/BOTFATHER.md) (Chinese).

## Credits

Built on [Telethon](https://github.com/LonamiWebs/Telethon) by Lonami. Not affiliated with Telegram.

---

If this saved your agent a few hundred clicks, a ⭐ helps other people find it.

[MIT](LICENSE)
