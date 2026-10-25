# Telegram API 兼容性与调用审计

> 面向实现者与上线验收人。记录 Telethon 1.45.0（TL layer 229）下我们实际发出的
> 请求、所需管理员权限、硬限制，以及当前 CLI 里仍存在的错误/不兼容。
>
> **快照说明**：本文的 `file:line` 取自 2026-10-24 08:03 ET 的快照。07:55–08:03
> 之间 Lead 与 botfather-engineer 已落一批 P0/兼容性修复；行号仍可能继续漂移，
> 请以函数名 + `grep -n` 复核。文末附快照 SHA-256。已经修掉的问题标为
> 「已修（快照）」，仍开放的问题单独列出。

## 0. 证据与阅读顺序

1. 先看 §3.1「已修（快照）」确认本轮修复范围，避免把已修问题当现行 bug。
2. 再看 §3.2「仍开放」和 §8「上线前 blocker」决定下一步。
3. 实现新命令时查 §2「请求签名」、§4「权限矩阵」、§5「限制」。
4. BotFather 相关看 §6；它不是普通 RPC，不能按单次请求设计。

证据来源：

- 已安装 Telethon 源码：
  `.venv/lib/python3.15/site-packages/telethon/tl/functions/*.py`、
  `.venv/lib/python3.15/site-packages/telethon/tl/types/__init__.py`、
  `.venv/lib/python3.15/site-packages/telethon/client/*.py`。
- 官方 MTProto 文档：`https://core.telegram.org/method/<method>` 与
  `https://core.telegram.org/api/*`（2026-10-24 抓取）。
- BotFather/Bot API 文档：`https://core.telegram.org/bots/features#botfather`、
  `https://core.telegram.org/bots/api#botcommand` 等。
- 外部资料仅作旁证；不作为指令执行。

## 1. 安装层事实与「文档层偏移」

| 项目 | 值 |
|---|---|
| Telethon | 1.45.0（`telethon-1.45.0.dist-info`） |
| TL layer | **229**（`telethon/tl/alltlobjects.py: LAYER = 229`） |
| Python | venv 为 3.15（`telegram-native/.venv`）；USAGE 写 3.12，需修正 |
| 本机 API 凭据 | 默认 `api_id=9` + 公开 `api_hash`（`telegram_native/config.py:13-14`） |

### 1.1 为什么必须注意「层偏移」

core.telegram.org 的 method 页有时落后于已安装 Telethon 的 schema。当前已确认的
差异（**同一方法，服务端构造函数可能不同**）：

| 方法 | 官方文档（抓取时） | 安装层 229 / Telethon 1.45 | 影响 |
|---|---|---|---|
| `messages.importChatInvite` | `#6c50051c hash = Updates` | `#de91436e hash = messages.ChatInviteJoinResult`，`.updates` 里才是 `Updates` | 老代码读 `result.chats` 会拿到 `None`；必须兼容两种形状 |
| `channels.joinChannel` | `Updates` | `messages.ChatInviteJoinResult` | 公开链接入群的结果可能没有 `.chats` |
| `channels.toggleJoinRequest` | `channel enabled`（旧签名） | `channel enabled apply_to_invites? guard_bot?` | 传新字段在旧服务端可能被忽略/拒绝；需真机验证 |
| `chatAdminRights` | 18 个 flags（无 `manage_linked_peers`/`manage_welcome_messages`） | 额外含 `manage_linked_peers`、`manage_welcome_messages` | 本 CLI 未使用这两个新 flag，暂无影响 |
| `messages.getForumTopics.offset_date` | TL int（date） | Telethon `datetime \| None` | 正常；但分页必须同时带 `offset_date/offset_id/offset_topic` |

**结论**：不要手写 TL 构造函数；以安装层为准，同时用 `.updates`/`.chats` 的
兼容读取。上线前用只读请求验证一次，避免 `TypeNotFoundError`/未知构造函数。

### 1.2 客户端行为

- `TelegramClient(..., flood_sleep_threshold=0, receive_updates=False)`
  （`telegram_native/client.py:100-105`）。
  - `flood_sleep_threshold=0`：**不自动睡过 FloodWait**，直接抛错，CLI 映射为
    `THROTTLED` + `details.retry_after`。这是 Agent 友好的设计，但也意味着调用方
    必须自己等待。
  - `receive_updates=False`：不跑事件循环、不注册 handler；BotFather 只能
    `get_messages(min_id=...)` 轮询。其他命令都是请求/响应，不受影响。
- `parse_mode=None`：不解析 Markdown，避免 Agent 文本里的 `*`/`_` 被吞。
- 每个命令新建/关闭一个 client（`_dispatch` finally 关闭）；不要跨命令共享事件循环。

## 2. 已核验的请求签名（安装层 229）

> 下列签名来自本地 `inspect.signature`；返回值来自 Telethon 生成类的 docstring。
> 只列本 CLI 用到的请求和下一步 P1 要用的请求。

### 2.1 群 / 超级群 / 频道

| 请求 | 安装层签名（关键字） | 返回 | 备注 |
|---|---|---|---|
| `channels.CreateChannelRequest` | `title, about, broadcast=None, megagroup=None, for_import=None, forum=None, geo_point=None, address=None, ttl_period=None` | `Updates` | `about` 必填（可为空串）；`forum=True` 必须同时 `megagroup=True`；`broadcast` 与 `megagroup` 互斥 |
| `messages.CreateChatRequest` | `users, title, ttl_period=None` | `messages.InvitedUsers` | 至少 1 个用户；普通群上限约 200 |
| `channels.ToggleForumRequest` | `channel, enabled, tabs` | `Updates` | `tabs` **必填**；只影响 UI（tabbed vs list），不是“是否开启”；**仅群主可调用** |
| `messages.MigrateChatRequest` | `chat_id` | `Updates` | 在 `messages` 命名空间，不在 `channels`；普通群→超级群，不可逆 |
| `channels.EditTitleRequest` | `channel, title` | `Updates` | 超级群/频道；标题 1–128 |
| `channels.EditPhotoRequest` | `channel, photo` | `Updates` | `photo=InputChatUploadedPhoto(file=...)` |
| `channels.UpdateUsernameRequest` | `channel, username` | `Bool` | 空串=清除；普通群必须先迁移 |
| `channels.DeleteChannelRequest` | `channel` | `Updates` | 仅创建者；普通群没有 DeleteChat |
| `channels.ToggleSlowModeRequest` | `channel, seconds` | `Updates` | 仅超级群；服务端只接受有限值（见 §5.1） |
| `channels.ToggleSignaturesRequest` | `channel, signatures_enabled=None, profiles_enabled=None` | `Updates` | 仅广播频道；`profiles_enabled` 需 `signatures_enabled=True` |
| `channels.SetDiscussionGroupRequest` | `broadcast, group` | `Bool` | 需要频道+群管理员；取消关联用 `InputChannelEmpty`（CLI 未实现） |
| `channels.ToggleJoinRequestRequest` | `channel, enabled, apply_to_invites=None, guard_bot=None` | `Updates` | 安装层新签名；官方文档可能仍旧 |
| `channels.ToggleJoinToSendRequest` | `channel, enabled` | `Updates` | 仅超级群 |
| `channels.GetFullChannelRequest` | `channel` | `messages.ChatFull` | 返回 `full_chat` + `chats` + `users` |
| `channels.GetParticipantsRequest` | `channel, filter, offset, limit, hash` | `channels.ChannelParticipants` | 一般用 `iter_participants` 更高层封装 |

