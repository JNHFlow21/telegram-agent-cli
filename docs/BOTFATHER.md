# BotFather operations (`telegram-native bot ...`)

This document describes the `bot` command group implemented in
`telegram_native/commands/bots.py`. BotFather is a conversational bot with
inline keyboards, so each command is a short scripted conversation over the
normal user session. No Telegram Desktop GUI automation is involved.

> Current status: the implementation and tests are offline. No command in this
> document has been executed against a real @BotFather from this repository.
> The prompts/buttons below are BotFather's observed/expected wording; label
> matching is deliberately tolerant because BotFather changes wording.

## Safety model

* Every command that can contact BotFather defaults to a **preview** and only
  sends anything when `--execute` is passed.
* Mutations additionally use the shared receipt/`--request-id` gate from
  `commands/common.py`. Reusing an executed request id replays the stored
  result (`outcome=noop`). Reusing an id whose outcome is unknown is blocked
  with `OUTCOME_UNKNOWN`; inspect the bot first, then use a **new** request id.
* `revoke` and `delete` are destructive: they also require `--yes` or
  `--confirm <username>` (the bot username without `@`).
* Every BotFather conversation holds `data_dir/botfather.lock` (an `flock`),
  so two CLI processes cannot interleave messages in the same chat.
* Full tokens are only written to `data_dir/bots/<username>.json` with mode
  `0600` (the directory is `0700`). Envelopes, receipts and error details carry
  only a masked token. The one exception is the explicit
  `bot token --show-secret`, which returns the full token in its `result`;
  even then the receipt stores only the masked form.
* Preview never connects to Telegram, so it cannot send anything to BotFather.

## Token file format

Path: `$TG_DATA_DIR` (default `~/.local/share/telegram-native`) +
`bots/<lowercased-username>.json`, mode `0600`.

```json
{
  "username": "my_test_bot",
  "bot_id": 123456789,
  "token": "123456789:AA...",
  "source": "create",
  "name": "My Test Bot",
  "created_at": "2026-10-24T12:00:00Z",
  "updated_at": "2026-10-24T12:00:00Z"
}
```

`source` is `create`, `token` or `revoke`. `created_at` is preserved when a
token is refreshed. The record is written atomically (temporary file +
`os.replace`) while holding the BotFather lock.

Masking keeps the numeric bot id and the first/last four secret characters,
for example `123456789:A1b2…p6Q7`. The full token is never included in an
envelope, receipt, error message or log.

## Commands

| Command | Gate | Remote effect |
| --- | --- | --- |
| `bot create --name NAME --username USER` | preview / `--execute` | `/newbot` creates a bot and returns its token |
| `bot list` | read-only | `/mybots`; lists bots from the inline buttons |
| `bot token --username USER [--show-secret]` | preview / `--execute` | `/token`; stores the token, prints masked unless `--show-secret`; may rotate the token |
| `bot set-name --username USER --name NAME` | preview / `--execute` | `/setname` |
| `bot set-about --username USER --about TEXT` | preview / `--execute` | `/setabouttext` |
| `bot set-description --username USER --description TEXT` | preview / `--execute` | `/setdescription` |
| `bot set-commands --username USER --commands-file FILE` | preview / `--execute` | `/setcommands` |
| `bot revoke --username USER --yes` | preview / `--execute` + confirm | `/revoke`; old token dies, new token stored |
| `bot delete --username USER --yes` | preview / `--execute` + confirm | `/deletebot`; local token file is removed |

Examples:

```bash
# Offline previews (default)
telegram-native bot create --name "Demo Bot" --username demo_bot
telegram-native bot token --username demo_bot
telegram-native bot set-name --username demo_bot --name "New Name"

# Explicit execution, one fresh request id per real change
telegram-native bot create --name "Demo Bot" --username demo_bot \
  --execute --request-id create-demo-20261024

telegram-native bot revoke --username demo_bot --yes \
  --execute --request-id revoke-demo-20261024

telegram-native bot delete --username demo_bot --yes \
  --execute --request-id delete-demo-20261024
```

