"""Claude Desktop + Google Drive — one-click automation.

Uses Claude Desktop Connectors (Drive), not Claude Code CLI.
Flow: copy prompt → open Claude → AppleScript paste/send → poll CSV → import.
"""

from __future__ import annotations

import shutil
import subprocess
import time
from pathlib import Path
from typing import Any

from app.config import Config, ROOT

EXPORTS = ROOT / "exports"
OUT_CSV = EXPORTS / "from_claude.csv"
DOWNLOADS = Path.home() / "Downloads"


def claude_desktop_available() -> bool:
    return Path("/Applications/Claude.app").exists()


def build_refresh_prompt(cfg: Config) -> str:
    EXPORTS.mkdir(parents=True, exist_ok=True)
    sheet_id = cfg.spreadsheet_id
    url = f"https://docs.google.com/spreadsheets/d/{sheet_id}/edit"
    tab = cfg.worksheet_name or "Deliveries"
    # Also ask for Downloads copy — Claude Desktop sometimes prefers that
    dl = DOWNLOADS / "from_claude.csv"
    return f"""Use Google Drive. Be brief. No explanations.

Open Sheet: {url}
Tab: {tab} (or main data tab)

Export FULL sheet as CSV with header. Fill-down blank amg_id/account_name from previous row.

Write the file to BOTH of these paths (overwrite OK):
1) {OUT_CSV}
2) {dl}

Columns at least: amg_id,account_name,channel_id,distribution_id,status,remarks

Reply with ONLY: OK rows=<N>
"""


def build_push_prompt(cfg: Config, full_csv: Path) -> str:
    url = f"https://docs.google.com/spreadsheets/d/{cfg.spreadsheet_id}/edit"
    tab = cfg.worksheet_name or "Deliveries"
    return f"""Use Google Drive. Be brief.

Update Sheet {url} tab "{tab}": replace contents with CSV at:
{full_csv}

Reply ONLY: OK updated=<N>
"""


def copy_to_clipboard(text: str) -> bool:
    try:
        return subprocess.run(["pbcopy"], input=text.encode("utf-8")).returncode == 0
    except Exception:
        return False


def open_claude_desktop() -> bool:
    try:
        subprocess.run(["open", "-a", "Claude"], check=False)
        return True
    except Exception:
        return False


def paste_and_send_in_claude(*, new_chat: bool = True) -> dict[str, Any]:
    """macOS Accessibility: activate Claude, paste clipboard, press Return."""
    new_chat_block = """
      keystroke "n" using command down
      delay 0.8
""" if new_chat else ""
    script = f"""
    tell application "Claude" to activate
    delay 1.8
    tell application "System Events"
      if not (exists process "Claude") then error "Claude process not found"
      tell process "Claude"
        set frontmost to true
        delay 0.4
        {new_chat_block}
        keystroke "v" using command down
        delay 0.5
        keystroke return
      end tell
    end tell
    return "sent"
    """
    proc = subprocess.run(
        ["osascript", "-e", script],
        capture_output=True,
        text=True,
        timeout=30,
    )
    return {
        "ok": proc.returncode == 0,
        "stdout": (proc.stdout or "").strip(),
        "stderr": (proc.stderr or "").strip(),
    }


def find_ready_csv() -> Path | None:
    candidates = [OUT_CSV, DOWNLOADS / "from_claude.csv"]
    # newest matching download as fallback
    try:
        for p in sorted(DOWNLOADS.glob("*.csv"), key=lambda x: x.stat().st_mtime, reverse=True)[:8]:
            name = p.name.lower()
            if "from_claude" in name or "delivery" in name and "mig" in name:
                candidates.append(p)
    except Exception:
        pass
    best: Path | None = None
    best_mtime = 0.0
    now = time.time()
    for p in candidates:
        try:
            if p.is_file() and p.stat().st_size > 40:
                # prefer files written in the last 10 minutes
                m = p.stat().st_mtime
                if now - m < 600 and m >= best_mtime:
                    best = p
                    best_mtime = m
        except Exception:
            continue
    return best


def poll_refresh_csv(*, timeout_sec: float = 0) -> dict[str, Any]:
    if timeout_sec <= 0:
        p = find_ready_csv()
        if p:
            return {"ready": True, "path": str(p), "bytes": p.stat().st_size}
        return {"ready": False, "path": str(OUT_CSV)}
    deadline = time.time() + timeout_sec
    while time.time() < deadline:
        p = find_ready_csv()
        if p:
            # ensure it's newer than ~start — caller may unlink first
            return {"ready": True, "path": str(p), "bytes": p.stat().st_size}
        time.sleep(2)
    return {"ready": False, "path": str(OUT_CSV)}


def run_auto_refresh(cfg: Config, *, wait_sec: int = 180) -> dict[str, Any]:
    """One-shot: prompt → Claude Desktop paste/send → wait for CSV path."""
    EXPORTS.mkdir(parents=True, exist_ok=True)
    for p in (OUT_CSV, DOWNLOADS / "from_claude.csv"):
        try:
            if p.exists():
                p.unlink()
        except Exception:
            pass

    prompt = build_refresh_prompt(cfg)
    (EXPORTS / "REFRESH_PROMPT.txt").write_text(prompt, encoding="utf-8")
    copied = copy_to_clipboard(prompt)
    opened = open_claude_desktop()
    time.sleep(1.0)
    sent = paste_and_send_in_claude(new_chat=True)

    if not sent.get("ok"):
        # Still return so UI can fall back to manual paste
        return {
            "ok": False,
            "auto_send": False,
            "prompt_copied": copied,
            "claude_opened": opened,
            "send_error": sent.get("stderr") or sent.get("stdout") or "osascript failed",
            "hint": (
                "Grant Accessibility to Terminal/Cursor/Python: "
                "System Settings → Privacy & Security → Accessibility. "
                "Prompt is on clipboard — paste in Claude once, then retry."
            ),
            "expect_csv": str(OUT_CSV),
        }

    poll = poll_refresh_csv(timeout_sec=wait_sec)
    return {
        "ok": bool(poll.get("ready")),
        "auto_send": True,
        "prompt_copied": copied,
        "claude_opened": opened,
        "poll": poll,
        "expect_csv": str(OUT_CSV),
    }


def run_auto_push(cfg: Config, full_csv: Path, *, wait_sec: int = 120) -> dict[str, Any]:
    prompt = build_push_prompt(cfg, full_csv)
    (EXPORTS / "PUSH_PROMPT.txt").write_text(prompt, encoding="utf-8")
    copied = copy_to_clipboard(prompt)
    opened = open_claude_desktop()
    time.sleep(1.0)
    sent = paste_and_send_in_claude(new_chat=True)
    # Push has no local file signal — wait a bit for Claude to finish Drive write
    if sent.get("ok"):
        time.sleep(min(wait_sec, 45))
    return {
        "ok": sent.get("ok", False),
        "auto_send": sent.get("ok", False),
        "prompt_copied": copied,
        "claude_opened": opened,
        "send_error": sent.get("stderr") or "",
        "source_csv": str(full_csv),
    }


# Back-compat names used by older code paths
def start_refresh(cfg: Config) -> dict[str, Any]:
    return run_auto_refresh(cfg, wait_sec=0)  # prepare only — prefer run_auto_refresh with wait


def start_push(cfg: Config, full_csv: Path) -> dict[str, Any]:
    return run_auto_push(cfg, full_csv, wait_sec=0)
