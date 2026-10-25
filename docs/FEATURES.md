# telegram-native 功能矩阵（Agent-first）

> 目标：给 Agent 一个**可发现、可预检、可幂等、可审计**的 Telegram CLI。
> 本文按所有者明确要求的高频场景排序：**群/频道管理、Forum Topics、BotFather
> Bot 生命周期、私聊**优先（P0），工作流闭环所需能力次之（P1），低频/高风险/
> 非目标能力明确跳过（P2）。
>
> 快照：2026-10-24 08:03 ET（验收结果 08:27 ET 追加），项目
> `telegram-native/`，Telethon 1.45.0（TL layer 229）。BotFather 的
> create/token/set-*/revoke/delete 已用一次性 Bot 真机验证；命令与行为以
> `--help` 和
> [`USAGE.md`](../USAGE.md) 为准；API 细节与文件行号见
> [`API-NOTES.md`](./API-NOTES.md)。

## 0. 图例与判断标准

| 标记 | 含义 |
|---|---|
| ✅ | 已实现，离线测试覆盖，读写路径清晰 |
| ⚠️ | 已实现，但有 API 限制、已知缺口或未做真机验证 |
| 🚧 | 已接入但只做过离线测试，上线前必须小号/测试群验证 |
| 🧪 | 2026-10-24 已在所有者授权下完成真机只读/预检验证；写执行仍需测试群 |
| ❌ | 未实现，建议列入下一阶段 |
| ⏭ | 明确不做（P2 或非目标） |

优先级：

- **P0**：所有者明确要的高频操作；没有它 Agent 无法完成主工作流。
- **P1**：P0 工作流的必要闭环，或高频到值得尽快补的能力。
- **P2**：低频、纯展示、高风险账号操作，或与 Agent 场景无关。

判断一行功能是否 P0，看三个问题：是否每天用、是否阻塞建群/管群/发消息、
失败后是否会造成不可逆后果。

### 0.1 真机验证状态（2026-10-24，只读/预览）

所有者已授权一次只读/预览验证，**没有对真实群/频道执行写操作**（除了
`chat mute/unmute` 的幂等回放验证）：

- 已验证：`auth status`、`chats list`、`chat info`（公开频道 + 论坛群）、
  `messages list/search`（含 `--topic`）、`topic list`、`members list`、
  `admins list`、`invite info`、`join-requests list`、`bot list`、
  `bot token`（masked，且发生在最新 rotation 语义改动之前）。
- 已验证执行：`chat mute` / `chat unmute`，并验证同一 request-id 重放为 `noop`。
- 已验证执行：临时论坛群、临时频道、一次性 Bot 的完整写入链路（建群/建频道、改资料、
  邀请/踢人/封禁、管理员变更、话题写操作、邀请链接、消息发送/编辑/置顶/删除、
  BotFather create/token/set-*/revoke/delete），测试后已全部删除。
- BotFather 写命令（create/token/set-name/set-about/set-description/set-commands/revoke/delete）已用一次性 Bot 真机验证；`/deletebot` 当前要求发送 `Yes, I am totally sure.`，代码已兼容。
- `bot token` 的 rotation 语义仍可能返回 `token_rotated: null`；不要把“取 token”当绝对只读。
- 运行环境实际是 Python **3.15**（`.venv`）；USAGE 已注明“3.12+ / 本机 3.15”。

## 1. P0：已实现的主工作流

### 1.1 授权与会话（auth/session）

| 能力 | 命令 | 状态 | 权限/限制 | 风险与备注 |
|---|---|---|---|---|
| 扫码授权 | `auth qr [--wait 180] [--save PATH]` | ✅ | 需手机已登录同账号 | 独立设备会话；二维码会打印到 stderr，PNG 0600，成功后删除 |
| 手机号 + 验证码 | `auth login [--phone +86...]` | ✅ | 验证码从 stdin 读 | 交互式；验证码不写入参数/环境/日志 |
| 两步验证 | `auth password [--password-stdin]` | 🧪 | 需要 pending 2FA 登录 | 已真机验证：进程重启后仍可完成；失败时提示重跑 `auth qr` |
| 状态 | `auth status` | 🧪 | 无 | 未登录也返回 `ok=true, authorized=false`，适合 Agent 探测 |
| 撤销会话 | `auth logout --execute` | ✅ | 无 | 撤销当前 CLI 设备；会删除本地 session 文件 |
| 多账号/多 session | 全局 `--session PATH` | ⚠️ | 每个文件一个登录 | 没有账号注册表；Agent 需自己管理路径，避免并发写同一 session |

### 1.2 私聊（private chats）