### 2.2 Forum Topics

| 请求 | 安装层签名 | 返回 | 关键限制 |
|---|---|---|---|
| `messages.CreateForumTopicRequest` | `peer, title, title_missing=None, icon_color=None, icon_emoji_id=None, random_id=None, send_as=None` | `Updates` | `title` ≤128 **UTF-8 字节**；`icon_color` 只能是六种 RGB；自定义 emoji 受 Premium/默认表情包限制 |
| `messages.EditForumTopicRequest` | `peer, topic_id, title=None, icon_emoji_id=None, closed=None, hidden=None` | `Updates` | **没有 `icon_color`**；`closed` 不能与任何其他字段同请求（`TOPIC_CLOSE_SEPARATELY`）；`hidden` 只对 General（id=1）有效 |
| `messages.GetForumTopicsRequest` | `peer, offset_date, offset_id, offset_topic, limit, q=None` | `messages.ForumTopics` | 分页三件套必须一起用；服务端可能返回少于 limit；默认按活动排序 |
| `messages.UpdatePinnedForumTopicRequest` | `peer, topic_id, pinned` | `Updates` | 置顶数量受 `topics_pinned_limit` 限制 |
| `messages.DeleteTopicHistoryRequest` | `peer, top_msg_id` | `messages.AffectedHistory` | 删除该 topic 全部消息；**General id=1 不可删除** |
| `messages.GetForumTopicsByIDRequest` | `peer, topics` | `messages.ForumTopics` | P1 可用于按 id 精确回读 |
| `messages.ReorderPinnedForumTopicsRequest` | `peer, order, force=None` | `Updates` | P2 |

### 2.3 成员 / 管理员 / 封禁

| 请求 | 安装层签名 | 返回 | 备注 |
|---|---|---|---|
| `channels.EditAdminRequest` | `channel, user_id, admin_rights, rank=None` | `Updates` | 超级群/频道；`rank` 只在这里有 |
| `messages.EditChatAdminRequest` | `chat_id, user_id, is_admin` | `Bool` | 普通群只有 bool，无精细权限/头衔 |
| `channels.EditBannedRequest` | `channel, participant, banned_rights` | `Updates` | 仅超级群/频道；普通群无精细封禁 |
| `channels.InviteToChannelRequest` | `channel, users` | `messages.InvitedUsers` | 返回含 `missing_invitees`（隐私/Premium 限制），不能只看“成功” |
| `messages.AddChatUserRequest` | `chat_id, user_id, fwd_limit` | `messages.InvitedUsers` | 普通群逐个加；`fwd_limit` 是转发最近 N 条 |
| `messages.DeleteChatUserRequest` | `chat_id, user_id, revoke_history=None` | `Updates` | 普通群移除/自己退群；`revoke_history` 清对方历史 |
| `channels.LeaveChannelRequest` | `channel` | `Updates` | 频道创建者不能直接 leave（`USER_CREATOR`） |

`ChatAdminRights` 字段（安装层 `tl/types/__init__.py:6524`）：

`change_info, post_messages, edit_messages, delete_messages, ban_users,
invite_users, pin_messages, add_admins, anonymous, manage_call, other,
manage_topics, post_stories, edit_stories, delete_stories,
manage_direct_messages, manage_ranks, manage_linked_peers,
manage_welcome_messages`

- `post_messages` / `edit_messages`：**仅频道**；超级群传了也不会让管理员“发帖”。
- `manage_topics`：论坛话题创建/编辑/删除/置顶。
- `anonymous`：隐藏管理员并禁止其以个人身份发言（超级群）。
- `other`：没有具体权限但仍保留管理员身份（可看日志/统计/成员/忽略慢速模式）。

`ChatBannedRights` 字段（`tl/types/__init__.py:6644`）语义是**反向**的：flag=True
表示“禁止”。`until_date` 为 None/0 表示永久；要解封传全 False + `until_date=None`。

### 2.4 邀请链接 / 入群

| 请求 | 安装层签名 | 返回 | 备注 |
|---|---|---|---|
| `messages.ExportChatInviteRequest` | `peer, legacy_revoke_permanent=None, request_needed=None, expire_date=None, usage_limit=None, title=None, subscription_pricing=None` | `ExportedChatInvite` | 每次调用通常**新建**一条链接；永久链接不能设过期时间 |
| `messages.EditExportedChatInviteRequest` | `peer, link, revoked=None, expire_date=None, usage_limit=None, request_needed=None, title=None` | `messages.ExportedChatInvite` | 编辑/撤销已有链接；永久链接 `CHAT_INVITE_PERMANENT`；bot 创建的链接普通用户不能编辑 |
| `messages.GetExportedChatInvitesRequest` | `peer, admin_id, limit, revoked=None, offset_date=None, offset_link=None` | `messages.ExportedChatInvites` | P1 列出链接 |
| `messages.GetExportedChatInviteRequest` | `peer, link` | `messages.ExportedChatInvite` | P1 查单条 |
| `messages.DeleteExportedChatInviteRequest` | `peer, link` | `Bool` | P1 永久删除（不是 revoke） |
| `messages.CheckChatInviteRequest` | `hash` | `ChatInvite` | 未加入时返回 `chatInvite`；已加入返回 `chatInviteAlready` |
| `messages.ImportChatInviteRequest` | `hash` | **`messages.ChatInviteJoinResult`**（安装层） | 需额外读 `.updates.chats`；`INVITE_REQUEST_SENT` 是“申请已发送”，不是失败 |
| `channels.JoinChannelRequest` | `channel` | **`messages.ChatInviteJoinResult`**（安装层） | 公开用户名入群；join-request 群可能返回 `INVITE_REQUEST_SENT` |
| `messages.GetChatInviteImportersRequest` | `peer, requested=False, subscription_expired=False, link=None, q=None, offset_date, offset_user, limit` | `messages.ChatInviteImporters` | P0 审批列表 |
| `messages.HideChatJoinRequestRequest` | `peer, user_id, approved` | `Updates` | P0 批准/拒绝单条 |
| `messages.HideAllChatJoinRequestsRequest` | `peer, approved, link=None` | `Updates` | P0 批量批准/拒绝 |

### 2.5 消息 / 搜索 / 已读

| 请求 | 安装层签名 | 返回 | 备注 |
|---|---|---|---|
| `messages.SendMessageRequest` | `peer, reply_to, message, random_id, ...` | `Updates` | `reply_to` 是 `InputReplyTo`，不是 int；论坛内回复需 `top_msg_id` |
| `messages.SearchRequest` | `peer, q, filter, min_date, max_date, offset_id, add_offset, limit, max_id, min_id, hash, from_id=None, ..., top_msg_id=None` | `messages.Messages` | **`filter` 必填**；topic 搜索用 `top_msg_id`，不要用 `getReplies` |
| `messages.GetRepliesRequest` | `peer, msg_id, offset_id, offset_date, add_offset, limit, max_id, min_id, hash` | `messages.Messages` | `iter_messages(reply_to=...)` 走这里；**search/filter 会被忽略** |
| `messages.UpdatePinnedMessageRequest` | `peer, id, silent=None, unpin=None, pm_oneside=None` | `Updates` | 没有 `top_msg_id`；话题内置顶就是对消息 id 操作 |
| `messages.ReadHistoryRequest` | `peer, max_id` | `messages.AffectedMessages` | 只能到 max_id，不能按 topic 精确已读 |
| `messages.GetDiscussionMessageRequest` | `peer, msg_id` | `messages.DiscussionMessage` | 频道评论入口（P1） |

