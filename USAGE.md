# telegram-native：Agent 使用说明

## 1. 这是什么

`telegram-native` 是一个面向 Agent 的 Telegram CLI。它不驱动 Telegram for macOS
的界面，也不依赖屏幕坐标、Accessibility 或 Computer Use；它用 Telegram 官方
MTProto 协议建立一个**独立的 Telegram 客户端会话**，因此可以脚本化、可幂等、
可审计地完成：

- 私聊 / 群组 / 频道的发现、信息、成员、管理员；
- 建群、建频道、改标题/简介/用户名/头像；
- 开启/关闭话题（Forum Topics），创建/编辑/关闭/置顶/删除话题；
- 邀请/移出/封禁/解封成员，设置/撤销管理员；
- 邀请链接、加入群/频道；
- 发消息、改消息、删消息、置顶、标记已读；
- 通过 @BotFather 创建 Bot、取 token、改名称/简介/描述/命令、撤销 token、删除 Bot。

同一账号可以同时存在多个登录设备。CLI 的会话会出现在 Telegram 的
「Settings → Devices」里，名字是 `telegram-native`；不需要时随时撤销。

### 与“操控已安装 App”的区别

| 方式 | 是否需要 GUI | 稳定性 | 能做什么 |
|---|---|---|---|
| Computer Use / Chrome Use | 是 | 受窗口/焦点/版本影响 | 只能点界面 |
| 读取 App 的 Postbox 数据库 | 否 | 受 App 版本/加密影响，写操作仍要协议 | 主要是只读缓存 |
| **telegram-native（本 CLI）** | **否** | **跟随官方 API，App 升级不影响** | **完整账号侧操作** |

本 CLI 不读取 Telegram App 的 `postbox/db`，也不复制它的登录密钥。它使用
TelegramSwift 开源客户端公开的 `api_id/api_hash`（标识客户端应用，不标识用户），
通过一次人工授权建立自己的 session。这样不会因为 App 升级或数据库加密变化而失效。

## 2. 安装与第一次授权

```sh
git clone https://github.com/risingjerry/telegram-agent-cli && cd telegram-agent-cli
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
python3 install_global.py            # 加 --with-skill 同时安装 Agent Skill
```

### 推荐：扫码授权（不需要输入手机号）

```sh
telegram-native auth qr
```

终端会打印一个二维码，并把它保存到：

```
~/.local/share/telegram-native/login-qr.png
```

用手机 Telegram 扫码：

1. 手机 Telegram → Settings → Devices → **Link Desktop Device**；
2. 扫描二维码；
3. 如果账号开了两步验证，按提示输入密码；
4. 命令返回 `outcome: executed`、`status: authorized`。

二维码约 30–60 秒过期，CLI 会自动刷新；`--wait 300` 可延长等待。授权成功后
QR 图片会被删除。

### 备选：手机号 + 验证码

```sh
telegram-native auth login
# 或先设置 TG_PHONE，避免交互输入手机号
telegram-native auth login --phone +8613800000000
```

验证码只从 stdin 读取，不写入参数、环境变量或日志。两步验证密码用 `getpass`
读取。

如果扫码后账号要求两步验证，`auth qr` 会以 `CONFIRMATION_REQUIRED` 退出并保留
pending session；接着用隐藏输入完成：

```sh
telegram-native auth password --password-stdin
# 密码从 stdin 读，不写参数/环境/日志
```

### 检查/撤销

```sh
telegram-native auth status
telegram-native auth logout --execute     # 撤销 CLI 会话
```

`auth status` 不会因为未登录而报错：返回 `authorized: false` 和下一步命令。

### 关于 api_id / api_hash

默认使用 Telegram for macOS 开源客户端的公开凭据（`api_id=9`）。如果 Telegram
对公开 api_id 有限制，或你有自己的 my.telegram.org 应用，可用环境变量覆盖：

```sh
export TG_API_ID=123456
export TG_API_HASH=...
```

也可以写入 `~/.config/telegram-native/config.json`（0600）：

