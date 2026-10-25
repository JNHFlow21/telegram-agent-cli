#!/usr/bin/env python3
"""Install the telegram-native launcher into ~/.local/bin (idempotent).

  python3 install_global.py               # launcher only
  python3 install_global.py --with-skill  # also install the agent skill for
                                          # Claude Code (~/.claude/skills) and
                                          # Codex / other agents (~/.agents/skills)

Nothing is deleted: an existing non-symlink launcher or an existing skill
folder is moved to a timestamped backup first.
"""
from __future__ import annotations

import argparse
import filecmp
import os
import shutil
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SOURCE = ROOT / "bin" / "telegram-native"
SKILL = ROOT / "skills" / "telegram-native"


def _free(path: Path) -> Path:
    """A timestamped backup path that does not exist yet."""
    base = f"{path}.bak-{time.strftime('%Y%m%d-%H%M%S')}"
    candidate, n = Path(base), 1
    while candidate.exists():
        candidate, n = Path(f"{base}-{n}"), n + 1
    return candidate


def _same_skill(dest: Path) -> bool:
    cmp = filecmp.dircmp(SKILL, dest)
    return not (cmp.left_only or cmp.right_only or cmp.diff_files or cmp.funny_files or cmp.subdirs)


def install_launcher(home: Path) -> Path:
    target = home / ".local" / "bin" / "telegram-native"
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.is_symlink():
        target.unlink()  # a previous launcher symlink; safe to replace
    elif target.exists():
        backup = _free(target)
        target.rename(backup)
        print(f"backed up existing {target} -> {backup}")
    os.symlink(SOURCE, target)
    return target


def install_skill(home: Path) -> list[Path]:
    installed = []
    backups = home / ".local" / "share" / "telegram-native" / "backups"
    for agent, skills_dir in (("claude", home / ".claude" / "skills"), ("agents", home / ".agents" / "skills")):
        dest = skills_dir / "telegram-native"
        if dest.is_symlink():
            dest.unlink()
        elif dest.is_dir() and _same_skill(dest):
            shutil.rmtree(dest)  # our own earlier copy, unchanged
        elif dest.exists():
            # Back up outside the skills folder so agents do not load a duplicate skill.
            backups.mkdir(parents=True, exist_ok=True)
            backup = _free(backups / f"skill-{agent}")
            shutil.move(str(dest), backup)
            print(f"backed up existing {dest} -> {backup}")
        skills_dir.mkdir(parents=True, exist_ok=True)
        shutil.copytree(SKILL, dest)
        installed.append(dest)
    return installed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--with-skill", action="store_true", help="also install the agent skill")
    args = parser.parse_args(argv)
    if not (ROOT / ".venv" / "bin" / "python").is_file():
        raise SystemExit("missing .venv; run: python3 -m venv .venv && .venv/bin/pip install -r requirements.txt")
    home = Path.home()
    print(install_launcher(home))
    if args.with_skill:
        for path in install_skill(home):
            print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