Telethon 高层 `client.send_message(..., reply_to=int)` 只构造
`InputReplyToMessage(reply_to_msg_id=id)`（`telethon/client/messages.py:893`），
**不能设置 `top_msg_id`**。要完整支持“话题内回复某条消息”，需要直接用
`messages.SendMessageRequest` 并构造 `types.InputReplyToMessage(reply_to_msg_id=M,
top_msg_id=N)`。

## 3. 当前实现审计

### 3.1 已修（快照 08:03，仍需回归测试）

| 问题 | 快照位置 | 现在做了什么 | 验证点 |
|---|---|---|---|
| `messages delete` 读不存在的 `args.revoke`（parser 只有 `--keep-for-me`）→ `AttributeError` | `telegram_native/commands/messages.py:231` | `revoke = not keep_for_me`；传给 `delete_messages`/回执 | `messages delete ... --execute` 不再 INTERNAL；`--keep-for-me` 确实 revoke=False |
| `ImportChatInvite` 返回 `ChatInviteJoinResult`，旧代码读 `.chats` | `telegram_native/commands/groups.py:322-326` | `updates = getattr(result, "updates", result)` 后再读 `.chats` | 私有链接入群后 `chat` 不为 None |
| join-request 群返回 `INVITE_REQUEST_SENT` 被当失败 | `groups.py:315-321`、`338-344` | 两条分支都捕获，返回 `joined=False, request_sent=True` | 公开/私有申请入群都返回成功语义 |
| `group delete` 对普通群调用 `DeleteChannel` | `groups.py:203-209` | 明确 `UNSUPPORTED`，提示 `chat leave`/迁移 | 普通群不会发无效 RPC |
| `topic delete` 允许 General id=1 | `topics.py:357-362` | 明确拒绝 | General 不可删 |
| `topic list` 只传 `offset_topic`，分页不完整 | `topics.py:137-166` | 同时维护 `offset_date/offset_id/offset_topic`，支持 `--all` | 多页话题无重复/漏项 |
| `topic create/edit` 未校验标题字节、icon_color | `topics.py:27-43,193-199,249` | 128 UTF-8 字节 + 六种官方 RGB 校验 | 超长/非法颜色返回 VALIDATION，不打网络 |
| `topic edit --icon-color` 静默忽略 | `topics.py:245-249` | 明确 `UNSUPPORTED` | 不再“成功但没改” |
| `messages search --topic` 走 `getReplies` 丢 query | `messages.py:32-54,90-118` | 改用 `SearchRequest(top_msg_id=...)`，`filter=InputMessagesFilterEmpty()` | 带 query 的 topic 搜索只返回匹配项 |
| `messages list --search --topic` 静默丢 search | `messages.py:57-88` | 同样走 `_search_in_topic` | 组合查询生效 |
| 频道管理员默认权限缺 `post_messages` | `members.py:307-312` | 广播频道默认加 `post_messages` | 新 promote 的频道管理员能发帖 |
| 普通群 `--rights/--rank` 被静默忽略 | `members.py:300-304` | 明确 `UNSUPPORTED` | 不再返回“已设置精细权限” |
| slowmode 允许 86400 等非法值 | `chats.py:487-494` | 白名单 0/5/10/30/60/300/900/3600 | 非法值本地 VALIDATION |
| `t.me/<name>/<msg>` 不能解析成会话 | `client.py:64-83` | `_parse_link` 支持 `/name/123`、`/s/name/123` | `chat info https://t.me/group/123` 可用 |
| 建群/建频道后邀请失败导致“已建但无回执” | `groups.py:67-100`、`158-180` | 邀请/用户名/头像失败记入 `warnings`，整体仍 executed | 回执存在，重试不会重复建群 |
| `messages send --topic 1` 传 reply_to=1 | `messages.py:146-150` | General 时 `reply_to=None`；`--topic` 与 `--reply-to` 互斥 | General 能发；同时传两个报 VALIDATION |
| 能 `group set-join-request` 却没有审批命令 | `join_requests.py:15-130` | 新增 `join-requests list/approve/reject`；list 已按 offset_date/offset_user 分页 | 逐用户审批；见 P1-2 |
| `group create --basic --username/--photo` 静默忽略 | `groups.py:68-76` | 现在写入 `warnings`（`unsupported_options`）并继续建群 | preview/execute 都能看到忽略原因 |
| BotFather `/setabout` 命令写错 | `bots.py:867` | 已改为官方 `/setabouttext`；预览/文档/测试同步 | 真机确认 prompt |
| BotFather `/token`/`/revoke` 只支持一种形状 | `bots.py:806-852`、`907-952` | 同时兼容“直接 token”和“确认按钮”；超时按 unknown 处理；结果含 `token_rotated` | 真机确认两种形状 |
| `auth password` 跨进程 pending 状态未知 | `auth.py:232-276` | **已真机验证**：进程重启后仍能完成 2FA | 保留为已验证行为；若失败仍提示重跑 `auth qr` |
| 邀请/权限/topic RPC 错误统一 REJECTED | `errors.py:7-25,80-110` | 已补 `ChatAdminInviteRequired/RightForbidden`→PERMISSION_DENIED，`InviteHashExpired/Invalid/TopicDeleted/MessageIdInvalid/UserPrivacyRestricted` 等映射 | 仍需真机确认 details.reason |
| 单英文单词标题被当 username | `client.py:161-191` | 裸词 username 查询失败或类型不符时回退标题匹配；显式 `@name` 仍直接报错 | 标题/用户名冲突时用 @/peer_id |
| 文本长度按 code points 计 | `messages.py:16-33` | 改用 UTF-16 code units | emoji 计数与服务端一致 |
| `topic_id` 把普通回复误当 topic | `serialize.py:217-225` | 仅 `forum_topic` 时输出 `topic_id`，另出 `forum_topic`；保留 `reply_to_msg_id` | Agent 解析正确 |
| 普通群 ban `--days` 负数 | `members.py:218-224` | `--days<=0` 本地 VALIDATION | 不再生成过去时间 |
| 普通群 promote 返回虚假 rights | `members.py:354-370` | 普通群返回 `rights=[]` + `basic_group_is_admin_only=true` | 不再误导 |
| `missing_invitees` 被忽略 | `members.py:126-158` | 解析并写入 `missing_invitees`/`warnings` | 部分邀请失败可见 |
| 普通群 `recent/kicked` 静默退化 | `members.py:56-62` | 明确 `UNSUPPORTED` | 不再返回错误成员集 |
| 普通群成员 N+1 get_entity | `members.py:30-40` | 先用 `full.users` 映射，缺失才回退 | 常见路径 1 次请求 |
| `set-discussion` 不能取消 | `chats.py:579-600`、`cli.py:115-121` | 新增 `--clear`，发 `SetDiscussionGroup(group=InputChannelEmpty())` | 关联可解绑 |
| 频道创建者 `leave` 无预检 | `chats.py:278-285` | owner 广播频道直接 `UNSUPPORTED`，提示转让/删除 | 不再等 `USER_CREATOR` |
| 普通群标题/简介/用户名无本地校验 | `chats.py:27-51,169,199,226` | 增加 128/255/5–32 校验；清除 username 允许空串 | 非法输入本地 VALIDATION |