| 能力 | 命令 | 状态 | 权限/限制 | 风险与备注 |
|---|---|---|---|---|
| 会话发现 | `chats list --type private` | 🧪 | 无 | 可按标题/用户名过滤；`--with-last` 附带最后一条摘要 |
| 用户/Bot 信息 | `chat info @user` | 🧪 | 无 | 返回 about、common chats、bot commands 等 |
| 读历史 | `messages list @user --limit 50` | 🧪 | 无 | 支持 offset、搜索、`--reverse`、正文截断 |
| 搜索 | `messages search @user --query ...` | 🧪 | 无 | 服务端搜索；私聊内 `from_user` 无意义 |
| 单条读取 | `messages get @user --id 123` | ✅ | 无 | 不存在返回 `NOT_FOUND` |
| 发文本 | `messages send @user --text "..." --execute` | ✅ | 对方隐私设置允许 | 默认 Markdown 关闭，避免误解析 |
| 发文件 | `messages send @user --file /path --text "caption" --execute` | ✅ | 文件大小/类型限制 | caption ≤1024 字符；大文件未见本地大小校验 |
| 回复 | `messages send @user --reply-to 123 --text ...` | ✅ | 无 | 私聊普通回复 |
| 编辑 | `messages edit @user --id 123 --text ...` | ✅ | 仅自己消息 | 不支持改媒体 caption |
| 删除 | `messages delete @user --ids 1,2 --keep-for-me --yes --execute` | ⚠️ | `--keep-for-me` 仅本地；否则尝试为双方删除 | 对他人消息的“为双方删除”需要权限，私聊通常不可用 |
| 置顶/取消 | `messages pin|unpin @user --id 123` | ✅ | 私聊可置顶 | 频道/群需 `pin_messages` |
| 标记已读 | `messages mark-read @user [--max-id N]` | ✅ | 无 | 只影响当前账号 |
| 免打扰 | `chat mute @user --seconds 3600` | 🧪 | 无 | 0 表示长期；`unmute` 取消；真机 executed + 幂等回放已验证 |
| 归档/置顶会话 | `chat archive|unarchive|pin|unpin @user` | ✅ | 无 | 只改自己的会话列表 |
| 拉黑/解除 | `chat block|unblock @user --yes --execute` | ✅ | 私聊/Bot | 群/频道请用 `members ban` 或 `chat leave` |
| 删除会话 | `chat delete-dialog @user [--revoke]` | ✅ | `--revoke` 可能清对方历史 | 破坏性，需要 `--yes/--confirm` |
| 联系人管理 | `contacts add/search/delete` | ❌ | — | P1：Agent 需要按手机号/用户名找人、维护联系人 |
| 转发/相册/媒体下载 | — | ❌ | — | P1：见 §3 |
| Secret Chat | — | ⏭ | — | MTProto E2E，CLI 明确不覆盖 |

### 1.3 群组（basic group 与 supergroup 创建）

| 能力 | 命令 | 状态 | 权限/限制 | 风险与备注 |
|---|---|---|---|---|
| 建普通群 | `group create --title T --basic --users @a,@b --execute` | ✅ | 至少 1 个用户；上限约 200 人 | 普通群没有公开用户名/话题/精细权限 |
| 建超级群 | `group create --title T --users @a,@b --execute` | ✅ | 无 | 默认 `megagroup=True`，这是推荐形态 |
| 建带话题超级群 | `group create --title T --forum --execute` | ✅ | 创建者 | 等价于 `CreateChannel(megagroup=True, forum=True)`；也可建后 `forum enable` |
| 创建时设用户名/头像 | `--username` / `--photo` | ⚠️ | 仅超级群/频道 | 失败不回滚，返回 `warnings`；`--basic` 时这两项会明确写入 `warnings` 并忽略 |
| 删除超级群/频道 | `group delete @g --confirm g --execute` | ✅ | 仅创建者 | 删除对所有人不可逆；普通群不能用此命令 |
| 删除普通群 | — | ❌ | — | API 没有 DeleteChat：只能 `chat leave`，最后一人退出后群消失；CLI 会明确拒绝 |
| 改标题/简介/头像 | `chat set-title|set-about|set-photo` | ✅ | `change_info` 管理员或创建者 | 标题 ≤128 字符；简介 ≤255；头像 ≤10 MB（Bot API 口径） |
| 设公开用户名 | `chat set-username @g --username ops` | ✅ | 仅超级群/频道；`change_info`/owner | 普通群必须先迁移；空字符串表示清除 |
| 慢速模式 | `chat set-slowmode @g --seconds 30` | ✅ | 管理员 | 仅超级群；允许 0/5/10/30/60/300/900/3600 秒 |
| 进群需批准 | `group set-join-request @g --enabled [--apply-to-invites]` | ⚠️ | 管理员 | 仅超级群；开启后**没有审批命令**，见 §3 缺口 |
| 先加入才能发言 | `group set-join-to-send @g --enabled` | ✅ | 管理员 | 仅超级群 |
| 退出群/频道 | `chat leave @g --confirm g --execute` | ✅ | 创建者不能直接退出频道 | 频道创建者需先转让/删除 |
| 基础群→超级群迁移 | — | ❌ | — | P1 高优：`messages.migrateChat`；很多能力只有超级群才有 |
| 默认成员权限 | — | ❌ | — | P1：`messages.editChatDefaultBannedRights` |
| 管理员日志 | — | ❌ | — | P2：`channels.getAdminLog` |

### 1.4 超级群 / Forum Topics