```json
{"api_id": 123456, "api_hash": "..."}
```

## 3. 统一输出契约（tg.v1）

所有命令向 stdout 打印**一个** JSON 信封；诊断/二维码/进度走 stderr。成功和失败
结构相同：

```json
{
  "schema": "tg.v1",
  "command": "group create",
  "ok": true,
  "outcome": "preview",
  "data": { "...": "命令结果" },
  "error": null,
  "warnings": [],
  "meta": {
    "ts": "2026-10-24T11:45:00Z",
    "action": "group.create",
    "request_id": "group-create-...",
    "account_id": 123456789,
    "account_username": "owner",
    "phone_masked": "+86******123",
    "dc_id": 2,
    "elapsed_ms": 128
  }
}
```

- `ok`：唯一成败布尔；Agent 只需看它。
- `outcome`：`readonly`（只读成功）、`preview`（预检，未改）、`executed`（已提交
  并确认）、`noop`（成功但无变化/幂等重放）、`unknown`（已提交但未确认，已上锁）。
- `error.code`：稳定机器码，见下表。
- 失败时 `data=null`，原始细节在 `error.details`；`error.message` 不含聊天正文或
  凭证。

### 错误码

| code | 含义 | 可重试 |
|---|---|---|
| `OUTCOME_UNKNOWN` | 已提交但未能确认，已写 unknown receipt；**不要换 request-id 盲重试** | 否 |
| `THROTTLED` | Telegram FloodWait / 慢速模式；`details.retry_after` | 是（等够再试） |
| `UNAVAILABLE` | 网络/超时/连接失败 | 通常是 |
| `PERMISSION_DENIED` | 需要管理员/群主权限 | 否 |
| `UNSUPPORTED` | 该会话类型/API 层不支持 | 否 |
| `REJECTED` | Telegram 拒绝（用户名占用、参数被拒等） | 否 |
| `VALIDATION` | 本地参数/输入校验失败 | 否 |
| `NOT_FOUND` | 目标不存在/不可见/不在群内 | 否 |
| `TARGET_AMBIGUOUS` | 标题匹配到多个会话；`details.candidates` 给出候选 | 否 |
| `CONFIRMATION_REQUIRED` | 未登录，或需要两步验证/单独确认 | 否 |
| `BLOCKED` | 本地安全门拦下（缺 `--yes`/`--confirm`） | 否 |
| `INTERNAL` | 未预期错误；`details.diagnostic` | 否 |

## 4. 写操作安全模型

### 4.1 默认预检，`--execute` 才提交

所有 Telegram 状态变更命令默认只返回 `outcome: preview`，不会改任何东西：

```sh
telegram-native group create --title "Demo" --forum
# 返回 preview：目标、参数、将执行的动作
telegram-native group create --title "Demo" --forum --execute
# 返回 executed
```

### 4.2 破坏性操作需要确认

`delete`、`ban`、`remove`、`leave`、`revoke`、`demote`、`disable` 等命令需要
`--yes`，或 `--confirm` 与目标标签完全一致：

```sh
telegram-native members ban "Demo" --users @spammer --yes --execute
telegram-native channel delete @old_channel --confirm old_channel --execute
```

预检阶段不要求确认；只有真正提交时才检查。

### 4.3 幂等键与回执

每次 `--execute` 会生成或复用 `--request-id`，并把结果写到：

```
~/.local/share/telegram-native/receipts/<sha256(request_id)>.json
```

回执文件包含命令参数和业务结果摘要（例如消息 id、邀请链接、群标题），属于私密
运行数据；不要提交到 Git 或上传。Bot token 不会写进回执，只写 0600 的 bots 文件。

同一个 request-id 再次执行时：

- 已 `executed`：返回 `outcome: noop` + 原结果，**不会重复创建/发送**；
- 已 `unknown`：返回 `OUTCOME_UNKNOWN`，拒绝重试，直到你人工核对。

需要重试一条结果未知的写操作时，先核对 Telegram，再显式换一个新的
`--request-id`。不要用同一个 id 反复提交。