### 3.2 仍开放的问题（按严重度）

#### P0 — 上线前必须处理 / 验证

| # | 位置 | 观察 | 影响 | 建议/状态 |
|---|---|---|---|---|
| P0-1 | `bots.py:907-952` | `/revoke` 选择 bot 后可能有/没有确认按钮；实现现在两种形态都接受，并返回 `confirmation_required`、`token_rotated=true` | 旧 token 在“选择 bot”后可能已失效；真机形状仍未验证 | 已改为兼容两种形态；上线前用一次性 bot 验证“选择后直接返回 token”和“需确认”两条路径 |
| P0-2 | `bots.py:806-852` | `bot token` 调 `/token`；官方文档暗示会生成新 token，第三方资料说只显示当前 token。实现现在检测轮换措辞并返回 `token_rotated: true/false/null` + `rotation_note` | 若实际轮换，Agent 仍可能把“取 token”当只读；真机语义未验证 | 真机确认；默认把它当潜在写操作；预览/结果明确提示；不要在无 `--execute` 时调用 |
| P0-4 | `telegram_native/config.py:13-14` | 默认使用 `api_id=9` + 公开 macOS 客户端 hash | 公开 api_id 可能触发 `ApiIdPublishedFlood`、限流或账号风控 | 上线账号必须用 my.telegram.org 自己的 api_id/api_hash；默认值只用于开发/只读 |
| P0-5 | 全仓 | 只读真机验证已完成：`auth status`、`chats list`、`chat info`（公开频道+论坛群）、`messages list/search`（含 `--topic`）、`topic list`、`members list`、`admins list`、`invite info`、`join-requests list`、`bot list`、`bot token`（masked）、`chat mute/unmute`（executed + idempotent replay）；写路径只验证了 preview | 只读路径已验证；写操作/BotFather 变更语义仍未验证；`bot token` 验证发生在最新 rotation 语义改动之前 | 继续按 §8 在测试群/一次性 Bot 验证写操作；注意 `bot token` 新语义不算 live-verified |

#### P1 — 功能正确性/可发现性

| # | 位置 | 观察 | 影响 | 建议 |
|---|---|---|---|---|
| P1-1 | `groups.py:237-305` | 只有 `invite link`/`invite revoke`；没有 list/edit/delete/importers；每次 link 都新建 | 链接堆积、无法盘点/改期限/删链接/看谁加入 | 增加 `invite list/edit/delete/importers`；永久链接不能设过期；短链先规范化 |
| P1-2 | `join_requests.py:15-130` | 新模块，无离线测试；list 已分页，但 approve/reject 仍逐用户循环、部分失败会中断；basic group 也被允许 | 批量操作可能半批准且无回执；basic group 可能直接 RPC 错误 | 逐用户捕获结果并写回执；限制 basic group；补 `HideAllChatJoinRequests` |
| P1-3 | `messages.py:144-205` | `--topic N --reply-to M` 互斥；话题内回复某条消息需要 `InputReplyToMessage(reply_to_msg_id=M, top_msg_id=N)`，高层 helper 不能表达 | 无法既回复某条消息又保持话题上下文 | 用 raw `SendMessageRequest`；可加 `--reply-to-topic-message` |
| P1-4 | `messages.py:303-325` | `mark-read` 只调 `ReadHistory(max_id)`；没有按 topic 的已读 | 论坛运营无法只清某个 topic 的未读 | 确认当前层 `messages.readDiscussion` 签名后补 `--topic` |
| P1-5 | `topics.py:212-225` | `CreateForumTopic` 结果解析失败时，fallback 取 `GetForumTopics(limit=1)` 的“最新活动” topic，不一定是刚建的 | 回执可能给错 topic_id（低概率） | 优先从返回 Updates 的 `MessageActionTopicCreate` 取；无法取到时返回 `topic_id=null` + warning，不要猜 |
| P1-6 | `messages.py:159-175` | 上传文件无大小校验；Telegram 用户会话上限 2 GB（Premium 4 GB），Bot API 更小 | 大文件可能传一半失败/超时 | 发前 stat + 明确上限；大文件提示可能超时 |
| P1-7 | `telegram_native/safety.py:53-66` | 回执会把 `result` 整体落盘（0600）；邀请链接/消息摘要会进磁盘 | 本地泄露面扩大 | 文档明确 receipts 含业务结果；BotFather token 已 mask；考虑只存摘要/结果 id |
#### P2 — 低危/打磨

| # | 位置 | 观察 | 建议 |
|---|---|---|---|
| P2-1 | `bots.py:50` | token 正则 `\b...\b` 在 secret 以 `-`/`_` 结尾时可能匹配失败（`\b` 依赖 word char） | 用 `(?![A-Za-z0-9_-])` 作为右边界；实测真实 token 长度 |
| P2-2 | `groups.py:19-27` | 邀请链接只识别 `t.me/+`、`t.me/joinchat/`、`tg://join`；官方还允许 `telegram.me`/`telegram.dog` | 扩展正则，统一规范化后再调 API |
| P2-3 | `client.py:64-83` | `t.me/name?single`、带 query 的链接未处理 | 先 `urllib.parse` 去 query，再解析 path |
| P2-4 | `commands/common.py:37-38` | `add_limit(maximum=...)` 的 maximum 从未使用；负数/0 可传 | 校验 `1 <= limit <= 1000`（或按 API 实际上限） |
| P2-5 | `chats.py:27-60` | `chats list --limit N` 先取 N 个 dialog 再按 type/search 过滤，可能少于 N | 文档说明；需要精确数量时用 `--all`/更大 limit 或分页 |
| P2-6 | `chats.py:381-410` | 归档用 `edit_folder(folder=1/0)`；自定义 folder 未支持 | 归档/取消已够用；自定义 folder 归 P2 |
| P2-7 | `chats.py:325-355` | mute 用 2038-01-01 表示长期；服务端可能存为 0/最大 int | 可接受；真机确认 UI 显示“ forever” |
| P2-8 | `serialize.py:228-241` | topic_to_dict 未输出 `short/title_missing/from_id` | 需要时补 |
| P2-9 | `messages.py` | `message_to_dict` 的媒体只有类型名，无 file id/size/mime | P1 媒体下载时一起补 |
| P2-10 | `topics.py:131-176` | 默认按活动排序；无 `order_by_create_date` 选项 | 需要“按创建时间”时加参数（`ForumTopics.order_by_create_date` 由服务端返回） |
| P2-11 | `auth.py:83-96` | `auth login` 只能交互式 stdin；Agent 不能传 `--code` | 可加 `--code-stdin`（不要把验证码放参数/环境） |
| P2-12 | `chats.py:301-323` | `delete-dialog --revoke` 对频道只清历史还是退群，语义需确认 | 真机确认；文档写清楚“不会自动 leave” |