| 能力 | 命令 | 状态 | 权限/限制 | 风险与备注 |
|---|---|---|---|---|
| 查看话题开关 | `forum status @g` | ✅ | 无 | 普通群返回 `forum=false, reason=basic_group` |
| 开启/关闭话题 | `forum enable|disable @g --execute` | ✅ | **仅群主（owner）** | 关闭会隐藏所有话题；已关联讨论群的超级群不能开启话题 |
| 话题列表 | `topic list @g [--all] [--search Q] [--offset-topic N]` | 🧪 | 无 | 默认按最近活动排序；`--all` 自动翻页；服务端可能返回少于 limit |
| 创建话题 | `topic create @g --title "Release" [--icon-color 0x6FB9F0] [--icon-emoji-id ID]` | ✅ | `manage_topics` | 标题 ≤128 UTF-8 字节；icon_color 只能是六种官方 RGB；自定义 emoji 受 Premium/默认表情包限制 |
| 编辑话题 | `topic edit @g --topic-id N [--title T] [--icon-emoji-id ID]` | ✅ | `manage_topics` | **API 不支持编辑 icon_color**；`closed` 不能与其他字段同请求（CLI 用单独命令） |
| 关闭/重开 | `topic close|reopen @g --topic-id N` | ✅ | `manage_topics` | 关闭后成员不能发消息 |
| 置顶/取消 | `topic pin|unpin @g --topic-id N` | ✅ | `manage_topics` | 置顶数量受服务端 `topics_pinned_limit` 限制 |
| 删除话题 | `topic delete @g --topic-id N --confirm N --execute` | ✅ | `manage_topics` | 删除该话题全部消息；**General（id=1）不可删除** |
| 话题内发消息 | `messages send @g --topic N --text ...` | ⚠️ | 未被禁止发言 | **General（topic=1）应不带 reply_to**；非 General 用 topic id 作为 reply_to；话题内回复某条消息还需要 `top_msg_id`，当前 helper 不能完整表达 |
| 话题已读状态 | — | ❌ | — | P1：`messages.readDiscussion`/按 topic 的 read max |
| 隐藏 General / 重排置顶 / 切换到 tabs UI | — | ❌ | — | P2：`EditForumTopic(hidden)`, `ReorderPinnedForumTopics`, `ToggleForum(tabs)` |
| 短话题 / monoforum | — | ⏭ | — | 与 Agent 主场景无关 |

### 1.5 频道（broadcast channel）

| 能力 | 命令 | 状态 | 权限/限制 | 风险与备注 |
|---|---|---|---|---|
| 建频道 | `channel create --title T [--username u] [--users @a] --execute` | ✅ | 无；受 spam 限制 | 创建者为 owner；`about` 可空 |
| 删除频道 | `channel delete @c --confirm c --execute` | ✅ | 仅创建者 | 不可逆 |
| 发帖 | `messages send @c --text ... --execute` | ✅ | 管理员需 `post_messages` | 默认管理员权限现在会为频道加 `post_messages` |
| 资料 | `chat set-title|set-about|set-photo|set-username` | ✅ | `change_info`/owner | 与超级群共用 |
| 署名 | `chat set-signatures @c --enabled|--disabled` | ⚠️ | 频道管理员 | API 还有 `profiles_enabled`（管理员资料链接），CLI 未暴露 |
| 关联讨论群 | `chat set-discussion @c --group @g` | ⚠️ | 频道+群管理员 | 只能设置，不能取消；讨论群需超级群且未隐藏历史 |
| 读/发评论 | — | ❌ | — | P1：`messages.getDiscussionMessage` + `getReplies` + `comment_to` |
| 频道统计/管理员日志 | — | ❌ | — | P2 |
| 所有权转让 | — | ❌ | — | P2；频道 owner 退出前需要 |

### 1.6 成员 / 管理员 / 邀请

| 能力 | 命令 | 状态 | 权限/限制 | 风险与备注 |
|---|---|---|---|---|
| 成员列表 | `members list @g [--filter all|admins|bots|recent|kicked] [--search Q]` | 🧪 | 部分群隐藏成员列表 | 普通群的 `recent/kicked` 已改为明确 UNSUPPORTED |
| 邀请成员 | `members add @g --users @a,@b --execute` | ✅ | `invite_users`/普通群成员 | 普通群逐个 `AddChatUser(fwd_limit=10)`；频道/超级群 `InviteToChannel` |
| 移出成员 | `members remove @g --users @a --yes --execute` | ✅ | `ban_users`（超级群） | 超级群=踢出（可再进）；普通群=DeleteChatUser |
| 封禁 | `members ban @g --users @a [--days N] --execute` | ✅ | `ban_users` | 仅超级群/频道；不填 days 为永久 |
| 解封 | `members unban @g --users @a --execute` | ✅ | `ban_users` | 仅超级群/频道 |
| 管理员列表 | `admins list @g` | 🧪 | 无 | 普通群/超级群/频道均支持 |
| 设管理员/改权限 | `admins promote @g --users @a [--rights ...] [--rank ...]` | ✅ | `add_admins`；不能授予超出自己的权限 | 普通群只能 `is_admin=True`，`--rights/--rank` 会被拒绝；频道默认含 `post_messages` |
| 撤销管理员 | `admins demote @g --users @a --yes --execute` | ✅ | `add_admins`/owner | 普通群用 `EditChatAdmin(is_admin=False)` |
| 创建邀请链接 | `invite link @g [--expire-days N] [--usage-limit N] [--request-needed] [--title T]` | ⚠️ | `invite_users` | 每次调用会新建一条链接；默认永久链接不会自动复用 |
| 撤销邀请链接 | `invite revoke @g --link URL --confirm URL --execute` | ⚠️ | `invite_users` | 只 revoke，不删除；永久链接不能设置过期时间；短链形式应先规范化 |
| 查看邀请链接 | `invite info --link URL/@name` | 🧪 | 无 | 私有链接用 `CheckChatInvite`；公开用户名直接 resolve |
| 加入群/频道 | `invite join --link URL --execute` | ⚠️ | 无；受频道数/隐私限制 | 私有 hash 用 `ImportChatInvite`；公开用 `JoinChannel`；需批准时返回 `request_sent=true`（公开/私有分支都已捕获） |
| 进群申请列表 | `join-requests list @g [--search Q] [--limit N]` | 🧪 | `invite_users` | 真机只读已验证；新模块无离线测试；已按 offset_date/offset_user 分页 |
| 批准/拒绝申请 | `join-requests approve|reject @g --users @a,@b --execute` | ⚠️ | `invite_users` | 新模块、无离线测试；逐用户 `HideChatJoinRequest`；reject 需破坏性确认；部分失败会中断 |
| 邀请链接列表/编辑/删除/导入者 | — | ❌ | — | P1：`GetExportedChatInvites`, `EditExportedChatInvite`, `DeleteExportedChatInvite`, `GetChatInviteImporters` |
| 转让群/频道所有权 | — | ❌ | — | P2 |