### 4.4 不自动重试

Telegram 的 FloodWait、慢速模式、网络超时都不会被本地静默重试。特别是发送、
建群、建 Bot 这类操作，超时可能已经成功；CLI 会写 `unknown` 回执并停止。

## 5. 目标引用（CHAT）

大多数命令的第一个位置参数是 `CHAT`，支持：

- `@username`（公开群/频道/Bot/用户）；
- `t.me/username`、`https://t.me/username`；
- `t.me/c/123456/78`（私有超级群消息链接）；
- 数字 id：用户 id、`-100...` 超级群/频道 id、`-...` 普通群 id；
- 标题/姓名：先精确匹配，再唯一子串匹配；多个匹配返回 `TARGET_AMBIGUOUS` 和候选；
- `me` / `self`（自己的 Saved Messages）；
- `botfather` / `@BotFather`。

标题匹配会遍历当前账号的会话列表。会话非常多时建议直接用 `@username` 或
`peer_id`。用 `chats list` 可以先拿到 `peer_id`。

## 6. 命令参考

所有命令都可以加 `--help`。`telegram-native commands` 输出机器可读的命令清单。

### 6.1 会话发现

```sh
telegram-native chats list
telegram-native chats list --type group --type channel --limit 200
telegram-native chats list --type private --search alice
telegram-native chats list --archived
telegram-native chats list --all --with-last
telegram-native chat info @group
telegram-native chat info -1001234567890
```

`chat info` 返回：id、peer_id、类型（private/bot/group/supergroup/channel）、标题、
用户名、是否 forum、成员数、简介、管理员权限、默认禁言权限、邀请链接（若已有）、
Bot 信息等。

### 6.2 群组与频道

```sh
# 创建超级群（默认），可直接开话题
telegram-native group create --title "Ops" --about "internal" --forum --execute
telegram-native group create --title "Ops" --users @alice,@bob --execute

# 创建普通群（不支持话题）
telegram-native group create --title "Chat" --basic --users @alice --execute

# 创建广播频道
telegram-native channel create --title "Announcements" --username my_channel --execute
telegram-native channel create --title "Announcements" --users @editor --execute

# 群/频道资料
telegram-native chat set-title @ops --title "Ops 2" --execute
telegram-native chat set-about @ops --about "new about" --execute
telegram-native chat set-username @ops --username ops_public --execute
telegram-native chat set-photo @ops --photo /absolute/path/avatar.png --execute

# 群/频道高级设置
telegram-native chat set-slowmode @ops --seconds 30 --execute
telegram-native group set-join-request @ops --enabled --execute
telegram-native group set-join-to-send @ops --enabled --execute
telegram-native chat set-signatures @channel --enabled --execute
telegram-native chat set-discussion @channel --group @ops --execute

# 删除（仅创建者；群/超级群/频道）
telegram-native group delete @ops --confirm ops --execute
telegram-native channel delete @old --confirm old --execute
```

说明：

- `group create` 默认创建 **supergroup**（`megagroup=True`），因为普通群不支持
  话题、精细管理员权限和公开用户名。
- `--forum` 创建时直接开启话题；也可以先建群再 `forum enable`。
- `--username` 只对超级群/频道有效；普通群没有公开用户名。
- 创建后设置用户名/头像失败不会回滚已创建的群/频道；返回的 `warnings` 会列出
  哪一步失败。`--basic` 搭配 `--username/--photo` 会返回 warning 并忽略这两项。
- 慢速模式只接受 `0,5,10,30,60,300,900,3600` 秒；普通群不支持。
- `group set-join-request` 开启后，用 `join-requests list/approve/reject` 审批。
- 普通群不能 `group delete`；只能 `chat leave`，或先迁为超级群。

### 6.3 话题（Forum Topics）

