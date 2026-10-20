#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import uvicorn

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from app.config import Config
from app.db import Database
from app.sync import SyncEngine


def main() -> None:
    p = argparse.ArgumentParser(description="AN3 Migration Dashboard (CSV mode — no Google Cloud)")
    sub = p.add_subparsers(dest="cmd")

    sub.add_parser("serve", help="Start web UI")

    imp = sub.add_parser("import-csv", help="Import CSV downloaded from Google Sheet")
    imp.add_argument("csv")
    imp.add_argument("--replace", action="store_true", help="Replace all local rows")

    exp = sub.add_parser("export-csv", help="Export dashboard → CSV (paste back into Sheet)")
    exp.add_argument("-o", "--output", default="exports/an3-migration-export.csv")

    sub.add_parser("sync-jira-pull", help="Optional: update rows from linked Jira tickets")
    sub.add_parser("auth-google", help="One-time Google OAuth login — saves token to secrets/")

    args = p.parse_args()
    cfg = Config.load()
    db = Database(cfg.db_path)
    sync = SyncEngine(cfg, db)

    if args.cmd == "serve":
        uvicorn.run("app.main:app", host=cfg.server_host, port=cfg.server_port, reload=False)
    elif args.cmd == "import-csv":
        print(sync.import_csv(Path(args.csv), replace=args.replace))
    elif args.cmd == "export-csv":
        print(sync.export_csv(Path(args.output)))
    elif args.cmd == "sync-jira-pull":
        print(sync.pull_from_jira())
    elif args.cmd == "auth-google":
        from app.google_oauth import run_oauth_login
        token_path = run_oauth_login(cfg)
        print(f"✓ Google token saved to {token_path}")
        print("  Re-run: bash deploy/deploy-lambda.sh")
    else:
        p.print_help()
        print("\nTypical flow (no Google Cloud):")
        print("  1) Sheet → File → Download → CSV")
        print("  2) python cli.py import-csv ~/Downloads/….csv --replace")
        print("  3) python cli.py serve")
        print("  4) Work in UI → Export CSV → import/paste into Sheet")


if __name__ == "__main__":
    main()