### 1.7 消息与媒体

| 能力 | 命令 | 状态 | 权限/限制 | 风险与备注 |
|---|---|---|---|---|
| 发文本 | `messages send CHAT --text T [--silent] [--no-link-preview]` | ✅ | 未被禁言/频道 `post_messages` | 文本 ≤4096；Markdown 默认关闭 |
| 发文件 | `messages send CHAT --file PATH [--as-document] [--text caption]` | ✅ | 同上 | caption ≤1024；未校验 2 GB/4 GB 上限 |
| 回复 | `--reply-to ID` | ✅ | 无 | 普通回复 |
| 话题发送 | `--topic ID` | ⚠️ | `manage_topics` 不必要，未被禁言即可 | General=1 需特殊处理；话题内回复需 `top_msg_id` |
| 编辑文本 | `messages edit CHAT --id N --text T` | ✅ | 自己消息/`edit_messages` | 不能改媒体 caption |
| 删除 | `messages delete CHAT --ids ... [--keep-for-me]` | ✅ | 自己消息/`delete_messages` | 默认尝试为双方删除；`--keep-for-me` 只删本地 |
| 置顶/取消 | `messages pin|unpin CHAT --id N [--notify]` | ✅ | `pin_messages` | 频道/群常用 |
| 搜索（单会话） | `messages search CHAT --query Q [--topic N]` | ✅ | 无 | 非 topic 用 `messages.search`；topic 用 `SearchRequest(top_msg_id=...)` |
| 历史搜索 | `messages list CHAT --search Q` | ⚠️ | 无 | 与 `--topic` 同时使用时 `getReplies` 会忽略 search；应改用 SearchRequest |
| 标记已读 | `messages mark-read CHAT [--max-id N]` | ✅ | 无 | 不支持按 topic 精确已读 |
| 转发 | — | ❌ | — | P1：`ForwardMessages`，Agent 高频 |
| 相册/多文件 | — | ❌ | — | P1：`SendMultiMedia` |
| 媒体下载 | — | ❌ | — | P1：Agent 需要读图片/文件内容 |
| 定时消息/投票/表情回应/按钮 | — | ❌/⏭ | — | P2；投票、按钮对 Agent 价值低 |

### 1.8 BotFather / Bot 生命周期

> **状态：✅ 已实现；2026-10-24 已用一次性 Bot 完成 create/set/token/revoke/delete 真机验收。**
> BotFather 是普通用户会话里的对话式 Bot，Bot API 不能创建 Bot；实现通过
> `send_message` + 轮询 `get_messages(min_id=...)` + 点击 inline button 驱动。
> 详细流程见 [`BOTFATHER.md`](./BOTFATHER.md)。

| 能力 | 命令 | 状态 | 权限/限制 | 风险与备注 |
|---|---|---|---|---|
| 创建 Bot | `bot create --name N --username u_bot --execute` | ✅ | 用户账号；每账号 20 个（Premium 40） | `/newbot` 先问 name 再问 username；username 全局唯一、5–32、`bot` 结尾、不可改 |
| 列出 Bot | `bot list` | 🧪 | 无 | 真机只读已验证；`/mybots` 的 inline buttons 解析 |
| 取 token | `bot token --username u_bot [--show-secret] --execute` | ✅ | 无 | **masked token 已真机验证（旧语义）**；新 rotation 语义仍需一次性 Bot 复测；结果含 `token_rotated`/`rotation_note` |
| 改显示名 | `bot set-name --username u_bot --name N` | ✅ | 无 | ≤64 字符 |
| 改 about | `bot set-about --username u_bot --about T` | ✅ | 无 | ≤120 字符；已改用官方 `/setabouttext`，待真机验证 prompt |
| 改 description | `bot set-description --username u_bot --description T` | ✅ | 无 | ≤512 字符 |
| 设置命令 | `bot set-commands --username u_bot --commands-file FILE` | ✅ | 无 | 每行 `command - description`；命令 1–32（小写字母/数字/下划线），描述 ≤256，最多 100 条 |
| 撤销 token | `bot revoke --username u_bot --yes --execute` | ✅ | 无 | 旧 token 立即失效；实现同时兼容“直接返回新 token”和“确认按钮”两种形态，结果含 `confirmation_required` |
| 删除 Bot | `bot delete --username u_bot --confirm u_bot --execute` | ✅ | 无 | 释放 username；不可恢复 |
| 转让 Bot / 头像 / 域名 / inline/privacy 设置 | — | ❌ | — | P2；可后续补 `/setuserpic`、`/setdomain`、`/setinline`、`/setprivacy` |

