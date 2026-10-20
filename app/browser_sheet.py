"""One-click Google Sheet pull using the user's logged-in browser session.

Opens the Sheets export URL in Chrome/default browser (uses existing Google login),
then picks up the downloaded CSV from ~/Downloads — no Claude paste, no GCP project.
"""

from __future__ import annotations

import subprocess
import time
from pathlib import Path
from typing import Any

from app.config import Config, ROOT

EXPORTS = ROOT / "exports"
OUT_CSV = EXPORTS / "from_claude.csv"
DOWNLOADS = Path.home() / "Downloads"

# Filenames Google commonly uses for this workbook when exported
NAME_HINTS = (
    "delivery migartion",
    "delivery migration",
    "an3 migration",
    "from_claude",
)


def _chrome_app() -> str | None:
    for p in (
        "/Applications/Google Chrome.app",
        Path.home() / "Applications/Google Chrome.app",
    ):
        if Path(p).exists():
            return str(p)
    return None


def export_url(cfg: Config) -> str:
    sid = cfg.spreadsheet_id
    # First sheet / default gid — Works when user is logged into Google in browser
    return f"https://docs.google.com/spreadsheets/d/{sid}/export?format=csv"


def open_export(cfg: Config) -> dict[str, Any]:
    url = export_url(cfg)
    chrome = _chrome_app()
    if chrome:
        proc = subprocess.run(["open", "-a", chrome, url], capture_output=True, text=True)
    else:
        proc = subprocess.run(["open", url], capture_output=True, text=True)
    return {
        "ok": proc.returncode == 0,
        "url": url,
        "stderr": (proc.stderr or "").strip(),
        "via": "chrome" if chrome else "default_browser",
    }


def _score_csv(path: Path, started: float) -> float | None:
    try:
        st = path.stat()
    except OSError:
        return None
    if st.st_size < 40:
        return None
    # Must be created/modified at or after we triggered the export
    if st.st_mtime < started - 2:
        return None
    name = path.name.lower()
    score = st.st_mtime
    if any(h in name for h in NAME_HINTS):
        score += 1000
    if name.endswith(".csv"):
        score += 10
    return score


def find_new_download(*, started: float) -> Path | None:
    best: Path | None = None
    best_score = -1.0
    try:
        paths = list(DOWNLOADS.glob("*.csv"))
    except OSError:
        return None
    for p in paths:
        sc = _score_csv(p, started)
        if sc is not None and sc > best_score:
            best = p
            best_score = sc
    return best


def poll_download(*, started: float, timeout_sec: float = 90) -> dict[str, Any]:
    deadline = time.time() + timeout_sec
    last: Path | None = None
    while time.time() < deadline:
        p = find_new_download(started=started)
        if p:
            # Wait briefly for download to finish writing
            time.sleep(0.8)
            try:
                size1 = p.stat().st_size
                time.sleep(0.5)
                size2 = p.stat().st_size
                if size1 == size2 and size1 > 40:
                    return {"ready": True, "path": str(p), "bytes": size2}
            except OSError:
                pass
            last = p
        time.sleep(1.0)
    return {
        "ready": False,
        "path": str(last) if last else "",
        "hint": "No new CSV in Downloads. Stay logged into Google in Chrome and retry.",
    }


def run_browser_refresh(cfg: Config, *, wait_sec: int = 90) -> dict[str, Any]:
    """Trigger browser export and return path to downloaded CSV (also copied to exports/)."""
    EXPORTS.mkdir(parents=True, exist_ok=True)
    started = time.time()
    opened = open_export(cfg)
    if not opened.get("ok"):
        return {
            "ok": False,
            "error": opened.get("stderr") or "Could not open browser for Sheet export",
            "opened": opened,
        }
    poll = poll_download(started=started, timeout_sec=wait_sec)
    if not poll.get("ready"):
        return {
            "ok": False,
            "error": poll.get("hint") or "Sheet CSV download timed out",
            "opened": opened,
            "poll": poll,
        }
    src = Path(poll["path"])
    OUT_CSV.write_bytes(src.read_bytes())
    return {
        "ok": True,
        "path": str(OUT_CSV),
        "source": str(src),
        "bytes": OUT_CSV.stat().st_size,
        "opened": opened,
    }
