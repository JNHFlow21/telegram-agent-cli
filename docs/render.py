"""Renders docs/demo.gif from FAKE demo data: python3 docs/render.py

No Telegram connection is made. Every envelope below is hand-written sample
output (fake ids, titles and usernames) in the real tg.v1 shape. Each scene
becomes an HTML frame, headless Chrome screenshots it and ffmpeg joins the
frames into docs/demo.gif. Needs Google Chrome and ffmpeg.
"""
import html
import json
import os
import re
import subprocess
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CHROME = os.environ.get("CHROME", "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")
W, H = 1180, 400

ASK = "Claude Code ▸ set up a forum group for the launch, add a Releases topic"

SCENES = [
    (
        "telegram-native chats list --type group --limit 2",
        {"schema": "tg.v1", "command": "chats list", "ok": True, "outcome": "readonly",
         "data": {"count": 2, "items": [
             {"peer_id": -1009876543210, "kind": "group", "title": "Design Guild", "is_forum": True, "unread_count": 3},
             {"peer_id": -1001122334455, "kind": "group", "title": "Weekend Hikers", "is_forum": False, "unread_count": 0}]},
         "error": None},
    ),
    (
        'telegram-native group create --title "Launch Crew" --forum',
        {"schema": "tg.v1", "command": "group create", "ok": True, "outcome": "preview",
         "data": {"status": "preview", "params": {"title": "Launch Crew", "forum": True, "users": []},
                  "requires_execute": True},
         "meta": {"request_id": "group-create-7f3a2c"}},
    ),
    (
        'telegram-native group create --title "Launch Crew" --forum --execute',
        {"schema": "tg.v1", "command": "group create", "ok": True, "outcome": "executed",
         "data": {"peer_id": -1001234567890, "kind": "group", "title": "Launch Crew", "is_forum": True,
                  "creator": True},
         "error": None},
    ),
    (
        'telegram-native topic create -1001234567890 --title "Releases" --execute',
        {"schema": "tg.v1", "command": "topic create", "ok": True, "outcome": "executed",
         "data": {"chat": {"peer_id": -1001234567890, "title": "Launch Crew", "is_forum": True},
                  "topic_id": 2, "title": "Releases"}, "error": None},
    ),
    (
        "telegram-native members ban -1001234567890 --users @spam_bot_42 --execute",
        {"schema": "tg.v1", "command": "members ban", "ok": False, "outcome": "noop", "data": None,
         "error": {"code": "BLOCKED", "retriable": False,
                   "details": {"required_confirm": "@spam_bot_42"}}},
    ),
]

TOKEN = re.compile(r'("(?:[^"\\]|\\.)*")(\s*:)?|\b(true|false|null)\b|(-?\d+)')
OUTCOME_COLOR = {"readonly": "#7dcfff", "preview": "#e0af68", "executed": "#9ece6a", "noop": "#f7768e"}


def colorize(text):
    out, pos = [], 0
    for m in TOKEN.finditer(text):
        out.append(html.escape(text[pos:m.start()]))
        s, colon, lit, num = m.groups()
        if s and colon:
            out.append(f'<span style="color:#7aa2f7">{html.escape(s)}</span>{colon}')
        elif s:
            color = OUTCOME_COLOR.get(s.strip('"'), "#c3a6ff" if s == '"BLOCKED"' else "#e6c07b")
            if s == '"BLOCKED"':
                color = "#f7768e"
            out.append(f'<span style="color:{color}">{html.escape(s)}</span>')
        elif lit:
            color = {"true": "#9ece6a", "false": "#f7768e"}.get(lit, "#787c99")
            out.append(f'<span style="color:{color}">{lit}</span>')
        else:
            out.append(f'<span style="color:#ff9e64">{num}</span>')
        pos = m.end()
    out.append(html.escape(text[pos:]))
    return "".join(out)


def prompt(cmd):
    return (f'<span style="color:#9ece6a">❯</span> <span style="color:#c0caf5">{html.escape(cmd)}</span>')


PAGE = """<!doctype html><meta charset="utf-8"><style>
html,body{{margin:0;background:#16161e}}
.win{{margin:14px;height:{h}px;border-radius:12px;background:#1a1b26;box-shadow:0 0 0 1px #2a2b3d;overflow:hidden}}
.bar{{height:32px;display:flex;gap:8px;align-items:center;padding-left:14px;color:#565f89;font:13px -apple-system,sans-serif}}
.bar i{{width:12px;height:12px;border-radius:50%;display:block}}
.bar span{{margin-left:auto;margin-right:auto;transform:translateX(-30px)}}
pre{{margin:0;padding:10px 22px;font:15px/1.5 "SF Mono",Menlo,monospace;color:#a9b1d6;white-space:pre-wrap}}
.ask{{color:#bb9af7}}</style>
<div class="win"><div class="bar"><i style="background:#ff5f57"></i><i style="background:#febc2e"></i><i style="background:#28c840"></i><span>telegram-native — fake demo data</span></div>
<pre><span class="ask"># {ask}</span>

{body}</pre></div>"""


def pretty(env):
    """Top-level keys one per line; list items one per line; the rest compact."""
    lines = ["{"]
    for i, (key, value) in enumerate(env.items()):
        comma = "," if i < len(env) - 1 else ""
        if isinstance(value, dict) and isinstance(value.get("items"), list):
            head = {k: v for k, v in value.items() if k != "items"}
            lines.append(f'  "{key}": {json.dumps(head, ensure_ascii=False)[:-1]}, "items": [')
            for j, item in enumerate(value["items"]):
                sep = "," if j < len(value["items"]) - 1 else ""
                lines.append("    " + json.dumps(item, ensure_ascii=False) + sep)
            lines.append("  ]}" + comma)
        else:
            lines.append(f'  "{key}": {json.dumps(value, ensure_ascii=False)}{comma}')
    lines.append("}")
    return "\n".join(lines)


def frames():
    """Yield (body_html, seconds). Each scene: command typed, then its output."""
    for cmd, env in SCENES:
        yield prompt(cmd) + '<span style="background:#c0caf5"> </span>', 1.1
        yield prompt(cmd) + "\n" + colorize(pretty(env)), 2.8


def main():
    tmp = tempfile.mkdtemp()
    concat = []
    for i, (body, secs) in enumerate(frames()):
        src = os.path.join(tmp, f"{i:02d}.html")
        with open(src, "w", encoding="utf-8") as fh:
            fh.write(PAGE.format(h=H - 28, ask=html.escape(ASK), body=body))
        png = os.path.join(tmp, f"{i:02d}.png")
        subprocess.run([CHROME, "--headless", "--disable-gpu", "--hide-scrollbars", "--force-device-scale-factor=1",
                        f"--window-size={W},{H}", f"--screenshot={png}", f"file://{src}"],
                       check=True, capture_output=True)
        concat += [f"file '{png}'", f"duration {secs}"]
    concat.append(f"file '{png}'")  # concat demuxer needs the last frame repeated
    listing = os.path.join(tmp, "frames.txt")
    with open(listing, "w") as fh:
        fh.write("\n".join(concat) + "\n")
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", listing, "-vf",
                    "fps=10,split[a][b];[a]palettegen=max_colors=96:stats_mode=diff[p];[b][p]paletteuse=dither=none",
                    "-loop", "0", os.path.join(ROOT, "docs", "demo.gif")], check=True)


if __name__ == "__main__":
    main()