### 1.9 第二轮 P1：迁移 / 邀请链接 / 媒体 / 搜索联系人 / 账号设备

| 能力 | 命令 | 状态 | 限制/风险 |
|---|---|---|---|
| 基础群迁移 | `group migrate CHAT --yes --execute` | ✅ 真机验证 | 不可逆；旧 peer id 失效，返回 `new.peer_id` |
| 邀请链接列表 | `invite list CHAT [--revoked]` | ✅ 真机验证 | 分页；返回永久/审批/过期/使用次数 |
| 邀请链接编辑 | `invite edit CHAT --link URL ...` | ✅ 真机验证 | 审批链接不能同时设 usage_limit；usage_limit 不能清零 |
| 邀请链接删除 | `invite delete CHAT --link URL --yes` | ✅ 真机验证 | 先 revoke 再 delete；永久/已删链接的语义以读回为准 |
| 邀请导入者 | `invite importers CHAT --link URL [--pending]` | ✅ 真机验证（空列表） | 分页；需要 `invite_users` 权限 |
| 相册发送 | `messages send-album CHAT --files a,b` | ✅ 真机验证 | 1–10 个文件；caption ≤1024 |
| 媒体下载 | `messages download CHAT --id N` | ✅ 真机验证 | 默认写私有 downloads/；文件 0600 |
| 媒体转发 | `messages forward --from A --to B --ids ...` | ✅ 真机验证 | 保留相册分组；目标需有发送权限 |
| 全局搜索 | `search global --query Q [--type ...]` | ✅ 真机验证 | 服务端搜索；只搜消息，不搜文件内容 |
| 联系人列表/搜索/解析 | `contacts list/search/resolve` | ✅ 真机验证 | 手机号解析受隐私设置限制 |
| 联系人添加/删除 | `contacts add/delete` | ⚠️ preview 验证 | 会真实写联系人；按手机号导入 |
| 账号资料 | `account profile` / `set-name|set-bio|set-username|set-photo` | ⚠️ 读真机，写 preview | 修改影响全账号 |
| 登录设备 | `account devices list/revoke/revoke-others` | ⚠️ 列表真机，撤销未执行 | 撤销他人设备会强制下线；当前 CLI 设备被拒绝 |
| 网页会话 | `account web-sessions list/revoke/revoke-all` | ⚠️ 列表真机（0 条） | 撤销会强制网页端下线 |
| 频道评论 | `comments info/list/reply` | ✅ 真机验证 | 需频道已关联讨论群；reply 发到讨论群 |
| 默认成员权限 | `chat set-permissions --preset/--restrict` | ✅ 真机验证 | 替换式；普通群不支持 |
| 管理员日志 | `admin-log list [--query] [--full]` | ✅ 真机验证 | 需管理员；默认只输出 action 摘要 |
| 话题细粒度 | `topic read/hide/reorder`, `forum set-tabs` | ✅ 真机验证 | hide 主要对 General；reorder 只影响置顶 |
| 批量审批 | `join-requests approve-all/reject-all` | ⚠️ preview | 执行会批量放人/拒人，需 `--yes` |
| 代理 | `TG_PROXY=socks5://...` | ✅ 真机验证 | 支持 socks5/socks4/http；凭据勿入日志 |
| 多账号 | `--account NAME`, `config accounts` | ⚠️ 命名 session smoke | 真实第二账号登录未测；注册表只存元数据 |

### 1.10 本地辅助与 Agent 契约

| 能力 | 命令 | 状态 | 说明 |
|---|---|---|---|
| 查看本地配置 | `config show` | ✅ | api_hash 掩码；显示 session/data/config、账号、代理是否配置 |
| 命名账号 | `config accounts list/remove` | ✅ | 注册表元数据；remove 不删 session 文件 |
| 使用说明 | `docs [--full]` | ✅ | 输出 USAGE.md 路径/内容 |
| 命令清单 | `commands` | ✅ | 机器可读的命令列表，便于 Agent 发现能力 |
| 统一输出 | 所有命令 | ✅ | 单 JSON `tg.v1`；`ok`、`outcome`、`error.code`、`meta.request_id` |
| 预检/执行门 | 所有写命令 | ✅ | 默认 `preview`；`--execute` 才提交；破坏性另需 `--yes`/`--confirm` |
| 幂等回执 | 所有写命令 | ✅ | 复用 `--request-id`；`executed` 重放为 `noop`，`unknown` 拒绝重试 |
| 目标解析 | 全局 CHAT | 🧪 | 支持 @username、t.me 链接（含 /name/123）、数字 id、标题/姓名；裸英文单词 username 失败会回退标题匹配 |
| 多账号 | 全局 `--session` | ⚠️ | 无账号注册表；路径由调用方管理 |