```sh
telegram-native forum status @ops
telegram-native forum enable @ops --execute
telegram-native forum disable @ops --confirm ops --execute

telegram-native topic list @ops --all --limit 200
telegram-native topic list @ops --search release
telegram-native topic create @ops --title "Release" --icon-color 0x6FB9F0 --execute
telegram-native topic edit @ops --topic-id 123 --title "Releases" --execute
telegram-native topic close @ops --topic-id 123 --execute
telegram-native topic reopen @ops --topic-id 123 --execute
telegram-native topic pin @ops --topic-id 123 --execute
telegram-native topic unpin @ops --topic-id 123 --execute
telegram-native topic delete @ops --topic-id 123 --confirm 123 --execute
```

注意：

- 只有 **supergroup** 且 `forum=true` 才能列/建话题；普通群会返回 `UNSUPPORTED`。
- 话题消息发送：`messages send @ops --topic 123 --text "..." --execute`。
- `topic delete` 会删除该话题及其消息，不可恢复。
- 当前 API 层不支持在编辑话题时改 `icon_color`；创建时可以传 `--icon-color`
  （只接受六种官方 RGB：`0x6FB9F0, 0xFFD67E, 0xCB86DB, 0x8EEE98, 0xFF93B2, 0xFB6F5F`）。
- 标题最多 128 UTF-8 字节；`General`（topic id=1）不能删除。
- `messages send --topic 1` 会按 General 处理（不设置 reply_to）；`--topic` 与
  `--reply-to` 不能同时使用。

### 6.4 成员

```sh
telegram-native members list @ops
telegram-native members list @ops --filter admins
telegram-native members list @ops --filter bots
telegram-native members list @ops --filter recent --limit 50
telegram-native members list @ops --filter kicked
telegram-native members list @ops --search alice

telegram-native members add @ops --users @alice,@bob --execute
telegram-native members remove @ops --users @alice --yes --execute
telegram-native members ban @ops --users @spammer --yes --execute
telegram-native members ban @ops --users @spammer --days 7 --yes --execute
telegram-native members unban @ops --users @spammer --execute
```

- `remove` = 移出（公开群可能可再次加入）；`ban` = 封禁（`--days` 不填为永久）。
- 普通群（basic group）不支持精细封禁权限；`ban` 会返回 `UNSUPPORTED`。
- 需要邀请/封禁权限；权限不足返回 `PERMISSION_DENIED`。

### 6.5 管理员

```sh
telegram-native admins list @ops
telegram-native admins promote @ops --users @alice --execute
telegram-native admins promote @ops --users @alice \
  --rights invite_users,pin_messages,delete_messages,ban_users,manage_topics \
  --rank "Ops Lead" --execute
telegram-native admins demote @ops --users @alice --yes --execute
```

默认权限：`invite_users,pin_messages,delete_messages,ban_users`；如果群已开话题，
自动加上 `manage_topics`。可用权限：

```
change_info, post_messages, edit_messages, delete_messages, ban_users,
invite_users, pin_messages, add_admins, anonymous, manage_call,
manage_topics, manage_direct_messages, manage_ranks
```

普通群只能设/撤管理员，不能精细分配权限。

### 6.6 邀请链接

```sh
telegram-native invite link @ops --execute
telegram-native invite link @ops --expire-days 7 --usage-limit 20 --execute
telegram-native invite link @ops --request-needed --execute
telegram-native invite info --link https://t.me/+AbCdEf
telegram-native invite revoke @ops --link https://t.me/+AbCdEf --confirm https://t.me/+AbCdEf --execute
telegram-native invite join --link https://t.me/+AbCdEf --execute
telegram-native invite join --link @public_group --execute
```

`invite info` 对私有链接调用 `CheckChatInvite`，返回标题、人数、是否需要批准、
过期时间；对公开 `@username` 返回会话信息。

### 6.7 进群申请（Join Requests）

开启 `group set-join-request --enabled` 后，用这组命令审批：

```sh
telegram-native join-requests list @ops
telegram-native join-requests approve @ops --users @alice,@bob --execute
telegram-native join-requests reject @ops --users @spammer --confirm @spammer --execute
```