## 4. 权限矩阵（谁需要什么）

| 操作 | 所需权限 | 适用对象 | 备注/错误 |
|---|---|---|---|
| 建群/建频道 | 无（普通用户） | 任意 | 受 `CHANNELS_TOO_MUCH`、spam 限制 |
| 删除群/频道 | **创建者（owner）** | 超级群/频道 | 普通群无删除 API；非 owner 会 `USER_CREATOR`/`CHAT_ADMIN_REQUIRED` |
| 改标题/简介/头像 | `change_info` 或 owner | 群/超级群/频道 | 普通群管理员也可（creator 授予） |
| 设/清公开用户名 | 管理员 + `can_set_username`；通常 owner | 超级群/频道 | 普通群必须先迁移；用户名全局唯一 |
| 慢速模式 | 管理员 | 超级群 | 官方只写 `CHAT_ADMIN_REQUIRED`；实践中需 `change_info`，待真机确认 |
| 开/关话题 | **仅 owner** | 超级群 | `CHAT_ADMIN_REQUIRED` 不等于够；owner-only 是官方 forum 文档明确写的 |
| 话题创建/编辑/删除/置顶/关闭 | `manage_topics` | 论坛超级群 | `CHAT_ADMIN_REQUIRED`；General 不可删 |
| 邀请成员 | `invite_users` | 超级群/频道；普通群成员 | 普通群还受隐私/200 人上限 |
| 踢人/封禁/解封 | `ban_users` | 超级群/频道 | 普通群无 `EditBanned`；只能 DeleteChatUser |
| 置顶消息 | `pin_messages` | 群/频道 | 普通群管理员也可 |
| 删除他人消息 | `delete_messages` | 群/频道 | 只能删自己消息时不需要 |
| 设/撤管理员 | `add_admins`；不能授予超出自己权限的 rights | 群/超级群/频道 | 普通群只有 bool；owner 不能被非 owner 改 |
| 创建/编辑/撤销邀请链接 | `invite_users` | 超级群/频道/普通群（有限） | 永久链接不能设过期；bot 创建的链接普通用户不能编辑 |
| 审批进群申请 | `invite_users`（待真机确认） | 开了 join-request 的群/频道 | `hideChatJoinRequest(approved=True/False)` |
| 频道发帖 | `post_messages` | 广播频道 | 普通成员不能发；管理员默认现在会带 |
| 频道署名 | 频道管理员 | 广播频道 | `ToggleSignatures` |
| 关联讨论群 | 频道管理员 + 群管理员 | 频道/超级群 | 群需未隐藏历史；不能是已关联的论坛 |
| 默认成员权限 | `change_info`（待确认） | 群/超级群/频道 | `editChatDefaultBannedRights` 不能用 `view_messages` |
| 管理员日志 | 管理员（`other` 等） | 超级群/频道 | P2 |

## 5. 限制、FloodWait 与错误语义

### 5.1 硬限制（本地应校验/文档应写清）

| 对象 | 限制 | 依据/备注 |
|---|---|---|
| 群/频道标题 | 1–128 字符 | Bot API `setChatTitle`；MTProto 同一上限 |
| 群/频道简介 | 0–255 字符 | Bot API `setChatDescription`；`CHAT_ABOUT_TOO_LONG` |
| 群/频道头像 | ≤10 MB | Bot API `setChatPhoto` 口径；CLI 已校验 10 MB |
| 话题标题 | ≤128 **UTF-8 字节** | `messages.createForumTopic` 官方参数说明 |
| 话题 icon_color | 仅 `0x6FB9F0, 0xFFD67E, 0xCB86DB, 0x8EEE98, 0xFF93B2, 0xFB6F5F` | 官方 method 页；CLI 已白名单 |
| 话题置顶数 | 服务端 `topics_pinned_limit` | 客户端配置参数；超限会 RPC 错误 |
| 消息文本 | ≤4096（UTF-16 code units） | Bot API sendMessage；emoji 计数注意 |
| 媒体 caption | ≤1024 | Bot API sendPhoto/sendDocument 等 |
| 文件大小 | 用户会话 2 GB，Premium 4 GB；Bot API 更小 | 本 CLI 未校验；上传超时风险 |
| 慢速模式 | 0/5/10/30/60/300/900/3600 秒 | Pyrogram/TDLib 文档；CLI 已白名单 |
| 邀请链接过期 | 必须是未来时间；永久链接不能设过期 | `EXPIRE_DATE_INVALID`/`CHAT_INVITE_PERMANENT` |
| 邀请链接次数 | 正整数；0/负数无效 | `USAGE_LIMIT_INVALID` |
| 普通群成员 | 约 200 | 超过会 `USERS_TOO_MUCH`/需迁移 |
| Bot 数量 | 20/账号；Premium 40/账号 | BotFather 限制；非官方汇总，需以 BotFather 实际回复为准 |
| Bot username | 5–32，字母/数字/下划线，`bot` 结尾，全局唯一且不可改 | BotFather 文档 |
| Bot 显示名 | 0–64 | Bot API `setMyName` |
| Bot description | 0–512 | Bot API `setMyDescription` |
| Bot about/short description | 0–120 | Bot API `setMyShortDescription` + BotFather 文档 |
| Bot commands | ≤100 条；command 1–32 小写字母/数字/下划线；description 1–256 | Bot API `BotCommand`/`setMyCommands` |
| 进群申请 | 每个链接可有 `request_needed`；批准后才入群 | `INVITE_REQUEST_SENT` 是成功语义 |

### 5.2 FloodWait / 慢速模式

- `flood_sleep_threshold=0` → Telethon 不睡，`FloodWaitError` 原样抛出。
  `errors.map_exception` 映射为 `THROTTLED`，`details.retry_after=exc.seconds`。
- 慢速模式 → `SlowModeWaitError`，同样 `THROTTLED`。
- **不要自动重试**：Agent 应读 `retry_after` 等够再发；写操作若已提交但超时，
  `run_write` 会写 `unknown` 回执并拒绝同 id 重试。
- 管理员权限 `other` 可忽略慢速模式（官方 rights 文档），但 CLI 不依赖这一点。

### 5.3 错误映射现状

`errors.py` 已显式映射：`ChatAdminRequired/ChatAdminInviteRequired/
ChatWriteForbidden/ChatForbidden/RightForbidden → PERMISSION_DENIED`；
`InviteHashExpired/Invalid、TopicDeleted、MessageIdInvalid → NOT_FOUND`；
`UserPrivacyRestricted → PERMISSION_DENIED`；`Username*`、`FloodWait`、
`SlowModeWait` 等也有专用映射。仍建议补：

| Telegram 错误 | 建议 code | 场景 |
|---|---|---|
| `InviteRequestSentError` | **成功语义**，不要走错误映射 | 申请入群已发送；groups.py 已特殊处理 |
| `UsersTooMuchError`/`ChannelsTooMuchError` | `REJECTED`（details.reason=limit） | 群/频道数量上限 |
| `UsernamePurchaseAvailableError` | `REJECTED`（details.reason=purchasable） | Fragment 可购买用户名 |
| `ChatNotModifiedError`/`TopicNotModifiedError` | `noop` 或 `REJECTED` | 重复设置相同值 |
| `AdminRankInvalidError`/`AdminRankEmojiNotAllowedError` | `VALIDATION` | 管理员头衔非法 |