## 2. P0 命令示例（可直接复制）

### 2.1 首次授权（人工一次）

```sh
telegram-native auth qr --wait 180
# 手机 Telegram → Settings → Devices → Link Desktop Device 扫码
telegram-native auth status
```

### 2.2 建一个带话题的运营群

```sh
telegram-native group create --title "Ops" --username ops_internal --about "internal" --forum --execute
telegram-native topic create @ops_internal --title "Release" --icon-color 0x6FB9F0 --execute
telegram-native topic create @ops_internal --title "Incidents" --execute
telegram-native members add @ops_internal --users @alice,@bob --execute
telegram-native admins promote @ops_internal --users @alice \
  --rights invite_users,pin_messages,delete_messages,ban_users,manage_topics \
  --rank "Ops Lead" --execute
```

### 2.3 话题内发消息/回复

```sh
# 非 General 话题：topic id 作为 reply_to
telegram-native messages send @ops_internal --topic 123 --text "release v1.2" --execute

# General 话题：不要传 --topic（API 要求按普通超级群消息发送）
telegram-native messages send @ops_internal --text "general announcement" --execute

# 普通回复
telegram-native messages send @ops_internal --reply-to 456 --text "ack" --execute
```

### 2.4 频道 + 邀请链接

```sh
telegram-native channel create --title "Announcements" --username my_ann --about "news" --execute
telegram-native chat set-signatures @my_ann --enabled --execute
telegram-native invite link @my_ann --usage-limit 100 --expire-days 7 --execute
telegram-native invite info --link https://t.me/+AbCdEf
telegram-native invite join --link https://t.me/+AbCdEf --execute
```

### 2.5 成员/管理员/封禁

```sh
telegram-native members list @ops_internal --filter admins
telegram-native members add @ops_internal --users @carol --execute
telegram-native members remove @ops_internal --users @carol --yes --execute
telegram-native members ban @ops_internal --users @spammer --days 7 --execute
telegram-native members unban @ops_internal --users @spammer --execute
telegram-native admins demote @ops_internal --users @alice --yes --execute
```

### 2.5b 进群申请审批

```sh
telegram-native join-requests list @ops_internal --limit 100
telegram-native join-requests approve @ops_internal --users @carol --execute
telegram-native join-requests reject @ops_internal --users @spammer --yes --execute
```

### 2.6 私聊与消息

```sh
telegram-native chats list --type private --limit 20
telegram-native messages list @alice --limit 20
telegram-native messages send @alice --text "hi" --execute
telegram-native messages send @alice --file /tmp/report.pdf --text "Q3" --execute
telegram-native messages delete @alice --ids 123 --keep-for-me --yes --execute
```

### 2.7 BotFather（只在小号/一次性 Bot 上）

```sh
telegram-native bot create --name "Demo Bot" --username demo_20261024_bot --execute
telegram-native bot set-commands --username demo_20261024_bot \
  --commands-file /tmp/commands.txt --execute
telegram-native bot token --username demo_20261024_bot --show-secret --execute
telegram-native bot delete --username demo_20261024_bot \
  --confirm demo_20261024_bot --execute
```

`/tmp/commands.txt`：

```
start - Start the bot
help - Show help
```

### 2.8 当前 Top 5 高频缺口（按阻塞程度）

> 第二轮 P1 与第三轮「评论/权限/日志/话题细粒度/批量审批/代理/多账号」均已补齐。
> 当前剩余的是低频或高风险能力，不影响群/频道/话题/Bot/私聊主工作流：

1. **媒体 caption 编辑/相册重发**：可下载/转发/发相册，但不能改已有媒体 caption。
2. **频道所有权转让 / 管理员日志筛选**：日志已能读，但未做事件类型/管理员过滤。
3. **Secret Chats**：MTProto E2E，CLI 明确不覆盖。
4. **账号级危险操作**：改手机号、设置/关闭 2FA、删除账号；不做自动化。
5. **Stories / 自定义 folders / 主题 / 实时位置**：与 Agent 主工作流无关。

## 3. P1：下一阶段最值得补的能力（按优先级）

