<div align="center">

# telegram-agent-cli

**为 AI Agent 打造的 Telegram 命令行工具。** 让 Claude Code、Codex 或任何 Agent 管理你的群组、频道、话题、成员和 Bot —— 走 Telegram 官方 MTProto 协议，JSON 输出，写操作只有加了 `--execute` 才会真正执行。

[![CI](https://github.com/risingjerry/telegram-agent-cli/actions/workflows/ci.yml/badge.svg)](https://github.com/risingjerry/telegram-agent-cli/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-2AABEE.svg)](LICENSE)
![Python 3.12+](https://img.shields.io/badge/python-3.12%2B-3776AB.svg)
![MTProto](https://img.shields.io/badge/Telegram-MTProto%20via%20Telethon-26A5E4.svg)
[![GitHub stars](https://img.shields.io/github/stars/risingjerry/telegram-agent-cli?style=social)](https://github.com/risingjerry/telegram-agent-cli/stargazers)

[English](README.md) · 简体中文

![telegram-native 演示：Agent 列出会话、预检后创建带话题的群、添加话题，未确认的封禁被拦下 —— 全部输出 tg.v1 JSON（演示数据均为虚构）](docs/demo.gif)

</div>

## 为什么

Agent 很擅长"给发布会建个群，加一个 Releases 话题，拉这三个人进来，把 Alice 设成管理员"，却很不擅长在 Telegram App 里点来点去。而 Bot API 的机器人既不能以你的身份建群、管话题，也不能和 @BotFather 对话。

**telegram-native** 把你自己账号的能力变成普通的命令行命令交给 Agent：

- **不做界面自动化** —— 通过 [Telethon](https://github.com/LonamiWebs/Telethon) 直接走 MTProto，使用独立的客户端会话，不操控也不读取 Telegram App。
- **每条命令一个 JSON 信封** —— 统一的 `tg.v1` 对象，含 `ok`、`outcome`、`data` 和稳定的 `error.code`，Agent 好解析。
- **默认安全** —— 所有写操作默认只是*预检*，加 `--execute` 才提交；删除、封禁、撤销等破坏性操作还要 `--yes` 或精确的 `--confirm`。
- **写操作幂等** —— 每次执行都有 `--request-id` 和本地回执；重放同一个 id 返回 `noop`，不会重复发送；结果未知时上锁，绝不盲目重试。
- **覆盖面广** —— 群、超级群、频道、论坛话题、成员、管理员、邀请链接、进群审批、消息、媒体、全局搜索、联系人、登录设备，以及通过 @BotFather 的 Bot 全生命周期。
- **自带 Agent Skill** —— [`skills/telegram-native/SKILL.md`](skills/telegram-native/SKILL.md) 让 Claude Code、Codex 等 Agent 开箱即会用。
- **多账号与代理** —— `--account work` 独立会话；`TG_PROXY` 支持 SOCKS5/SOCKS4/HTTP。

## 安装

```sh
git clone https://github.com/risingjerry/telegram-agent-cli && cd telegram-agent-cli && python3 -m venv .venv && .venv/bin/pip install -r requirements.txt && python3 install_global.py --with-skill
```

这会把 `telegram-native` 链接到 `~/.local/bin`，并（加 `--with-skill` 时）把 Agent Skill 复制到 `~/.claude/skills/` 和 `~/.agents/skills/`。已有文件会先备份，不会被覆盖。只想要命令行就去掉 `--with-skill`。

然后由你本人登录一次（不是 Agent）：

```sh
telegram-native auth qr        # 手机 Telegram：设置 → 设备 → 关联桌面设备，扫码
telegram-native auth status    # authorized: true
telegram-native chats list     # 只读检查
```

想用验证码登录：`telegram-native auth login`。开了两步验证：`telegram-native auth password --password-stdin`。验证码和密码只从隐藏的 stdin 读取。

运行要求：macOS 或 Linux，Python 3.12+。暂不支持 Windows。

## 配合 Claude Code / Codex / 其他 Agent 使用

仓库自带一个标准 [Agent Skill](skills/telegram-native/SKILL.md)（YAML 头 + 使用规则），内容包括：先查 `auth status`、先预检再 `--execute`、`--yes` / `--confirm` 闸门、如何读 `tg.v1` 信封、FloodWait 处理和常用配方。

任选一种方式安装：

```sh
# 1. 用上面的安装脚本
python3 install_global.py --with-skill

# 2. 用 skills CLI（https://github.com/vercel-labs/skills）
npx skills add risingjerry/telegram-agent-cli -g -a claude-code -a codex

# 3. 手动复制
cp -r skills/telegram-native ~/.claude/skills/    # Claude Code
cp -r skills/telegram-native ~/.agents/skills/    # Codex 和其他 Agent
```

之后直接说：*"建一个叫 Launch Crew 的话题群，加一个 Releases 话题，邀请 @alice。"* Agent 会逐步预检、给你看，再加 `--execute` 执行。

在本仓库里干活的 Agent 还会读到 [AGENTS.md](AGENTS.md)。

## 命令

`telegram-native commands` 输出机器可读的命令清单，`<命令> --help` 查看参数。完整说明见 [USAGE.md](USAGE.md)。

| 类别 | 命令 |
|---|---|
| 授权 | `auth qr` · `auth login` · `auth password` · `auth status` · `auth logout` |
| 发现 | `chats list` · `chat info` · `search global` |
| 群与频道 | `group create\|migrate\|delete\|set-join-request\|set-join-to-send` · `channel create\|delete` |
| 会话设置 | `chat set-title\|set-about\|set-username\|set-photo\|set-slowmode\|set-signatures\|set-discussion\|set-permissions` · `chat mute\|unmute\|archive\|unarchive\|pin\|unpin\|block\|unblock` |
| 论坛话题 | `forum status\|enable\|disable\|set-tabs` · `topic list\|create\|edit\|close\|reopen\|pin\|unpin\|delete\|read\|hide\|reorder` |
| 成员与管理员 | `members list\|add\|remove\|ban\|unban` · `admins list\|promote\|demote` · `admin-log list` |
| 邀请与审批 | `invite link\|list\|edit\|delete\|importers\|revoke\|join\|info` · `join-requests list\|approve\|reject\|approve-all\|reject-all` |
| 消息与媒体 | `messages list\|search\|get\|send\|send-album\|download\|forward\|edit\|delete\|pin\|unpin\|mark-read` · `comments info\|list\|reply` |
| Bot（@BotFather） | `bot create\|list\|token\|set-name\|set-about\|set-description\|set-commands\|revoke\|delete` |
| 联系人与账号 | `contacts list\|search\|resolve\|add\|delete` · `account profile\|set-name\|set-bio\|set-username\|set-photo` · `account devices …` · `account web-sessions …` |
| 本地 | `config show` · `config accounts list\|remove` · `docs` · `commands` |

大多数命令的目标参数 `CHAT` 可以是 `@username`、`t.me/…` 链接、数字 peer id、准确的标题，或 `me`（收藏夹）。

## 工作原理

```text
Agent ──shell──▶ telegram-native ──MTProto (Telethon)──▶ Telegram 服务器
                     │
                     ├─ stdout：一个 tg.v1 JSON 信封
                     ├─ ~/.local/share/telegram-native/  会话（0700）、回执
                     └─ ~/.config/telegram-native/       可选 config.json（0600）
```

1. **独立会话。** `auth qr` 会在你的账号下新增一个名为 `telegram-native` 的登录设备，在 Telegram「设置 → 设备」里可见，随时可撤销，或用 `auth logout --execute`。
2. **预检 → 执行。** 不加 `--execute` 的写操作只解析目标并返回 `outcome: "preview"` 和准确参数；加 `--execute` 才提交、确认并返回 `outcome: "executed"`。
3. **回执。** 每次执行按 `--request-id` 写一份回执，同一个 id 重放返回 `noop`；超时会变成 `OUTCOME_UNKNOWN` 并上锁，直到人工核对。
4. **稳定错误码。** `THROTTLED`（FloodWait，带 `retry_after`）、`PERMISSION_DENIED`、`TARGET_AMBIGUOUS`（带候选）、`BLOCKED`（缺确认）、`NOT_FOUND` 等。写操作从不自动重试。

```json
{"schema": "tg.v1", "command": "group create", "ok": true, "outcome": "preview",
 "data": {"status": "preview", "params": {"title": "Launch Crew", "forum": true}, "requires_execute": true},
 "error": null, "warnings": [], "meta": {"action": "group.create", "request_id": "group-create-…", "elapsed_ms": 3}}
```

## 限制

- 非官方客户端：Telegram 的反垃圾规则和 FloodWait 照样生效，群发消息或批量拉人可能导致账号受限。写操作不会自动重试，请按 `retry_after` 等待。
- 它是独立客户端：看不到 Secret Chat 和只存在于其他设备上的草稿，也不会操控 Telegram App。
- 仅命令行：没有 MCP Server，不支持 Windows（需要 macOS 或 Linux、Python 3.12+）。
- 部分 Telegram 接口本身没有对应能力（例如不能改论坛话题图标颜色，不支持按话题标记已读），逐条说明见 [docs/FEATURES.md](docs/FEATURES.md)。

## 常见问题

### 怎么让 Claude Code 操控 Telegram？

用 `--with-skill` 安装（或 `npx skills add risingjerry/telegram-agent-cli`），用 `telegram-native auth qr` 登录一次，然后直接用自然语言吩咐 Claude Code。Skill 会让它先查 `auth status`、每个写操作先预检、等你同意后才加 `--execute`。Codex、Cursor 以及任何能跑命令行的 Agent 同理。

### AI Agent 怎么学会用 telegram-native？

三层：[Agent Skill](skills/telegram-native/SKILL.md)（规则与配方）、`telegram-native commands`（所有命令的 JSON 清单）、每个命令的 `--help`（准确参数）。有了 `tg.v1` 信封，Agent 只需看 `ok`、`outcome` 和 `error.code`。

### 需要 Bot token 吗？

不需要。它通过 MTProto 以**你的用户账号**登录，所以能建群、管论坛话题、和 @BotFather 对话 —— 这些 Bot API 都做不到。它还能帮你*创建* Bot 并取回 token（默认打码，加 `--show-secret` 才显示完整）。

### 安全吗？会不会封号？

它用的是官方 MTProto 协议（和 Telegram 各端 App 一样），不做普通客户端做不到的事。但它是非官方客户端：Telegram 的反垃圾规则和 FloodWait 限制照样生效，群发消息或批量拉人可能导致账号受限。telegram-native 从不自动重试写操作，遇到限流返回 `THROTTLED` 和需要等待的秒数，所有改动都要 `--execute`。像你自己用 App 那样用它即可，风险由账号所有者承担。

### 会话存在哪里？谁能看到？

在 `~/.local/share/telegram-native/`（目录权限 0700）。会话文件等于账号的完整访问权，千万不要提交或分享。CLI 不会打印它，不把密钥放进命令行参数，回执里也不含 Bot token。随时可以在 Telegram「设备」页撤销。

### 内置的 api_id / api_hash 是什么？

默认使用开源 Telegram for macOS 客户端公开发布的 `api_id`/`api_hash`，它们标识的是客户端应用而不是你。想用自己在 [my.telegram.org](https://my.telegram.org) 申请的应用，设置 `TG_API_ID` 和 `TG_API_HASH`，或写进 `~/.config/telegram-native/config.json` 的 `api_id` / `api_hash`。

### 它会操控 Telegram 桌面 App 吗？

不会。它不点 App、不读 App 数据库，也不需要安装 App，是一个独立客户端。因此看不到 Secret Chat 和只存在于其他设备上的草稿。

### 能当 MCP Server 用吗？

目前不能。它是输出 JSON 的命令行工具，Claude Code、Codex 等 Agent 可以直接通过 shell 调用。

### 能用代理或多个账号吗？

可以。`TG_PROXY=socks5://user:pass@host:port`（也支持 `socks4://`、`http://`），或写进配置文件的 `"proxy"`。`telegram-native --account work auth qr` 建立独立的命名会话，之后任何命令加 `--account work` 即可。

## 卸载

```sh
telegram-native auth logout --execute          # 在 Telegram 服务端撤销会话
rm ~/.local/bin/telegram-native
rm -rf ~/.claude/skills/telegram-native ~/.agents/skills/telegram-native
rm -rf ~/.local/share/telegram-native ~/.config/telegram-native   # 会话、回执、配置
```

最后删除克隆下来的目录。

## 开发

```sh
.venv/bin/python -m unittest discover -s tests   # 离线测试，不联网、不用账号
.venv/bin/python docs/render.py                  # 重新生成 docs/demo.gif（虚构数据；需要 Chrome + ffmpeg）
```

更多背景：[docs/FEATURES.md](docs/FEATURES.md)、[docs/API-NOTES.md](docs/API-NOTES.md)、[docs/BOTFATHER.md](docs/BOTFATHER.md)。

## 致谢

基于 Lonami 的 [Telethon](https://github.com/LonamiWebs/Telethon)。与 Telegram 官方无关。

---

如果它帮你的 Agent 省下了几百次点击，点个 ⭐ 能让更多人发现它。

[![Star History Chart](https://api.star-history.com/svg?repos=risingjerry/telegram-agent-cli&type=Date)](https://star-history.com/#risingjerry/telegram-agent-cli&Date)

[MIT](LICENSE)