## 6. BotFather / Bot 生命周期

### 6.1 架构事实

- BotFather 是 **普通用户会话** 里的一个 Bot；创建/删除/改 token 只能通过它，
  不能用 Bot API token 完成（Bot API 只能管理已有 Bot 的设置）。
- 它不是一次 RPC，而是“发消息 → 等回复/编辑 → 点 inline button”的对话。
  当前实现（`telegram_native/commands/bots.py`）用：
  `send_message("BotFather", ...)` + `get_messages("BotFather", min_id=...)` +
  `message.buttons[i][j].click()`，每轮对话持有 `botfather.lock`。
- `receive_updates=False` 意味着不能靠 `NewMessage` 事件；轮询是正确路径，但
  延迟/编辑消息/按钮回调需要仔细处理（当前实现已处理 fingerprint/edit）。
- BotFather 会**编辑原消息**（点按钮后）而不是发新消息；只比较 `message.id` 会漏。

### 6.2 命令与流程（官方文档 + 现状）

| 命令 | 流程 | 现状/风险 |
|---|---|---|
| `/newbot` | 问 name → 问 username → 返回 token | 本地校验 5–32/`bot` 结尾；token 存 0600；username 占用/非法由 BotFather 文本返回 |
| `/mybots` | 列出 Bot 的 inline buttons | 解析按钮里的 `...bot`；display name 形态可能解析失败 |
| `/token` | 选 Bot → 返回 token | 结果含 `token_rotated: true/false/null` + `rotation_note`；语义仍需真机确认 |
| `/setname` | 选 Bot → 问 new name → 成功 | ≤64 |
| `/setabouttext` | 选 Bot → 问 new about → 成功 | ≤120；已改用 `/setabouttext`，待真机验证 prompt |
| `/setdescription` | 选 Bot → 问 new description → 成功 | ≤512 |
| `/setcommands` | 选 Bot → 问命令列表 → 发 `command - description` 多行 | ≤100 条；command 1–32；description ≤256 |
| `/revoke` | 选 Bot → （可能直接）返回新 token | 已兼容直接返回/确认按钮两种形态；结果含 `confirmation_required`；旧 token 立即失效 |
| `/deletebot` | 选 Bot → 确认 → 删除并释放 username | 不可恢复；确认按钮文案可能变化 |
| `/cancel` | 取消当前对话 | 代码把 cancel 文本映射为 REJECTED |

### 6.3 限制

- **20 bots/账号；Premium 40/账号**。BotFather 在超限时用自然语言拒绝，代码需
  匹配 “too many bots” 之类文本；匹配不到会超时为 UNAVAILABLE（见
  `docs/BOTFATHER.md` 的 open questions）。
- username 5–32、字母开头、字母/数字/下划线、必须 `bot` 结尾、全局唯一、
  **创建后不可改**；删除 Bot 会释放 username。
- token 形如 `<bot_id>:<secret>`，secret 约 35 位 base64url；token 等同密码。
- 显示名 0–64、description 0–512、about 0–120；commands 100/32/256。
- BotFather 本身可能 FloodWait；不要并发跑多个 bot 命令（已有 flock）。

### 6.4 上线前必须真机确认

1. `/setabouttext` 的准确 prompt/按钮（代码已改用官方命令，仍需真机确认）。
2. `/token` 是只读还是轮换；如果是轮换，`bot token` 必须改名为 `bot rotate-token`
   或加显式确认。
3. `/revoke` 是否有二次确认；代码已同时接受“直接 token”和“确认按钮”，但真机要确认 `confirmation_required`/超时语义。
4. `/deletebot` 的确认按钮文案/层数（当前只处理一轮）。
5. `/mybots` 按钮显示的是 username 还是 display name。
6. 20/40 限制下 BotFather 的拒绝文本是否能被本地识别。

### 6.5 更稳的替代路径（P1 建议）

一旦拿到 token，Bot 的 name/description/about/commands 可以走 Bot API：
`setMyName`、`setMyDescription`、`setMyShortDescription`、`setMyCommands`。
这些是单次 HTTPS 请求，比 BotFather 对话稳定得多；BotFather 只保留
“创建/删除/revoke token/转移所有权”等必须用用户账号的操作。代价是 CLI 需要
处理 token 的读取与 HTTPS 出站；不要把 token 放命令行参数。

## 7. CLI/parser 承诺 vs API 现实

| CLI 表面 | API 现实 | 当前处理 |
|---|---|---|
| `topic edit --icon-color` | `editForumTopic` 无 `icon_color` | 明确 UNSUPPORTED；只能 create 设置 |
| `topic delete --topic-id 1` | General 不可删除 | 明确拒绝 |
| `group delete` 普通群 | 无 DeleteChat | 明确拒绝，提示 leave/迁移 |
| `admins promote --rights/--rank` 普通群 | `editChatAdmin` 只有 bool | 明确拒绝 |
| `chat set-username` 普通群 | 普通群无公开用户名 | 明确拒绝 |
| `messages send --topic 1` | General 按普通消息发，不应带 reply_to | 已改为 reply_to=None |
| `messages send --topic N --reply-to M` | 需要 `InputReplyToMessage(reply_to_msg_id=M, top_msg_id=N)` | 目前互斥，无法同时表达（P1-3） |
| `messages list --search Q --topic N` | `getReplies` 忽略 search | 已改走 `SearchRequest(top_msg_id)` |
| `invite link --request-needed` 普通群 | 进群审批主要面向超级群/频道 | 待真机；失败透传 RPC |
| `bot token` | `/token` 可能轮换 | 仅 `--execute`；结果掩码；待真机确认 |
| `chat set-signatures` | 仅广播频道；`profiles_enabled` 可选 | 已限制频道；profiles 未暴露 |
| `chat set-discussion` | 取消关联要 `InputChannelEmpty` | 只能设置，不能取消 |
| `members ban --days` | `until_date` 必须是未来或 None | 快照中已校验 `--days>0`（见 §3.1） |

## 8. 上线前 blocker 与验证顺序

### 8.0 已完成的真机验证（2026-10-24，含一次性写入验收）

所有者授权后，使用**一次性 Bot + 临时论坛群 + 临时频道**完成全链路验收，
测试对象均已删除；没有向真人或生产群发消息：

- 只读：`auth status`、`chats list`、`chat info`、`messages list/search`、
  `topic list`、`members list`、`admins list`、`invite info`、
  `join-requests list`、`bot list`。
- 写入（临时对象）：建群/建频道、标题/简介/签名、慢速模式、进群审批开关、
  话题 create/edit/close/reopen/pin/unpin/delete、成员 add/ban/unban/remove、
  管理员 promote/demote、邀请链接 create/info/join/revoke、消息 send/edit/
  pin/unpin/delete、mark-read、BotFather create/token/set-*/revoke/delete。
- 幂等：`chat mute me` 用同一 `--request-id` 重放返回 `noop`。
- BotFather `/deletebot` 当前要求发送 `Yes, I am totally sure.`；代码已兼容
  文本确认与按钮确认，并完成真机删除。
- 观察：`/setcommands` 一次 FloodWait 5s、`/deletebot` 一次 FloodWait 6s，
  等待后重试成功；`members add` 到广播频道邀请 Bot 被 Telegram 拒绝
  （Bot 只能当频道管理员），CLI 已加预检。