| 优先级 | 能力 | 为什么现在补 | 关键 API | 风险/注意 |
|---|---|---|---|---|
| P1-1 ✅ | **基础群→超级群迁移** | 话题/用户名/精细权限/慢速模式都要求超级群；Agent 遇到普通群只能绕路 | `messages.migrateChat` | 不可逆；迁移后 peer id 改变，需要更新本地引用/回执 |
| P1-2 ✅ | **邀请链接全生命周期** | 现在只能“新建/撤销单条”，不能盘点、改期限、删链接、看谁用链接进来 | `messages.getExportedChatInvites`, `getExportedChatInvite`, `editExportedChatInvite`, `deleteExportedChatInvite`, `getChatInviteImporters` | 永久链接不能设过期时间；bot 创建的链接普通用户不能编辑 |
| P1-3 ✅ | **媒体读/转发/相册** | Agent 目前只能上传，不能读图/读文件，也不能把消息搬到另一个群 | `messages.forwardMessages`, `SendMultiMedia`, `upload/downloadMedia`, `EditMessage(caption)` | 媒体可能含隐私；下载要限制大小/类型并落盘到私有目录 |
| P1-4 ✅ | **全局搜索 + 联系人/用户资料** | 跨会话找消息、按用户名/手机号找人，是私聊工作流入口 | `messages.searchGlobal`, `contacts.search`, `contacts.resolveUsername`, `users.getFullUser` | 全局搜索只能文本/filter；隐私设置可能拒绝 |
| P1-5 ✅ | **账号资料与会话管理** | Agent 可能需改自己的名字/bio/头像，或清理登录设备 | `account.updateProfile`, `account.updateUsername`, `account.updateProfilePhoto`, `account.getAuthorizations`, `account.resetAuthorization` | 改资料影响全账号；撤销设备可能踢掉人工登录 |
| P1-6 ✅ | **频道讨论区评论** | 频道发帖后要读评论/回复评论 | `messages.getDiscussionMessage`, `messages.getReplies`, `SendMessage(comment_to=...)` | 评论在关联超级群；需要先找到 discussion message |
| P1-7 ✅ | **默认成员权限 / 管理员日志** | 建群后常要统一禁言/限制；审计谁改了设置 | `messages.editChatDefaultBannedRights`, `channels.getAdminLog` | 默认权限影响所有成员；日志只对管理员可见 |
| P1-8 ✅ | **话题已读 / 隐藏 General / 重排置顶 / tabs UI** | 论坛运营的细粒度控制 | `messages.readDiscussion`, `EditForumTopic(hidden)`, `ReorderPinnedForumTopics`, `ToggleForum(tabs)` | 隐藏 General 是强操作；置顶数量有限 |
| P1-9 ✅ | **进群申请批量审批/回执** | 已能分页列出，但只能逐用户批准；批量操作中途失败无回执 | `HideAllChatJoinRequests` + 逐用户结果捕获 | 批量批准风险高，需显式确认 |
| P1-10 ✅ | **网络代理/多账号注册表** | 本机/服务器可能需要代理；Agent 管理多个账号 | Telethon `proxy=`，session 目录约定 | 代理配置含凭证，不能进日志 |

### P1 验收标准（建议）

- 每个 P1 写命令都必须走 `run_write`：preview 默认、`--execute`、破坏性确认、
  `--request-id` 回执；错误码稳定。
- 每个 P1 都要有离线 fake 测试 + 一个“只读真机”测试；写操作只在测试群/测试频道。
- 进群审批、迁移、改权限、删除链接等不可逆操作必须有 `--confirm` 精确标签。

## 4. P2 / 明确跳过

| 能力 | 决策 | 原因 |
|---|---|---|
| Stories / 短视频 | ⏭ | 与 Agent 主工作流无关，API 面大 |
| Gifts / Stars / 支付 / 频道订阅 | ⏭ | 资金与合规风险高 |
| Monoforum / 频道私信 / 付费消息 | ⏭ | 新且低频，权限模型复杂 |
| Web Apps / Mini Apps / Bot 支付 | ⏭ | 需要浏览器/回调服务，不属于 CLI |
| 投票 / Quiz / 游戏 | ⏭ | 低频，Agent 很少创建 |
| 群通话 / 直播 | ⏭ | 音视频栈与 CLI 无关 |
| Secret Chat / E2E | ⏭ | MTProto 加密聊天不在本 CLI 范围 |
| 主题/壁纸/表情包管理 | ⏭ | 纯展示 |
| 文件夹（Folders）管理 | ⏭ | 低频；`chats list --archived` 已覆盖常用归档 |
| 2FA 设置/修改 | ⏭ | 高风险账号安全操作，应由人工在官方客户端完成 |
| 隐私规则细调 | ⏭ | 高风险，且容易把账号锁死 |
| 聊天记录导出/导入 | ⏭ | 数据量大、隐私风险高 |
| 定时消息 | P2 | 可用外部调度器 + CLI 实现，不必进核心 |
| 表情回应/已读回执展示 | P2 | 读路径可后续补 |

## 5. “CLI 承诺了但 API 做不到”的边界

这些不是实现懒惰，而是 Telegram API/权限模型的硬边界；文档和 `--help`
必须明确，避免 Agent 误判：