`list` 返回申请人、申请时间、申请留言（如有）、是否仍 pending。`approve` 会把人
加入群/频道；`reject` 是破坏性操作，需要 `--yes` 或 `--confirm`。

### 6.8 消息

```sh
telegram-native messages list @ops --limit 30
telegram-native messages list @ops --topic 123 --limit 50
telegram-native messages list @ops --search keyword
telegram-native messages search @ops --query "release"
telegram-native messages get @ops --id 456

telegram-native messages send @ops --text "hello" --execute
telegram-native messages send @ops --text-file /tmp/body.md --execute
telegram-native messages send @ops --file /tmp/report.pdf --text "Q3" --execute
telegram-native messages send @ops --topic 123 --text "in topic" --execute
telegram-native messages send @ops --reply-to 456 --text "reply" --silent --execute

telegram-native messages edit @ops --id 456 --text "fixed" --execute
telegram-native messages delete @ops --ids 456,457 --confirm 456,457 --execute
telegram-native messages delete @ops --ids 456 --keep-for-me --yes --execute
telegram-native messages pin @ops --id 456 --execute
telegram-native messages unpin @ops --id 456 --execute
telegram-native messages mark-read @ops --execute
```

- 文本上限 4096 字符；文件 caption 上限 1024。
- 默认不解析 Markdown，避免把 `*`/`_` 当格式。
- 频道发消息需要 `post_messages` 权限；群需要未被禁言。
- 删除/编辑通常只对本人消息或管理员有效；被拒绝时返回 `REJECTED`/`PERMISSION_DENIED`。

### 6.9 私聊与 Bot 会话

私聊就是 `chats list --type private` / `--type bot` 里的会话，读写方式相同：

```sh
telegram-native chats list --type private
telegram-native messages list @alice --limit 20
telegram-native messages send @alice --text "hi" --execute
telegram-native chat mute @alice --seconds 3600 --execute
telegram-native chat archive @alice --execute
telegram-native chat block @alice --yes --execute
telegram-native chat unblock @alice --execute
```

`chat block` 只对私聊/ Bot 有效；群/频道请用 `members ban` 或 `chat leave`。

### 6.10 BotFather：创建/编辑/管理 Bot

BotFather 是普通用户会话里的一个 Bot，创建 Bot 必须用**用户账号**（Bot API 不能
创建 Bot）。本 CLI 通过消息和 inline keyboard 驱动它。

```sh
telegram-native bot list
telegram-native bot create --name "My Helper" --username my_helper_bot --execute
telegram-native bot token --username my_helper_bot --execute
telegram-native bot token --username my_helper_bot --show-secret --execute

telegram-native bot set-name --username my_helper_bot --name "My Helper 2" --execute
telegram-native bot set-about --username my_helper_bot --about "short about" --execute
telegram-native bot set-description --username my_helper_bot --description "long description" --execute
telegram-native bot set-commands --username my_helper_bot --commands-file commands.txt --execute
telegram-native bot revoke --username my_helper_bot --yes --execute
telegram-native bot delete --username my_helper_bot --confirm my_helper_bot --execute
```

`commands.txt` 每行：

```
start - Start the bot
help - Show help
```

限制与注意：

- 每个账号最多 20 个 Bot（Telegram 限制）。
- Bot username 必须全局唯一、以 `bot` 结尾（不区分大小写），长度 5–32，只允许
  字母/数字/下划线。
- 2026-10-24 已在本机真实账号上验证 `bot list` 和 `bot token`（masked）可用；
  `bot create`/`revoke`/`delete` 仍需用一次性 Bot 做真机验收。
- `bot token` 不是绝对只读：BotFather 的 `/token` 流程在新版本中可能顺带轮换 token。
  因此它要求 `--execute`，返回 `token_rotated`（true/null）和 `rotation_note`；
  超时按 `OUTCOME_UNKNOWN` 处理，不要假定旧 token 仍有效。