- 仍开放：`/token` 是否轮换在生产语义上可能因 BotFather 版本而异；
  生产 token 不建议交给无审计的长跑 Agent 静默轮换。

### 8.1 代码/配置 blocker

1. **修完 Lead 的 P0 批次并跑离线测试**：`python -m unittest discover -v`（当前
   56/56 通过）；确认 `messages delete`、topic/General、search、invite join、
   admin defaults、slowmode、partial warnings 回归。
2. **BotFather**：`/setabouttext`、`/revoke` 两种确认形态、`/deletebot` 文本确认均已真机验证；
   `/token` 的 rotation 语义仍可能因版本而异，保留 `token_rotated:null` 警告。
3. **API 凭据**：用 my.telegram.org 自己的 `api_id/api_hash`；不要用默认公开
   macOS 凭据做生产写操作。
4. **错误映射**：至少补 invite/topic/permission 三类；否则 Agent 无法区分
   “链接过期”“无权限”“topic 已删”。
5. **回执/秘密**：确认 receipts 目录 0700、文件 0600，且 BotFather 回执不含完整
   token；邀请链接/消息内容是否落盘要在文档中告知。
6. **代理**：当前 Telethon 未配置 proxy；若部署网络需要代理，先加 `proxy=` 支持。

### 8.2 只读真机验证（安全）

按顺序，全部只读：

```sh
telegram-native auth status
telegram-native chats list --limit 10
telegram-native chat info @<known_test_chat>
telegram-native messages list @<known_test_chat> --limit 5
telegram-native topic list @<known_test_forum> --limit 5
telegram-native invite info --link <known_invite>
```

目的：验证 session、entity 解析、`GetFullChannel/GetFullChat`、`GetForumTopics`、
`CheckChatInvite` 在 layer 229 下没有未知构造函数/字段错误。

### 8.3 写操作验证（测试群/测试频道/一次性 Bot）

1. 建测试超级群（forum）→ 建话题 → 发送到 General 与非 General → 编辑/删除
   自己的消息 → 关闭/重开/置顶/删除非 General 话题。
2. 建测试普通群 → add/remove → leave；确认 `delete` 被拒绝且提示正确。
3. 建测试频道 → 发帖 → 邀请链接 → `invite info` → 用第二测试账号 join。
4. 管理员 promote/demote；确认频道管理员能发帖、普通群 rights 被拒绝。
5. 进群审批：开启 join-request → 第二账号申请 → `join-requests list` → approve/reject；
   再测一次超过 100 条的列表（确认分页缺口）。
6. 慢速模式每个允许值试一次；确认非法值本地拒绝。
7. BotFather：只用一次性小号/一次性 Bot，逐条验证 §6.4；不要对生产 Bot 跑
   `/token`、`/revoke`、`/deletebot`。

### 8.4 失败注入

- 断网/超时：写操作应 `OUTCOME_UNKNOWN`，不得同 request-id 重试。
- FloodWait：应 `THROTTLED` + `retry_after`，不自动重试。
- 权限不足：应 `PERMISSION_DENIED`（补映射后），不是泛化 REJECTED。
- 邀请链接过期/无效：应可区分的错误，不是 REJECTED。

## 9. 开放问题（需要真机/官方确认）

1. `/token` 到底显示当前 token 还是生成新 token？`/revoke` 是否有二次确认？（实现已兼容两种形态，仍需真机确认结果字段是否准确）
2. Telegram 当前生产层是否已支持 Telethon 229 的
   `messages.ChatInviteJoinResult`/`ToggleJoinRequest(apply_to_invites)`？
   官方文档与安装层不一致，必须用只读/测试群验证。
3. `messages.getReplies` 在 forum topic 上是否等同于 topic 历史？搜索必须用
   `SearchRequest(top_msg_id)`，历史读取是否也建议换？
4. `topics_pinned_limit`、`channels_too_much`、`users_too_much` 等当前账号等级下
   的具体数值（可从 help.getConfig 读，但 CLI 未暴露）。
5. 慢速模式是否接受 5 秒（TDLib 最新允许，Pyrogram 文档只列 10 起）？
6. 永久邀请链接 revoke 后是否会生成新的默认链接？`CHAT_INVITE_PERMANENT` 的
   准确触发条件？
7. `request_needed` 是否支持普通群（basic group）？
8. 频道 `delete-dialog --revoke` 是否只清历史、不退群？
9. `auth password` 在跨进程后能否完成 pending 2FA？
10. 普通群 `promote` 的 `add_admins` 权限边界：普通群管理员能否加其他管理员？
11. 公开 username 入群时 `JoinChannelRequest` 返回 `ChatInviteJoinResult` 的
    `.updates.chats` 是否可靠？当前用 `resolved.entity` 兜底。
12. BotFather 超 20/40 的拒绝文本是否稳定可匹配？
13. `GetChatInviteImporters` 的分页（offset_date/offset_user）在 >100 条时是否无重复/漏项？

## 10. 参考