| CLI 表面能力 | API 现实 | 当前处理 |
|---|---|---|
| `topic edit --icon-color` | `messages.editForumTopic` 没有 `icon_color` 字段 | 明确返回 `UNSUPPORTED`；只能在 `topic create` 设置 |
| `topic delete --topic-id 1` | General 话题不可删除（`id=1`） | 明确拒绝；可 close 或清空消息 |
| `group delete` 普通群 | 没有删除 basic group 的方法 | 明确拒绝，提示 `chat leave`/迁移 |
| `admins promote --rights/--rank` 普通群 | `messages.editChatAdmin` 只有 `is_admin` bool | 明确拒绝 `--rights/--rank`；只切换管理员 |
| `chat set-username` 普通群 | 普通群没有公开用户名 | 明确拒绝；先迁移超级群 |
| `messages send --topic 1` | General 话题按普通超级群消息发送，不应带 reply_to | 已改为 `reply_to=None`；`--topic` 与 `--reply-to` 互斥 |
| `messages send --topic N --reply-to M` | 话题内回复需 `inputReplyToMessage.top_msg_id=N`；Telethon helper 只传 reply_to_msg_id | 当前二选一，不能完整表达；需要 raw `SendMessageRequest` |
| `messages list --search Q --topic N` | `getReplies` 会忽略 search | 当前 search 被静默丢弃；`messages search --topic` 已改用 SearchRequest |
| `invite link --request-needed` 普通群 | 进群审批主要面向超级群/频道 | 需真机确认；失败会映射为 RPC 错误 |
| `bot token` | `/token` 的行为（显示当前 vs 生成新 token）需真机确认 | 只允许显式 `--execute`；默认掩码；绝不静默轮换生产 token |

## 6. Agent 使用契约（简要）

1. **先发现**：`telegram-native commands` / `--help`，不要猜参数。
2. **先预检**：所有写命令默认 `outcome=preview`；检查 `data.params/preview`。
3. **再执行**：加 `--execute`；破坏性操作再加 `--yes` 或精确 `--confirm`。
4. **复用 request-id**：把 preview 返回的 `meta.request_id` 带回执行；重试同一
   逻辑操作必须复用；`OUTCOME_UNKNOWN` 时先核对 Telegram，不要换 id 盲重试。
5. **只解析 stdout JSON**：诊断/二维码/进度在 stderr；失败时 `data=null`，
   `error.code` 是稳定分支点。
6. **不要打印秘密**：token 默认掩码；手机号掩码；session/api_hash 不进日志。
7. **写操作只在测试群/测试频道验收**；不要给真实用户/群发测试消息。

## 7. 上线前 P0 验收清单

- [ ] `auth status` 在未登录时 `ok=true, authorized=false`。
- [ ] 人工扫码后 `auth status` 返回账号信息（手机号掩码、dc_id）。
- [ ] `chats list --limit 10`、`chat info`、`messages list`、`topic list` 只读通过。
- [x] 测试超级群：create → forum enable → topic create/edit/close/reopen/pin/unpin/delete（非 General）。
- [ ] 测试 General：`messages send` 不传 `--topic` 能发；传 `--topic 1` 已按 `reply_to=None` 处理。
- [x] 测试话题：`messages send --topic N` 能发；`messages search --topic N` 能按 query 过滤。
- [x] 测试频道：create → 发帖 → 邀请链接 → `invite info`（第二账号 join 未测）。
- [ ] 测试普通群：create → add/remove → leave；确认 delete 被拒绝且提示正确。
- [x] 测试管理员：promote/demote；频道管理员能发帖（post_messages）；普通群 rights 被拒绝（preview 验证）。
- [x] 测试 BotFather：一次性 Bot 验证 `/newbot`、`/setabouttext`、`/token`、`/revoke`、`/deletebot`。
- [ ] 检查所有 token/session/receipt 文件权限为 0600/0700，日志无秘密。
- [x] 检查 FloodWait：实测 5s/6s，`THROTTLED` 带 `retry_after`，不自动重试。

## 8. 参考

- Telegram API: [Forum topics](https://core.telegram.org/api/forum),
  [Admin/banned rights](https://core.telegram.org/api/rights),
  [Invite links & join requests](https://core.telegram.org/api/invites),
  [Channels/supergroups/basic groups](https://core.telegram.org/api/channel)
- 方法页：[channels.toggleForum](https://core.telegram.org/method/channels.toggleForum),
  [messages.createForumTopic](https://core.telegram.org/method/messages.createForumTopic),
  [messages.editForumTopic](https://core.telegram.org/method/messages.editForumTopic),
  [messages.getForumTopics](https://core.telegram.org/method/messages.getForumTopics),
  [channels.editAdmin](https://core.telegram.org/method/channels.editAdmin),
  [channels.editBanned](https://core.telegram.org/method/channels.editBanned),
  [messages.exportChatInvite](https://core.telegram.org/method/messages.exportChatInvite)
- BotFather / Bot API: [BotFather guide](https://core.telegram.org/bots/features#botfather),
  [BotCommand](https://core.telegram.org/bots/api#botcommand),
  [setMyCommands](https://core.telegram.org/bots/api#setmycommands),
  [setMyName](https://core.telegram.org/bots/api#setmyname),
  [setMyDescription](https://core.telegram.org/bots/api#setmydescription),
  [setMyShortDescription](https://core.telegram.org/bots/api#setmyshortdescription)
- Bot 数量限制（非官方但一致）：[tginfo/Telegram-Limits #288](https://github.com/tginfo/Telegram-Limits/issues/288)
- 慢速模式可选值（Pyrogram/TDLib 文档）：[set_slow_mode](https://docs.pyrogram.org/api/methods/set_slow_mode),
  [SetChatSlowModeDelay](https://pub.dev/documentation/tdlib/latest/td_api/SetChatSlowModeDelay-class.html)
