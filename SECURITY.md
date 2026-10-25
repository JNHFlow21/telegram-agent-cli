# Security policy

Only the latest `main` is supported.

Please report vulnerabilities privately via
[GitHub private vulnerability reporting](https://github.com/JNHFlow21/telegram-agent-cli/security/advisories/new)
rather than a public issue.

telegram-native stores your Telegram session under
`~/.local/share/telegram-native/` (override with `TG_DATA_DIR`). Anyone with
that file can act as your account — keep it private and never share it in
issues or logs.