- `bot set-about` 使用 BotFather 的 `/setabouttext` 命令（不是 `/setabout`）。
- `bot delete` 兼容两种确认：inline 按钮，或 BotFather 要求发送
  `Yes, I am totally sure.` 的文本确认（2026-10-24 实测为后者）。
- token 是密码。默认只返回掩码，完整 token 存在
  `~/.local/share/telegram-native/bots/<username>.json`（0600）；只有显式
  `--show-secret` 才打印完整值。
- BotFather 的提示文案/按钮可能随 Telegram 更新变化；CLI 用大小写不敏感的子串
  匹配，失败时把可用按钮列在 `error.details`。
- 创建/删除/撤销都是真实账号操作，必须有明确授权；不要用自动化测试去点。

### 6.11 本地辅助

```sh
telegram-native config show
telegram-native docs
telegram-native docs --full
telegram-native commands
```

### 6.12 第二批 P1 能力：迁移、邀请链接、媒体、搜索/联系人、账号设备

#### 基础群 → 超级群

```sh
telegram-native group migrate -123456789 --yes --execute
```

- 只对 basic group 有效；不可逆，旧 peer id 会失效。
- 成功后 `data.result.new` 是新超级群的 `peer_id`；后续命令用它。
- 普通群缺话题/公开用户名/慢速模式/精细权限，需要这些能力时先迁移。

#### 邀请链接全生命周期

```sh
telegram-native invite list @ops
telegram-native invite list @ops --revoked
telegram-native invite edit @ops --link https://t.me/+AbCd --expire-days 7 --execute
telegram-native invite edit @ops --link https://t.me/+AbCd --no-expire --execute
telegram-native invite edit @ops --link https://t.me/+AbCd --usage-limit 20 --execute
telegram-native invite edit @ops --link https://t.me/+AbCd --request-needed --execute
telegram-native invite importers @ops --link https://t.me/+AbCd
telegram-native invite importers @ops --link https://t.me/+AbCd --pending
telegram-native invite delete @ops --link https://t.me/+AbCd --yes --execute
```

- 支持 `t.me/+...`、`+...`、`tg://join?invite=...`，内部统一成 `https://t.me/+...`。
- `invite delete` 会先 revoke 再 delete；Telegram 只允许删除 revoked 集合里的链接。
- 审批链接（`request_needed`）不能同时设置 `usage_limit`；Telegram 会返回
  `UsageLimitInvalid`，CLI 本地先拦。
- Telethon 层无法把已有 `usage_limit` 清零；需要清零请新建链接并删除旧链接。
- 永久链接的过期/次数语义受 Telegram 限制；以 `invite list` 读回为准。

#### 媒体下载 / 转发 / 相册

```sh
telegram-native messages send-album @ops --files /tmp/a.jpg,/tmp/b.jpg --caption "Q3" --execute
telegram-native messages download @ops --id 123
telegram-native messages download @ops --id 123 --output /tmp/out/
telegram-native messages forward --from @ops --to me --ids 123,124 --execute
telegram-native messages forward --from @ops --to @target --ids 123 --silent --execute
```

- 相册 1–10 个文件；caption ≤1024 UTF-16；单文件 ≤2 GB。
- 下载默认写 `~/.local/share/telegram-native/downloads/<chat>/`，目录 0700、文件 0600。
- 转发保留相册分组，不需要额外 `--as-album`；`me` 是 Saved Messages。

#### 全局搜索与联系人

```sh
telegram-native search global --query "release" --limit 50
telegram-native search global --query "release" --type group
telegram-native contacts list
telegram-native contacts search --query alice
telegram-native contacts resolve --username @alice
telegram-native contacts resolve --phone +8613800000000
telegram-native contacts add --phone +8613800000000 --first-name Alice --last-name Chen --execute
telegram-native contacts delete --users @alice --execute
```

- `search global` 是服务端跨会话搜索；`--type private|group|channel` 过滤。
- `contacts add` 通过手机号导入联系人；会真实写入账号联系人列表。
- `contacts resolve` 只读；按手机号解析不一定能拿到非联系人的隐私信息。