`bot list` has no `--execute` flag and prints a read-only envelope
(`outcome=readonly`).

## Conversation engine

For each conversation the module:

1. takes `botfather.lock`;
2. calls `client.send_message("BotFather", "...")` once;
3. polls `client.get_messages("BotFather", min_id=<last seen id>, limit=50)`;
4. treats a changed `edit_date`/text/button fingerprint on the current message
   as a fresh reply, because BotFather often edits the message whose button was
   clicked instead of sending a new one;
5. clicks inline buttons through `message.buttons[i][j].click()`, choosing by
   case-insensitive label match;
6. waits at most `BOTFATHER_TIMEOUT` (30 s, module constant) and polls every
   `BOTFATHER_POLL_INTERVAL` (1 s);
7. releases the lock on exit.

Button labels are matched by exact normalized username first, then by
case-insensitive substring. If several labels match, the error is
`TARGET_AMBIGUOUS`; if none match, `NOT_FOUND`. Both errors include
`available_buttons` with the labels BotFather actually sent, so an operator can
adapt without guessing.

Replies containing cancel/cancelled (including BotFather's `/cancel` reset)
raise `REJECTED`. Known BotFather refusals (`already taken`, invalid username,
`too many bots`, etc.) also raise `REJECTED` with a token-masked
`botfather_message` in `details`.

## Flow details

### Create: `bot create`

1. Send `/newbot`.
2. Wait for the name prompt (`name` / `call it`).
3. Send the display name.
4. Wait for the username prompt (`username` / `must end in`).
5. Send the username (without `@`).
6. Wait until a reply contains a token matching
   `\b\d{6,12}:[A-Za-z0-9_-]{30,}\b`.
7. Store the token (`source=create`), derive the numeric bot id from the token
   prefix, and return `bot.id`, `bot.username`, `token_masked`, `token_file`.

If BotFather replies that the username is taken or invalid, no token file is
written and the command fails with `REJECTED`. A timeout after a value has
been submitted is `OUTCOME_UNKNOWN` (the bot may or may not exist), so the same
request id will not send any step again.

### List: `bot list`

1. Send `/mybots`.
2. Wait for a reply with inline buttons, or for "no bots" wording.
3. Extract usernames from button labels with
   `@?([A-Za-z][A-Za-z0-9_]{1,28}bot)\b` (case-insensitive), deduplicated in
   order.
4. Return `count`, `bots: [{username, label}]`, `usernames`,
   `available_buttons`.

If the account has no bots, the result is an empty list. Buttons whose labels
contain no parseable username produce `NOT_FOUND` with the labels attached.

### Token: `bot token`

1. Send `/token`.
2. Wait for the bot-selection buttons and click the button matching
   `--username`.
3. Wait for a reply containing a token.
4. Store the full token (`source=token`) with mode `0600`.
5. Return `token_masked`, `bot_id`, `token_file`, and `token` (masked by
   default, full only when `--show-secret` is present).

**Rotation ambiguity:** BotFather documentation and community guides disagree
on whether `/token` displays the current token or generates a new one. The
selected bot may also rotate the token as a side effect. The implementation
therefore treats the wait after the selection click as potentially mutating:
a timeout there is `OUTCOME_UNKNOWN`, not `UNAVAILABLE`. When BotFather's reply
explicitly mentions a revoked/new/regenerated/previous token, the result has
`token_rotated: true`. When it does not say, `token_rotated` is `null` and a
`rotation_note` explains that rotation is unconfirmed — never assume the
command was a pure read.

The receipt for this command stores only the masked result. A replay with
`--show-secret` reads the full token back from the local 0600 file.

### Set name / about / description

All three follow the same shape:

1. Send `/setname`, `/setabouttext` or `/setdescription`.
2. Click the button for `--username`.
3. Wait for the corresponding prompt:
   * set-name: text containing `new name`;
   * set-about: text containing both `about` and `new`;
   * set-description: text containing both `description` and `new`.
4. Send the new value.
5. Wait for a success-style reply (`success`, `done`, `updated`, `changed`,
   `saved`, `okay`, ...).

Values are validated locally before any network contact:

| Field | Limit |
| --- | --- |
| Bot display name | 1–64 characters |
| About | 1–120 characters |
| Description | 1–512 characters |

Empty values are rejected by the CLI (`VALIDATION`). Clearing via BotFather's
`/empty` is not implemented yet (see open questions).

### Set commands: `bot set-commands`

The file is UTF-8, one command per line:

```
start - Start the bot
help - Show help
```

Blank lines and lines starting with `#` are ignored. The separator must be
` - ` (space, hyphen, space). The command name is validated as 1–32
lowercase letters, digits or underscores (`[a-z0-9_]{1,32}`); a leading slash
is stripped. Descriptions must be non-empty and at most 256 characters. Duplicate command names are rejected. At most 100
commands are accepted (BotFather's documented limit is 100).

The formatted body sent to BotFather is exactly:

```
start - Start the bot
help - Show help
```

The command waits for a prompt containing `list of commands`, sends the body,
then waits for the success reply.

### Revoke: `bot revoke`

Two BotFather shapes are supported because guides disagree on whether
`/revoke` asks for confirmation:

1. Send `/revoke`.
2. Click the button for `--username`. This click may already rotate the token.
3. Wait for either:
   * a reply containing the new token (direct shape), or
   * a confirmation message with buttons; click the one containing
     `yes`/`revoke` and not `cancel`/`no`/`back`, then wait for the token.
4. Store it (`source=revoke`, preserving `created_at`).
5. Return `revoked=true`, `token_rotated=true`, `confirmation_required`
   (`true` for the confirm shape, `false` for the direct shape), and the
   masked token.

Destructive gate: `--yes` or `--confirm <username>` is required.
The old token can be invalid immediately after the selection click, so **any**
timeout after that click is `OUTCOME_UNKNOWN`, never a blind retry.

### Delete: `bot delete`

1. Send `/deletebot`.
2. Click the button for `--username`.
3. Wait for the confirmation message. Current BotFather may either show a
   button containing `yes`/`delete` (click it) or ask the user to **send** the
   exact sentence `Yes, I am totally sure.` (send it). Both shapes are handled.
4. Wait for a success-style reply (`deleted`, `removed`, `success`, `done`).
5. Delete the local `bots/<username>.json` file if present.

Destructive gate: `--yes` or `--confirm <username>` is required. A timeout
after the confirmation click is `OUTCOME_UNKNOWN`; the bot may already be gone.
A local unlink failure does not turn a confirmed remote deletion into an
unknown outcome; `token_file_removed` reports what happened locally.

## Limits and username rules

* **20 bots per account** (Telegram/BotFather limit). The CLI does not try to
  count them in advance; BotFather refuses `/newbot` when the limit is reached,
  which surfaces as `REJECTED`/`OUTCOME_UNKNOWN` depending on the reply.
* Bot username: 5–32 characters, starts with a letter, only ASCII letters,
  digits and underscores, and must end in `bot` (case-insensitive). Enforced
  locally. The `@` prefix is accepted and stripped.
* Bot display name: 1–64 characters.
* About text: 1–120 characters.
* Description: 1–512 characters.
* Commands: up to 100; command token 1–32 lowercase letters/digits/underscore;
  description up to 256 characters.
* BotFather messages can be edited; the engine treats an edited message with a
  new fingerprint as a fresh reply.

## Failure semantics

All errors use the stable `tg.v1` codes.

| Code | Meaning in this module | Retriable? |
| --- | --- | --- |
| `VALIDATION` | Bad username/name/about/description/commands file, missing file, bad line, too many commands | Fix arguments; no Telegram state changed |
| `NOT_FOUND` | No matching bot button, no token in reply, no bots under the account | Safe to re-read; use a fresh request id if executing again |
| `TARGET_AMBIGUOUS` | Several buttons match the username or confirmation keywords | Use a more precise username; no click sent |
| `REJECTED` | BotFather refused the step, or replied cancelled | No mutation; inspect `details.botfather_message` |
| `BLOCKED` | Destructive command without `--yes`/matching `--confirm` | Add confirmation and retry |
| `THROTTLED` | Telegram `FloodWaitError`; the CLI does not sleep/retry automatically | Wait `details.retry_after`, then decide |
| `UNAVAILABLE` | Timeout before any value/confirmation was submitted, or a connection error | Safe in principle, but the write gate records `unknown` and blocks the same request id |
| `OUTCOME_UNKNOWN` | Timeout after a value/confirmation click, or after selecting a bot for `/token`/`/revoke` (the token may already have rotated); the remote side may have applied it | **Do not blind-retry.** Inspect the bot/token, then use a new request id |
| `INTERNAL` | Unexpected exception mapped by `errors.map_exception` | Investigate |

Notes:

* The timeout for each wait is 30 seconds by default. `details.step` identifies
  the exact stage (`newbot/name`, `token/result`,
  `revoke/result-or-confirm`, `delete/confirm`, ...).
* `details.last_reply` (when present) is BotFather's last message with any token
  pattern masked.
* A timeout after the initial command but before submitting a value is
  `UNAVAILABLE` (retriable), but the shared write gate still stores it as
  `unknown`; a deliberate new request id is required to send again.
* The engine never retries a mutating step on its own.

## Tests

The module is covered by offline fakes only; no test opens a Telegram
connection.

```bash
cd telegram-native
.venv/bin/python -m unittest tests.test_bots -v
```

Coverage includes: token parsing/masking, create success, username taken,
initial and post-value timeouts, no duplicate send on a reused request id,
`/mybots` button parsing (including case-insensitive labels and empty accounts),
token storage/masking/`--show-secret` receipt hygiene, edited-message flows,
command-file formatting, cancellation, destructive confirmation, both
`/revoke` shapes (direct token and confirmation button), token-rotation
detection, post-selection `OUTCOME_UNKNOWN` timeouts, delete plus local
token-file cleanup, and `FloodWaitError` mapping.

## Live verification (2026-10-24)

A disposable bot was created, edited, revoked and deleted on the owner's
account (no other bot or group was touched):

- `/newbot` create: **verified**.
- `/token`: **verified** (masked result; full token stored in a 0600 file).
- `/setname`, `/setabouttext`, `/setdescription`, `/setcommands`: **verified**.
- `/revoke`: **verified**; result reports `token_rotated`.
- `/deletebot`: current BotFather asks the user to **send**
  `Yes, I am totally sure.` rather than click a button. The flow now handles
  both text and button confirmations and was verified end-to-end.
- One `/setcommands` attempt hit FloodWait 5 s; waiting and retrying with a new
  request id succeeded. `/deletebot` hit FloodWait 6 s; retry after waiting
  succeeded.

Still open / not verified:

1. `/empty` clearing for about/description (needs a small `--clear` CLI change).
2. Whether current `/token` ever rotates the token (result field
   `token_rotated` may be `null` when wording is ambiguous).
3. 2FA/password-protected BotFather actions and the exact 20-bot refusal wording.
4. `/mybots` labels that show display names instead of usernames.
5. Timeout tuning: 30 s is conservative; a mutating step that times out becomes
   `OUTCOME_UNKNOWN` and must not be blindly retried.

## References consulted

- Telegram Bot Features — Edit Bots: <https://core.telegram.org/bots/features#edit-bots>
  (BotFather command list uses `/setabouttext` for the about text).
- Bitrix24 “Connect Telegram bot”:
  <https://helpdesk.bitrix24.com/open/17467872/#1>
  (describes `/token` as showing the current token and `/revoke` as creating a
  new one).
- Livecaller Telegram integration guide:
  <https://docs.livecaller.io/livecaller/services/integrations/telegram>
  (describes `/mybots` → API Token → Revoke current token with a confirmation
  step).

These external descriptions conflict on the exact `/token` and `/revoke`
shapes, which is why the implementation accepts both and reports
`token_rotated`/`confirmation_required` explicitly instead of assuming one.