- MTProto methods:
  [channels.toggleForum](https://core.telegram.org/method/channels.toggleForum),
  [channels.createChannel](https://core.telegram.org/method/channels.createChannel),
  [messages.createForumTopic](https://core.telegram.org/method/messages.createForumTopic),
  [messages.editForumTopic](https://core.telegram.org/method/messages.editForumTopic),
  [messages.getForumTopics](https://core.telegram.org/method/messages.getForumTopics),
  [messages.deleteTopicHistory](https://core.telegram.org/method/messages.deleteTopicHistory),
  [channels.editAdmin](https://core.telegram.org/method/channels.editAdmin),
  [channels.editBanned](https://core.telegram.org/method/channels.editBanned),
  [channels.inviteToChannel](https://core.telegram.org/method/channels.inviteToChannel),
  [messages.exportChatInvite](https://core.telegram.org/method/messages.exportChatInvite),
  [messages.editExportedChatInvite](https://core.telegram.org/method/messages.editExportedChatInvite),
  [messages.importChatInvite](https://core.telegram.org/method/messages.importChatInvite),
  [messages.migrateChat](https://core.telegram.org/method/messages.migrateChat)
- API concepts:
  [Forum topics](https://core.telegram.org/api/forum),
  [Admin/banned/default rights](https://core.telegram.org/api/rights),
  [Invite links & join requests](https://core.telegram.org/api/invites),
  [Channels/supergroups/basic groups](https://core.telegram.org/api/channel)
- BotFather/Bot API:
  [BotFather guide](https://core.telegram.org/bots/features#botfather),
  [BotCommand](https://core.telegram.org/bots/api#botcommand),
  [setMyCommands](https://core.telegram.org/bots/api#setmycommands),
  [setMyName](https://core.telegram.org/bots/api#setmyname),
  [setMyDescription](https://core.telegram.org/bots/api#setmydescription),
  [setMyShortDescription](https://core.telegram.org/bots/api#setmyshortdescription)
- Limits/旁证:
  [tginfo/Telegram-Limits #288（20/40 bots）](https://github.com/tginfo/Telegram-Limits/issues/288),
  [Pyrogram set_slow_mode](https://docs.pyrogram.org/api/methods/set_slow_mode),
  [TDLib SetChatSlowModeDelay](https://pub.dev/documentation/tdlib/latest/td_api/SetChatSlowModeDelay-class.html)
- 安装层源码：
  `.venv/lib/python3.15/site-packages/telethon/tl/functions/channels.py`、
  `.venv/lib/python3.15/site-packages/telethon/tl/functions/messages.py`、
  `.venv/lib/python3.15/site-packages/telethon/tl/types/__init__.py`、
  `.venv/lib/python3.15/site-packages/telethon/client/messages.py`。

## 11. 快照 SHA-256（2026-10-24 08:03 ET）

> 注意：08:19–08:27 的一次性验收又改动了 `bots.py`（`/deletebot` 文本确认）、
> `members.py`（频道 Bot 成员预检）和若干文档/测试；下表是 08:03 的历史快照，
> 不是最终哈希。复核代码时以函数名 + `grep -n` 为准。

```
3d44e23664385a3dc5e06f941d182fd2530cdfd7d8a3b264d86a24910944296f  telegram_native/cli.py
3b7aeab5638df752f298a70e201e1479eb269c74fdae91366766fbcfacce5709  telegram_native/client.py
3afba292fb7a645c9eb712a3921e93c8a36fcfea711c83580918901f2e3f7e2b  telegram_native/errors.py
7244ebca6b54913b59d506f2470367da64f87b66cf37b30eca2cfad9a064a389  telegram_native/serialize.py
560605967f1d049815472b253a8a0efbe77f12c8d640f4a286ecedbfea5186d3  telegram_native/commands/groups.py
8dec6fa38d8b1e863b2dde17adf8a2d84edc682682f4261abc2f1910050cc1a8  telegram_native/commands/members.py
747b6edef5706a423874c2192e61d6488837a6d7166aa077022464f46629421e  telegram_native/commands/messages.py
bf2b838ef28de10edcb3f9e2ccee21416c867294f2808195a474012f59b1da49  telegram_native/commands/topics.py
782652e8d0c40002c117d2ff013e21dc72ebedef2918b4089af1fcd84c47e300  telegram_native/commands/chats.py
4b4c7c080ba3c5ccdf34d850d5ef06f7634b162a10c442581f4657d630090a6f  telegram_native/commands/bots.py
19fa978712e974fb3a28598477c35d0ebec5fb61fa28876a3b2cc2054a2a3814  telegram_native/commands/auth.py
6d07951ccf4a07a07902b681c3bebf59340287680ca4495e8114859c55893891  telegram_native/commands/common.py
6710e1a929b3cb9fab3874e1d429fe35b7d1d6cb4e064a87dd817687e00deabb  telegram_native/commands/join_requests.py
```

## 12. 第二轮 P1 新增命令（2026-10-24）

| 域 | 命令 | 关键 API | 状态/限制 |
|---|---|---|---|
| 群迁移 | `group migrate` | `messages.migrateChat` | 真机验证；不可逆，返回新 `peer_id` |
| 邀请链接 | `invite list/edit/delete/importers` | `getExportedChatInvites`, `editExportedChatInvite`, `deleteExportedChatInvite`, `getChatInviteImporters` | 真机验证；delete 先 revoke 再 delete；审批链接不能同时设 usage_limit |
| 媒体 | `messages send-album/download/forward` | `send_file([...])`, `download_media`, `forward_messages` | 真机验证；相册 1–10；下载文件 0600；forward 自动保留相册分组 |
| 全局搜索 | `search global` | `messages.searchGlobal` | 真机验证；服务端搜索，`--type` 过滤 users/groups/broadcasts |
| 联系人 | `contacts list/search/resolve/add/delete` | `contacts.getContacts/search/resolvePhone/resolveUsername/importContacts/deleteContacts` | 读路径真机；add/delete 仅 preview |
| 账号资料 | `account profile/set-name/set-bio/set-username/set-photo` | `account.updateProfile/updateUsername`, `photos.uploadProfilePhoto` | 读真机；写仅 preview |
| 登录设备 | `account devices list/revoke/revoke-others` | `account.getAuthorizations/resetAuthorization` | 列表真机；撤销未执行；当前 CLI 设备拒绝 |
| 网页会话 | `account web-sessions list/revoke/revoke-all` | `account.getWebAuthorizations/resetWebAuthorization(s)` | 列表真机（0 条）；撤销未执行 |

已知限制：

- `invite edit` 的 `usage_limit=0` 无法通过 Telethon 序列化清零（0 被视为“无 flag”）；需要新建链接。
- 审批链接 + usage_limit 组合会被 Telegram 拒绝（`UsageLimitInvalid`），CLI 已本地预检。
- `invite delete` 只能删除 revoked 集合里的链接；CLI 先 revoke 再 delete，并兼容已删除。
- 媒体下载路径默认在 `~/.local/share/telegram-native/downloads/`，目录 0700、文件 0600。
- 设备/IP 信息是私密数据，不要提交或上传。

## 13. 第三轮：评论 / 权限 / 日志 / 话题细粒度 / 批量审批 / 代理多账号

| 域 | 命令 | 关键 API | 状态/限制 |
|---|---|---|---|
| 频道评论 | `comments info/list/reply` | `messages.getDiscussionMessage`, `messages.getReplies`, `sendMessage(reply_to=discussion_msg)` | 真机验证（临时频道+讨论群）；必须已关联讨论群 |
| 默认权限 | `chat set-permissions` | `messages.editChatDefaultBannedRights` | 真机验证；替换式；普通群不支持 |
| 管理员日志 | `admin-log list` | `channels.getAdminLog` | 真机验证（临时群有事件）；`--full` 输出大对象 |
| 话题已读 | `topic read` | `messages.readDiscussion` | 真机验证；`read_max_id` 可选 |
| 隐藏 General | `topic hide` | `messages.editForumTopic(hidden=...)` | 真机验证；主要用于 id=1 |
| 重排置顶 | `topic reorder` | `messages.reorderPinnedForumTopics` | 真机验证；只影响置顶顺序 |
| tabs UI | `forum set-tabs` | `channels.toggleForum(tabs=...)` | 真机验证；要求已开启话题 |
| 批量审批 | `join-requests approve-all/reject-all` | `messages.hideAllChatJoinRequests` | 代码完成，仅 preview；执行会批量放人/拒人，需 `--yes` |
| SOCKS/HTTP 代理 | `TG_PROXY=...` / config `proxy` | Telethon `proxy=` + `python-socks` | 真机验证 SOCKS5 127.0.0.1:7890 |
| 多账号 | `--account NAME` / `config accounts` | 独立 session + `accounts.json` | 命名 session 真机 smoke；真实第二账号登录未测 |

已知限制：

- `admin-log` 默认只给 `action_type` + 截断 summary；`--full` 才输出完整 action。
- `comments reply` 发到关联讨论群的话题线程；频道未关联讨论群时返回 `UNSUPPORTED`。
- 批量审批是单次 `hideAllChatJoinRequests`，没有逐用户部分失败回执；操作前请 preview。
- `topic hide` 的 `hidden` 主要对 General 有意义，其他 topic 可能被服务端忽略。
- 代理凭据只放 `TG_PROXY` 或 0600 配置文件，不要写进命令行/日志。
- 多账号注册表只记录元数据；移除条目不会删除 session 文件。