#### 账号资料与设备管理

```sh
telegram-native account profile
telegram-native account set-name --first-name Alex --last-name Doe --execute
telegram-native account set-bio --about "AI automation" --execute
telegram-native account set-username --username my_name --execute
telegram-native account set-photo --photo /absolute/avatar.png --execute
telegram-native account devices list
telegram-native account devices revoke --hash 123456789 --yes --execute
telegram-native account devices revoke-others --yes --execute
telegram-native account web-sessions list
telegram-native account web-sessions revoke --hash 123456789 --yes --execute
telegram-native account web-sessions revoke-all --yes --execute
```

- `account devices revoke` 拒绝撤销当前 CLI 会话（用 `auth logout --execute`）。
- `devices revoke-others` 会撤销除当前 CLI 外的所有设备，破坏性，必须 `--yes`。
- `set-name/set-bio/set-username/set-photo` 会真实修改账号资料；默认只预览。
- 设备列表包含 IP/地区等私密信息；不要提交或上传输出。

#### 频道评论、默认权限、管理员日志、话题细粒度

```sh
# 频道帖子的评论区
telegram-native comments info @channel --post-id 123
telegram-native comments list @channel --post-id 123 --limit 50
telegram-native comments reply @channel --post-id 123 --text "comment" --execute

# 默认成员权限（替换式）
telegram-native chat set-permissions @group --preset open --execute
telegram-native chat set-permissions @group --preset readonly --execute
telegram-native chat set-permissions @group --preset media-only --execute
telegram-native chat set-permissions @group --restrict send_plain,send_polls --execute

# 管理员日志
telegram-native admin-log list @group --limit 50
telegram-native admin-log list @group --query "title" --full

# 话题细粒度
telegram-native topic read @group --topic-id 3
telegram-native topic hide @group --topic-id 1 --hide
telegram-native topic hide @group --topic-id 1 --show
telegram-native topic pin @group --topic-id 3
telegram-native topic reorder @group --topic-ids 3,1
telegram-native forum set-tabs @group --enabled
telegram-native forum set-tabs @group --disabled

# 批量进群审批
telegram-native join-requests approve-all @group --yes --execute
telegram-native join-requests reject-all @group --yes --execute
telegram-native join-requests approve-all @group --link https://t.me/+AbCd --yes --execute
```

- 评论区要求频道已关联讨论群；`comments reply` 实际发到讨论群的话题线程。
- `chat set-permissions` 是**替换式**：`open` 清空限制，`readonly` 禁止发送，
  `media-only` 只禁文字；`--restrict` 从“无限制”开始只打开列出的限制字段。
- `admin-log` 需要管理员；`--full` 会输出更大 action 对象。
- `topic hide` 主要针对 General（id=1）；`topic reorder` 只影响置顶话题顺序。
- `approve-all/reject-all` 是批量操作，必须 `--yes`；可按链接限定。

#### 代理与多账号

```sh
# SOCKS5 / SOCKS4 / HTTP 代理
TG_PROXY=socks5://127.0.0.1:7890 telegram-native auth status
TG_PROXY=socks5://user:pass@host:1080 telegram-native chats list

# 命名账号（独立 session）
telegram-native --account work auth status
telegram-native --account work auth qr
telegram-native --account work chats list
telegram-native config accounts list
telegram-native config accounts remove --name work --execute
```

- 代理也可写到 `~/.config/telegram-native/config.json` 的 `"proxy"`。
- `--account NAME` 使用 `accounts/<name>.session`；授权成功后写入
  `~/.local/share/telegram-native/accounts.json` 注册表。
- `config accounts remove` 只移除注册表条目，不删除 session 文件。
- 同一个 session 不要并发跑多个写命令；CLI 没有跨进程会话锁。

## 7. Agent 常用配方

### 7.1 建一个带话题的群并初始化

