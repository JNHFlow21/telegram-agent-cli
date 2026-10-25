"""Local configuration and private runtime paths."""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import unquote, urlparse

# Telegram for macOS (Keepcoder, open source) publishes these credentials in
# packages/ApiCredentials/Sources/ApiCredentials/Config.swift. They identify the
# client application, not the user. A user may override them with a personal
# my.telegram.org application via env or config.
DEFAULT_API_ID = 9
DEFAULT_API_HASH = "3975f648bb682ee889f35483bc618d1c"  # gitleaks:allow (public client credential)

CONFIG_DIR = Path(os.environ.get("TG_CONFIG_DIR", Path.home() / ".config" / "telegram-native"))
DATA_DIR = Path(os.environ.get("TG_DATA_DIR", Path.home() / ".local" / "share" / "telegram-native"))
CONFIG_FILE = CONFIG_DIR / "config.json"
ACCOUNTS_FILE = DATA_DIR / "accounts.json"


@dataclass(frozen=True)
class Settings:
    api_id: int
    api_hash: str
    session_path: Path
    data_dir: Path
    config_dir: Path
    proxy: tuple | None = None

    @property
    def receipts_dir(self) -> Path:
        return self.data_dir / "receipts"

    @property
    def bots_dir(self) -> Path:
        return self.data_dir / "bots"

    @property
    def lock_path(self) -> Path:
        return self.data_dir / "botfather.lock"


def _read_config() -> dict:
    if not CONFIG_FILE.exists():
        return {}
    try:
        raw = json.loads(CONFIG_FILE.read_text("utf-8"))
    except (OSError, ValueError):
        return {}
    return raw if isinstance(raw, dict) else {}


def parse_proxy(value: str | None) -> tuple | None:
    """Parse socks5://user:pass@host:port into Telethon's proxy tuple."""
    if not value:
        return None
    parsed = urlparse(value)
    if parsed.scheme not in {"socks5", "socks4", "http"}:
        raise ValueError("proxy scheme must be socks5, socks4 or http")
    if not parsed.hostname or not parsed.port:
        raise ValueError("proxy must include host and port")
    return (
        parsed.scheme,
        parsed.hostname,
        parsed.port,
        unquote(parsed.username) if parsed.username else None,
        unquote(parsed.password) if parsed.password else None,
    )


def load_settings() -> Settings:
    raw = _read_config()
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    DATA_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        os.chmod(DATA_DIR, 0o700)
    except OSError:
        pass

    api_id = int(os.environ.get("TG_API_ID") or raw.get("api_id") or DEFAULT_API_ID)
    api_hash = str(os.environ.get("TG_API_HASH") or raw.get("api_hash") or DEFAULT_API_HASH)
    session = Path(
        os.environ.get("TG_SESSION")
        or raw.get("session")
        or (DATA_DIR / "telegram-native.session")
    ).expanduser()
    session.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    proxy = parse_proxy(os.environ.get("TG_PROXY") or raw.get("proxy"))
    return Settings(
        api_id=api_id,
        api_hash=api_hash,
        session_path=session,
        data_dir=DATA_DIR,
        config_dir=CONFIG_DIR,
        proxy=proxy,
    )


def save_config(values: dict) -> Path:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)
    current = _read_config()
    current.update(values)
    fd = os.open(CONFIG_FILE, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(current, fh, ensure_ascii=False, indent=2, sort_keys=True)
        fh.write("\n")
    return CONFIG_FILE


def mask_phone(phone: str | None) -> str | None:
    if not phone:
        return None
    if len(phone) <= 4:
        return "*" * len(phone)
    return phone[:2] + "*" * max(0, len(phone) - 5) + phone[-3:]


def safe_account_name(name: str) -> str:
    value = (name or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,31}", value):
        raise ValueError("account name must be 1-32 chars: letters, digits, _ or -")
    return value


def account_session_path(name: str) -> Path:
    safe = safe_account_name(name)
    root = DATA_DIR / "accounts"
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    return root / f"{safe}.session"


def load_accounts() -> dict:
    if not ACCOUNTS_FILE.exists():
        return {}
    try:
        raw = json.loads(ACCOUNTS_FILE.read_text("utf-8"))
    except (OSError, ValueError):
        return {}
    return raw if isinstance(raw, dict) else {}


def save_account(name: str, session_path: Path, meta: dict) -> dict:
    safe = safe_account_name(name)
    data = load_accounts()
    data[safe] = {
        **{k: v for k, v in meta.items() if v is not None},
        "session": str(session_path),
    }
    DATA_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)
    tmp = ACCOUNTS_FILE.with_suffix(".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2, sort_keys=True)
        fh.write("\n")
    os.replace(tmp, ACCOUNTS_FILE)
    return data


def remove_account(name: str) -> bool:
    safe = safe_account_name(name)
    data = load_accounts()
    if safe not in data:
        return False
    data.pop(safe, None)
    tmp = ACCOUNTS_FILE.with_suffix(".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2, sort_keys=True)
        fh.write("\n")
    os.replace(tmp, ACCOUNTS_FILE)
    return True