```sh
telegram-native group create --title "Ops" --forum --execute
telegram-native topic create @ops --title "General" --execute
telegram-native topic create @ops --title "Release" --icon-color 0x6FB9F0 --execute
telegram-native members add @ops --users @alice,@bob --execute
telegram-native admins promote @ops --users @alice --rights invite_users,pin_messages,manage_topics --execute
```

### 7.2 给频道建邀请链接并发出

```sh
telegram-native channel create --title "Announcements" --username my_ann --execute
telegram-native invite link @my_ann --usage-limit 100 --execute
telegram-native messages send @my_ann --text "Welcome" --execute
```

### 7.3 创建一个 Bot 并配置命令

```sh
telegram-native bot create --name "Support" --username my_support_bot --execute
telegram-native bot set-commands --username my_support_bot --commands-file commands.txt --execute
telegram-native bot token --username my_support_bot --show-secret --execute
```

### 7.4 判断目标类型再决定命令

```sh
telegram-native chat info @target
# data.chat.kind: private|bot|group|supergroup|channel
# data.chat.is_forum: true/false
```

## 8. 运行条件与限制

- macOS / Linux，Python 3.12+（macOS 上用 3.15 实测；CI 在 Ubuntu 跑 3.12/3.13 离线测试；Windows 未支持）；依赖 Telethon 1.45、qrcode、Pillow、
  python-socks[asyncio]。
- 直连 Telegram 已验证；需要代理时用 `TG_PROXY`，支持：
  `socks5://user:pass@host:port`、`socks4://...`、`http://...`。
  也可写进 `~/.config/telegram-native/config.json` 的 `"proxy"`。
- 多账号：`--account NAME` 使用 `~/.local/share/telegram-native/accounts/<name>.session`；
  `auth qr --account work` 授权后会写入 `accounts.json` 注册表；
  `config accounts list` 查看，`config accounts remove --name work --execute` 只移除注册表。
- 一个 CLI 会话 = 一个 Telegram 登录设备；可在手机/桌面端 Devices 里撤销。
- 本 CLI 不读取 Telegram App 的本地数据库，所以**看不到**只在本地存在的草稿、
  某些缓存和端到端加密的 Secret Chat 内容；服务端群/频道/私聊消息不受影响。
- Telegram 的 API 权限、慢速模式、FloodWait、Bot 数量限制仍然生效。
- 公开 api_id 可能被 Telegram 限制；可用 `TG_API_ID`/`TG_API_HASH` 换成自己的。
- 不是官方 Telegram 客户端；使用风险由账号所有者承担。

## 9. 验证清单

安装后按顺序做：

```sh
telegram-native --version
telegram-native commands
telegram-native config show
telegram-native auth status                 # 未登录也应 ok=true, authorized=false
telegram-native auth qr                     # 人工扫码一次
telegram-native auth status                 # authorized=true
telegram-native chats list --limit 10        # 只读
telegram-native chat info @some_public_group # 只读
```

写操作验收建议用测试群/测试频道，先 preview，再 `--execute`，最后用
`chats list` / `chat info` / `messages list` 读回。不要在验收中给真实用户发消息。

## 10. 故障排查

| 现象 | 处理 |
|---|---|
| `CONFIRMATION_REQUIRED` + `authorized:false` | 先 `auth qr` 或 `auth login` |
| `THROTTLED` | 看 `details.retry_after`，等够再试；不要自动循环 |
| `PERMISSION_DENIED` | 确认当前账号是群主/管理员，且机器人/频道有对应权限 |
| `TARGET_AMBIGUOUS` | 用 `details.candidates[].peer_id` 或 `@username` 重新指定 |
| `UNSUPPORTED`（话题/封禁/用户名） | 目标可能是普通群；先转超级群或换目标 |
| 扫码后没反应 | 二维码过期；重新运行 `auth qr --wait 300`，手机保持前台 |
| 2FA 提示 | 在终端输入两步验证密码（不会回显/入库） |
| 会话被撤销 | `auth status` 会显示未授权；重新扫码 |
| 写操作 `OUTCOME_UNKNOWN` | 先看 Telegram 实际状态和 receipt；不要用同 id 重试 |
