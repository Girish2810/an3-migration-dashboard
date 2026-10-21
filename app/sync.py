from __future__ import annotations

import csv
import os
from pathlib import Path
from typing import Any

from app.config import Config
from app.db import Database
from app.fabric_client import FabricClient
from app.jira_client import JiraClient
from app.sheets_client import SheetsClient


def _is_lambda() -> bool:
    return bool(os.environ.get("AWS_LAMBDA_FUNCTION_NAME"))


class SyncEngine:
    def __init__(self, cfg: Config, db: Database) -> None:
        self.cfg = cfg
        self.db = db
        self.sheets = SheetsClient(cfg)
        self.jira = JiraClient(cfg)
        self.fabric = FabricClient(cfg)

    def pull_from_multiple_sheets(self) -> dict[str, Any]:
        """Pull data from all configured sheets (deliveries, deletions, daily_log, etc.)."""
        results = {}
        sheets_config = self.cfg.sheets_config or {}

        # Pull from each configured sheet
        for sheet_key, sheet_info in sheets_config.items():
            try:
                worksheet_name = sheet_info.get("worksheet_name")
                columns = sheet_info.get("columns") or []

                if not worksheet_name or not columns:
                    continue

                # Read sheet tab
                header, rows = self.sheets.read_tab(worksheet_name)

                # Apply columns if configured
                if columns:
                    # Filter to only configured columns
                    row_dicts = []
                    for row in rows:
                        row_dict = {}
                        for i, col in enumerate(header):
                            if i < len(row):
                                row_dict[col] = str(row[i]).strip() if row[i] else ""
                        row_dicts.append(row_dict)
                else:
                    row_dicts = rows

                results[sheet_key] = {
                    "worksheet": worksheet_name,
                    "rows": len(row_dicts),
                    "data": row_dicts
                }
            except Exception as e:
                results[sheet_key] = {"error": str(e)}

        return {"ok": True, "sheets": results}

    def _pull_from_gviz(self) -> dict[str, Any]:
        """Pull rows from the public Google Sheets gviz/tq endpoint (no auth needed)."""
        import json
        import re
        import urllib.request

        sheet_id = self.cfg.spreadsheet_id
        if not sheet_id:
            raise RuntimeError("spreadsheet_id not set in config")
        sheet_name = self.cfg.worksheet_name or "Deliveries"
        url = (
            f"https://docs.google.com/spreadsheets/d/{sheet_id}"
            f"/gviz/tq?tqx=out:json&headers=1&sheet={sheet_name}"
        )
        with urllib.request.urlopen(url, timeout=15) as r:
            raw = r.read().decode("utf-8")

        # Strip JSONP wrapper: google.visualization.Query.setResponse({...});
        m = re.search(r"setResponse\((\{.*\})\)", raw, re.DOTALL)
        if not m:
            raise RuntimeError("Unexpected gviz response — is the sheet shared as 'Anyone with link'?")
        data = json.loads(m.group(1))
        if data.get("status") == "error":
            raise RuntimeError(data.get("errors", [{}])[0].get("message", "gviz error"))

        table = data["table"]
        headers = [c.get("label") or c.get("id") or "" for c in table["cols"]]
        rows = []
        for r in table.get("rows") or []:
            cells = [(c["v"] if c and c.get("v") is not None else "") for c in (r.get("c") or [])]
            row = {headers[i]: str(cells[i]).strip() if i < len(cells) else "" for i in range(len(headers))}
            rows.append(row)

        columns = self.cfg.sheet_columns or headers
        n = self.db.apply_sheet_rows(columns, rows, replace=True)
        self.db.log_sync("pull", "gviz", f"{n} rows")
        return {"ok": True, "rows": n}

    def pull_from_sheets(self) -> dict[str, Any]:
        header, rows = self.sheets.read_all()
        columns = self.cfg.sheet_columns or header
        if header and not self.cfg.sheet_columns:
            columns = header
        n = self.db.apply_sheet_rows(columns, rows, replace=True)
        self.db.log_sync("pull", "google_sheets", f"{n} rows")
        return {"ok": True, "rows": n, "columns": columns}

    def push_to_sheets(self) -> dict[str, Any]:
        columns = self.cfg.sheet_columns
        if not columns:
            raise RuntimeError("sheet columns not configured")
        rows = self.db.to_sheet_rows(columns)
        self.sheets.write_all(columns, rows)
        self.db.log_sync("push", "google_sheets", f"{len(rows)} rows")
        return {"ok": True, "rows": len(rows)}

    def add_account_with_distributions(self, data: dict[str, Any]) -> dict[str, Any]:
        """Add a new account with multiple distributions; append rows to sheet immediately."""
        amg_id = (data.get("amg_id") or "").strip()
        account_name = (data.get("account_name") or "").strip()
        if not amg_id or not account_name:
            raise RuntimeError("amg_id and account_name are required")

        jira_keys = [k.strip() for k in (data.get("jira_keys") or []) if k.strip()]
        distributions = data.get("distributions") or []
        if not distributions:
            raise RuntimeError("At least one distribution row is required")

        jira_creation = jira_keys[0] if len(jira_keys) > 0 else ""
        jira_switchover = jira_keys[1] if len(jira_keys) > 1 else ""
        jira_extra = jira_keys[2] if len(jira_keys) > 2 else ""

        saved_ids: list[int] = []
        for dist in distributions:
            row: dict[str, Any] = {
                "amg_id": amg_id,
                "account_name": account_name,
                "channel_id": (dist.get("channel_id") or "").strip() or "(pending)",
                "distribution_id": (dist.get("distribution_id") or "").strip(),
                "status": (dist.get("status") or "not-started").strip(),
                "initial_start_date": (data.get("initial_start_date") or "").strip(),
                "eta_provided_date": (data.get("eta_provided_date") or "").strip(),
                "remarks": (data.get("remarks") or "").strip(),
                "jira_creation_key": jira_creation,
                "jira_switchover_key": jira_switchover,
                "jira_extra_key": jira_extra,
                "dirty": 1,
            }
            saved_ids.append(self.db.upsert_delivery(row))

        sheet_appended = False
        if self.sheets.configured and self.cfg.sheet_columns:
            cols = self.cfg.sheet_columns
            all_rows = self.db.list_deliveries()
            sheet_rows = []
            for did in saved_ids:
                saved = next((r for r in all_rows if r["id"] == did), {})
                sheet_rows.append([str(saved.get(c) or "") for c in cols])
            if sheet_rows:
                self.sheets.append_rows(sheet_rows)
                sheet_appended = True
                self.db.clear_dirty(saved_ids)

        self.db.log_sync("add_account", "local", f"{amg_id}: {len(saved_ids)} distributions")
        return {
            "ok": True,
            "count": len(saved_ids),
            "ids": saved_ids,
            "sheet_appended": sheet_appended,
            "pending_sync": self.db.stats()["pending_sync"],
        }

    def append_entry(self, entry: dict[str, Any]) -> dict[str, Any]:
        """Add ticket/delivery locally only. Drive/Claude sync happens later in one bulk shot."""
        row = {
            "amg_id": (entry.get("amg_id") or "").strip(),
            "account_name": (entry.get("account_name") or "").strip(),
            "channel_id": (entry.get("channel_id") or "").strip(),
            "channel_name": (entry.get("channel_name") or "").strip(),
            "platform": (entry.get("platform") or "").strip(),
            "platform_id": (entry.get("platform_id") or "").strip(),
            "distribution_id": (entry.get("distribution_id") or "").strip(),
            "stream_type": (entry.get("stream_type") or "hls").strip() or "hls",
            "status": (entry.get("status") or "non-ssai pending").strip(),
            "remarks": (entry.get("remarks") or "").strip(),
            "initial_start_date": (entry.get("initial_start_date") or "").strip(),
            "eta_provided_date": (entry.get("eta_provided_date") or "").strip(),
            "completed_date": (entry.get("completed_date") or "").strip(),
            "jira_creation_key": (entry.get("jira_creation_key") or "").strip(),
            "jira_switchover_key": (entry.get("jira_switchover_key") or "").strip(),
            "jira_extra_key": (entry.get("jira_extra_key") or "").strip(),
            "migration_resources": entry.get("migration_resources") or "",
            "dirty": 1,
        }
        if not row["account_name"] or not row["amg_id"]:
            raise RuntimeError("account_name and amg_id are required")
        if not row["jira_creation_key"] and not row["jira_switchover_key"] and not row["channel_id"]:
            raise RuntimeError("provide at least a Jira ticket or channel_id")
        if not row["channel_id"]:
            row["channel_id"] = "(pending)"
        new_id = self.db.upsert_delivery(row)
        self.db.log_sync("append", "local", f"delivery {new_id} pending Drive sync")
        return {
            "ok": True,
            "id": new_id,
            "pending": True,
            "pending_sync": self.db.stats()["pending_sync"],
            "delivery": row,
        }

    def append_entries_bulk(self, entries: list[dict[str, Any]]) -> dict[str, Any]:
        ids = []
        for e in entries:
            r = self.append_entry(e)
            ids.append(r["id"])
        return {
            "ok": True,
            "added": len(ids),
            "ids": ids,
            "pending_sync": self.db.stats()["pending_sync"],
        }

    def update_and_push_status(self, delivery_id: int, patch: dict[str, Any]) -> dict[str, Any]:
        """Local status update only — marks dirty for later bulk Drive sync."""
        rows = self.db.list_deliveries()
        row = next((r for r in rows if r["id"] == delivery_id), None)
        if not row:
            raise RuntimeError("delivery not found")
        payload = {**row}
        for k, v in patch.items():
            if v != "":
                payload[k] = v
        payload["dirty"] = 1
        self.db.upsert_delivery(payload)
        self.db.log_sync("update", "local", f"delivery {delivery_id} pending Drive sync")
        return {
            "ok": True,
            "pending": True,
            "pending_sync": self.db.stats()["pending_sync"],
            "sheet_pushed": False,
        }

    def prepare_claude_drive_sync(self) -> dict[str, Any]:
        """
        One-shot pack for Claude + Google Drive.
        Does NOT call Claude. User pastes prompt once → Claude updates Sheet in one turn.
        """
        from app.config import ROOT

        out_dir = ROOT / "exports"
        out_dir.mkdir(parents=True, exist_ok=True)
        columns = self.cfg.sheet_columns or [
            "amg_id", "initial_start_date", "eta_provided_date", "completed_date",
            "account_name", "channel_id", "distribution_id", "status", "remarks",
            "platform", "platform_id", "channel_name", "stream_type",
            "jira_creation_key", "jira_switchover_key", "migration_resources",
        ]
        dirty = self.db.list_dirty()
        full_csv = self.export_csv_text()
        (out_dir / "full_export.csv").write_text(full_csv, encoding="utf-8")

        # pending-only CSV (smaller → fewer Claude tokens)
        import csv
        import io

        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(columns + ["_dashboard_id", "_action"])
        for d in dirty:
            w.writerow([str(d.get(c) or "") for c in columns] + [d["id"], "upsert"])
        pending_csv = buf.getvalue()
        (out_dir / "pending_only.csv").write_text(pending_csv, encoding="utf-8")

        sheet_url = (
            f"https://docs.google.com/spreadsheets/d/{self.cfg.spreadsheet_id}/edit"
            if self.cfg.spreadsheet_id
            else "(set spreadsheet_id in config)"
        )
        tab = self.cfg.worksheet_name or "Deliveries"
        prompt = f"""You have Google Drive access. Do ONE efficient Sheet update (no chatter).

Sheet: {sheet_url}
Tab: {tab}

Task:
1. Open that Google Sheet tab.
2. Prefer applying ONLY the pending rows below (append new / update matching channel_id+platform+distribution_id or jira keys).
3. If easier, replace the whole tab with full_export.csv contents (keep header row).
4. Reply with: rows_appended, rows_updated, and done.

Pending rows CSV:
```csv
{pending_csv}
```

Full export path on disk (if you need it): {out_dir / "full_export.csv"}
Header columns: {", ".join(columns)}
"""
        prompt_path = out_dir / "CLAUDE_DRIVE_PROMPT.txt"
        prompt_path.write_text(prompt, encoding="utf-8")
        self.db.log_sync("prepare", "claude_drive", f"{len(dirty)} pending rows")
        return {
            "ok": True,
            "pending_count": len(dirty),
            "prompt_path": str(prompt_path),
            "pending_csv_path": str(out_dir / "pending_only.csv"),
            "full_csv_path": str(out_dir / "full_export.csv"),
            "prompt": prompt,
            "sheet_url": sheet_url,
        }

    def mark_synced(self) -> dict[str, Any]:
        n = self.db.clear_dirty()
        self.db.log_sync("clear", "dirty", f"cleared {n}")
        return {"ok": True, "cleared": n}

    def pull_from_jira(self) -> dict[str, Any]:
        patches = self.jira.poll_updates()
        updated = 0
        for p in patches:
            jira_key = p.get("jira_key") or ""
            dist = p.get("distribution_id") or ""
            row = self.db.get_by_jira_key(jira_key) if jira_key else None
            if not row and dist:
                row = self.db.get_by_distribution_id(dist)
            if not row:
                continue
            payload = {"id": row["id"]}
            for k in (
                "status", "completed_date", "initial_start_date", "eta_provided_date",
                "jira_creation_key", "jira_switchover_key", "distribution_id",
            ):
                if p.get(k):
                    payload[k] = p[k]
            self.db.upsert_delivery({**row, **payload})
            updated += 1
        self.db.log_sync("pull", "jira", f"{updated} rows updated from {len(patches)} issues")
        return {"ok": True, "issues_seen": len(patches), "rows_updated": updated}

    def enrich_delivery(self, delivery_id: int) -> dict[str, Any]:
        rows = self.db.list_deliveries()
        row = next((r for r in rows if r["id"] == delivery_id), None)
        if not row:
            raise RuntimeError("delivery not found")
        dist = row.get("distribution_id") or ""
        if not dist or not self.fabric.configured:
            return {"ok": False, "reason": "no distribution_id or fabric not configured"}
        body = self.fabric.get_delivery(row["amg_id"], dist)
        info = self.fabric.enrich(body)
        self.db.upsert_delivery({**row, **info})
        return {"ok": True, "enriched": info}

    def enrich_account(self, account_name: str) -> dict[str, Any]:
        n = 0
        for row in self.db.list_deliveries(account_name=account_name):
            if not row.get("distribution_id"):
                continue
            try:
                self.enrich_delivery(row["id"])
                n += 1
            except Exception:
                pass
        return {"ok": True, "enriched": n}

    def import_csv(self, path: Path, *, replace: bool = False) -> dict[str, Any]:
        rows: list[dict[str, Any]] = []
        with path.open(newline="", encoding="utf-8-sig") as f:
            last_amg = ""
            last_account = ""
            last_channel = ""
            for item in csv.DictReader(f):
                row = self._csv_row_to_delivery(item)
                # Sheet often fills amg_id / account only on the first row of a block
                if row["amg_id"]:
                    last_amg = row["amg_id"]
                else:
                    row["amg_id"] = last_amg
                if row["account_name"]:
                    last_account = row["account_name"]
                else:
                    row["account_name"] = last_account
                if row["channel_id"] and row["channel_id"] != "(pending)":
                    last_channel = row["channel_id"]
                elif not row["channel_id"] or row["channel_id"] == "(pending)":
                    row["channel_id"] = last_channel or "(pending)"
                if not row["amg_id"] or not row["account_name"]:
                    continue
                row["dirty"] = 0
                rows.append(row)
        n = self.db.import_rows(rows, replace=replace)
        self.db.clear_dirty()
        self.db.log_sync("import", "csv", f"{path.name} {n} rows")
        return {"ok": True, "rows": n, "pending_sync": self.db.stats()["pending_sync"]}

    def import_csv_text(self, text: str, *, replace: bool = True) -> dict[str, Any]:
        """Import CSV content downloaded from Google Sheet (File → Download → CSV)."""
        import io

        rows: list[dict[str, Any]] = []
        last_amg = ""
        last_account = ""
        last_channel = ""
        reader = csv.DictReader(io.StringIO(text))
        for item in reader:
            row = self._csv_row_to_delivery(item)
            if row["amg_id"]:
                last_amg = row["amg_id"]
            else:
                row["amg_id"] = last_amg
            if row["account_name"]:
                last_account = row["account_name"]
            else:
                row["account_name"] = last_account
            if row["channel_id"] and row["channel_id"] != "(pending)":
                last_channel = row["channel_id"]
            elif not row["channel_id"] or row["channel_id"] == "(pending)":
                row["channel_id"] = last_channel or "(pending)"
            if not row["amg_id"] or not row["account_name"]:
                continue
            row["dirty"] = 0
            rows.append(row)
        n = self.db.import_rows(rows, replace=replace)
        self.db.clear_dirty()
        self.db.log_sync("import", "csv", f"upload {n} rows replace={replace}")
        return {"ok": True, "rows": n, "pending_sync": self.db.stats()["pending_sync"]}

    def start_sheet_refresh(self) -> dict[str, Any]:
        """Fully automatic Refresh — no paste/load steps.

        On Lambda: Sheets API only (no browser or Claude Desktop available).
        Local: browser export → Claude Desktop fallback.
        """
        # 1) Sheets API if already authorized
        if self.sheets.configured:
            try:
                pulled = self.pull_from_sheets()
                return {
                    "ok": True,
                    "auto": True,
                    "via": "sheets_api",
                    "imported": {"rows": pulled.get("rows"), "ok": True},
                    "stats": self.db.stats(),
                    "accounts": self.db.list_accounts(),
                    "detail": pulled,
                }
            except Exception:
                pass

        if _is_lambda():
            # Fall back to public gviz endpoint (no auth — sheet must be shared "Anyone with link")
            try:
                pulled = self._pull_from_gviz()
                return {
                    "ok": True,
                    "auto": True,
                    "via": "gviz",
                    "imported": {"rows": pulled.get("rows"), "ok": True},
                    "stats": self.db.stats(),
                    "accounts": self.db.list_accounts(),
                }
            except Exception as exc:
                return {"ok": False, "auto": False, "error": str(exc)}

        from app import browser_sheet, claude_sync

        # 2) Browser export using existing Google login (true one-click)
        browser = browser_sheet.run_browser_refresh(self.cfg, wait_sec=90)
        if browser.get("ok"):
            imported = self.import_csv(Path(browser["path"]), replace=True)
            self.db.log_sync("pull", "browser_export", f"{imported['rows']} rows")
            return {
                "ok": True,
                "auto": True,
                "via": "browser",
                "imported": imported,
                "stats": self.db.stats(),
                "accounts": self.db.list_accounts(),
                "source": browser.get("source"),
            }

        # 3) Claude Desktop only if installed
        if claude_sync.claude_desktop_available():
            result = claude_sync.run_auto_refresh(self.cfg, wait_sec=180)
            if result.get("ok") and result.get("poll", {}).get("ready"):
                path = Path(result["poll"]["path"])
                dest = claude_sync.OUT_CSV
                if path.resolve() != dest.resolve():
                    dest.write_bytes(path.read_bytes())
                imported = self.import_csv(dest, replace=True)
                self.db.log_sync("pull", "claude_desktop_auto", f"{imported['rows']} rows")
                return {
                    "ok": True,
                    "auto": True,
                    "via": "claude_desktop",
                    "imported": imported,
                    "stats": self.db.stats(),
                    "accounts": self.db.list_accounts(),
                }
            return {
                "ok": False,
                "auto": False,
                "detail": {"browser": browser, "claude": result},
                "error": (
                    result.get("send_error")
                    or result.get("hint")
                    or "Refresh failed (browser + Claude)."
                ),
            }

        return {
            "ok": False,
            "auto": False,
            "detail": browser,
            "error": browser.get("error")
            or "Could not download Sheet. Open the Sheet once in Chrome while logged into Google, then retry Refresh.",
        }

    def complete_sheet_refresh(self) -> dict[str, Any]:
        from app import claude_sync

        poll = claude_sync.poll_refresh_csv()
        if not poll.get("ready"):
            raise RuntimeError(
                "CSV not ready yet. Expected file from Claude Desktop at "
                f"{poll.get('path')}"
            )
        imported = self.import_csv(Path(poll["path"]), replace=True)
        self.db.log_sync("pull", "claude_desktop", f"{imported['rows']} rows")
        return {
            "ok": True,
            "imported": imported,
            "stats": self.db.stats(),
            "accounts": self.db.list_accounts(),
        }

    def start_sheet_push(self) -> dict[str, Any]:
        if _is_lambda():
            if self.sheets.configured:
                result = self.push_to_sheets()
                cleared = self.mark_synced()
                return {"ok": True, "auto": True, "via": "sheets_api", "cleared": cleared, "detail": result}
            return {"ok": False, "auto": False, "error": "Sheets API not configured on Lambda."}

        from app import claude_sync

        pack = self.prepare_claude_drive_sync()
        full = Path(pack["full_csv_path"])
        result = claude_sync.run_auto_push(self.cfg, full, wait_sec=60)
        if result.get("ok"):
            cleared = self.mark_synced()
            return {
                "ok": True,
                "auto": True,
                "pending_was": pack["pending_count"],
                "cleared": cleared,
            }
        return {
            "ok": False,
            "auto": False,
            "pending_count": pack["pending_count"],
            "error": result.get("send_error")
            or "Could not auto-send to Claude Desktop. Prompt is on clipboard.",
            "detail": result,
        }

    def get_sheet_via_claude(self) -> dict[str, Any]:
        return self.start_sheet_refresh()

    def push_sheet_via_claude(self) -> dict[str, Any]:
        return self.start_sheet_push()

    @staticmethod
    def _csv_row_to_delivery(item: dict[str, Any]) -> dict[str, Any]:
        name = (item.get("account_name") or "").strip()
        # normalize common typo / singular
        if name.lower() in {"ott studio", "ott studios"}:
            name = "OTT Studios"
        return {
            "amg_id": (item.get("amg_id") or "").strip(),
            "account_name": name,
            "channel_id": (item.get("channel_id") or "").strip() or "(pending)",
            "channel_name": (item.get("channel_name") or "").strip(),
            "platform": (item.get("platform") or "").strip(),
            "platform_id": (item.get("platform_id") or "").strip(),
            "distribution_id": (item.get("distribution_id") or "").strip(),
            "stream_type": (item.get("stream_type") or "hls").strip() or "hls",
            "status": (item.get("status") or "").strip(),
            "remarks": (item.get("remarks") or "").strip(),
            "initial_start_date": (item.get("initial_start_date") or "").strip(),
            "eta_provided_date": (item.get("eta_provided_date") or "").strip(),
            "completed_date": (item.get("completed_date") or "").strip(),
            "jira_creation_key": (item.get("jira_creation_key") or "").strip(),
            "jira_switchover_key": (item.get("jira_switchover_key") or "").strip(),
            "jira_extra_key": (item.get("jira_extra_key") or "").strip(),
            "migration_resources": (item.get("migration_resources") or "").strip(),
        }

    def export_csv_text(self) -> str:
        """Export dashboard DB as CSV to paste/import back into Google Sheet."""
        import io

        columns = self.cfg.sheet_columns or [
            "amg_id", "initial_start_date", "eta_provided_date", "completed_date",
            "account_name", "channel_id", "distribution_id", "status", "remarks",
            "platform", "platform_id", "channel_name", "stream_type",
            "jira_creation_key", "jira_switchover_key", "migration_resources",
        ]
        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow(columns)
        for row in self.db.to_sheet_rows(columns):
            writer.writerow(row)
        self.db.log_sync("export", "csv", f"{len(self.db.list_deliveries())} rows")
        return buf.getvalue()

    def export_csv(self, path: Path) -> dict[str, Any]:
        path.parent.mkdir(parents=True, exist_ok=True)
        text = self.export_csv_text()
        path.write_text(text, encoding="utf-8")
        return {"ok": True, "path": str(path), "bytes": len(text.encode("utf-8"))}

    def add_delivery_and_append_sheet(self, data: dict[str, Any]) -> dict[str, Any]:
        """Add ticket/delivery in dashboard and append a new row at end of Google Sheet."""
        existing = self.db.find_existing(data)
        if existing:
            raise RuntimeError(
                f"Already exists (id={existing['id']}, dist={existing.get('distribution_id') or '—'}). "
                "Edit the existing row or Pull Sheet instead."
            )
        if not data.get("status"):
            data["status"] = "non-ssai pending"
        new_id = self.db.upsert_delivery(data)
        appended = False
        if self.sheets.configured and self.cfg.sheet_columns:
            cols = self.cfg.sheet_columns
            row = self.db.list_deliveries()
            saved = next((r for r in row if r["id"] == new_id), data)
            values = [str(saved.get(c) or "") for c in cols]
            self.sheets.append_rows([values])
            appended = True
            self.db.log_sync("append", "google_sheets", f"delivery {new_id}")
        return {"ok": True, "id": new_id, "sheet_appended": appended}

    def update_delivery_and_push_sheet(self, delivery_id: int, patch: dict[str, Any]) -> dict[str, Any]:
        rows = self.db.list_deliveries()
        row = next((r for r in rows if r["id"] == delivery_id), None)
        if not row:
            raise RuntimeError("delivery not found")
        payload = {**row, **{k: v for k, v in patch.items() if v is not None and v != ""}}
        # allow clearing? keep non-empty only for partial updates; empty string for status not allowed here
        for k, v in patch.items():
            if k in payload or v == "":
                if v != "" or k in ("remarks", "status", "distribution_id", "completed_date"):
                    payload[k] = v
        self.db.upsert_delivery(payload)
        pushed = False
        # Prefer full rewrite so status marks in dashboard stay in sheet
        if self.sheets.configured and self.cfg.sheet_columns:
            self.push_to_sheets()
            pushed = True
        return {"ok": True, "id": delivery_id, "sheet_pushed": pushed}

    def _pull_deletion_from_gviz(self) -> dict[str, Any]:
        import json
        import re
        import urllib.request

        sheet_id = self.cfg.deletion_spreadsheet_id or self.cfg.spreadsheet_id
        if not sheet_id:
            raise RuntimeError("deletion_spreadsheet_id not set in config")
        gid = self.cfg.deletion_sheet_gid
        if gid:
            url = (
                f"https://docs.google.com/spreadsheets/d/{sheet_id}"
                f"/gviz/tq?tqx=out:json&headers=1&gid={gid}"
            )
        else:
            sheet_name = self.cfg.deletion_worksheet_name or "Channel deletion sheet"
            url = (
                f"https://docs.google.com/spreadsheets/d/{sheet_id}"
                f"/gviz/tq?tqx=out:json&headers=1&sheet={sheet_name}"
            )
        with urllib.request.urlopen(url, timeout=15) as r:
            raw = r.read().decode("utf-8")
        m = re.search(r"setResponse\((\{.*\})\)", raw, re.DOTALL)
        if not m:
            raise RuntimeError("Unexpected gviz response from deletion sheet")
        data = json.loads(m.group(1))
        if data.get("status") == "error":
            raise RuntimeError(data.get("errors", [{}])[0].get("message", "gviz error"))
        table = data["table"]
        headers = [c.get("label") or c.get("id") or "" for c in table["cols"]]
        rows = []
        for r in table.get("rows") or []:
            cells = [(c["v"] if c and c.get("v") is not None else "") for c in (r.get("c") or [])]
            row = {headers[i]: str(cells[i]).strip() if i < len(cells) else "" for i in range(len(headers))}
            rows.append(row)
        columns = self.cfg.deletion_columns or headers
        n = self.db.apply_deletion_sheet_rows(columns, rows, replace=True)
        self.db.log_sync("pull", "deletion_gviz", f"{n} rows")
        return {"ok": True, "rows": n}

    def pull_deletion_from_sheets(self) -> dict[str, Any]:
        del_id = self.cfg.deletion_spreadsheet_id
        main_id = self.cfg.spreadsheet_id
        if del_id and del_id != main_id:
            return self._pull_deletion_from_gviz()
        header, rows = self.sheets.read_tab(self.cfg.deletion_worksheet_name)
        columns = self.cfg.deletion_columns or header
        n = self.db.apply_deletion_sheet_rows(columns, rows, replace=True)
        self.db.log_sync("pull", "deletion_sheet", f"{n} rows")
        return {"ok": True, "rows": n}

    def push_deletion_to_sheets(self) -> dict[str, Any]:
        columns = self.cfg.deletion_columns
        if not columns:
            raise RuntimeError("deletion columns not configured")
        rows = self.db.deletion_to_sheet_rows(columns)
        self.sheets.write_tab(self.cfg.deletion_worksheet_name, columns, rows)
        self.db.log_sync("push", "deletion_sheet", f"{len(rows)} rows")
        return {"ok": True, "pushed": len(rows)}

    def full_sync(self) -> dict[str, Any]:
        """Sheet is source of truth: pull sheet first, then optional Jira enrich, then push back."""
        out: dict[str, Any] = {}
        if self.sheets.configured:
            out["sheets_pull"] = self.pull_from_sheets()
        if self.jira.configured:
            out["jira_pull"] = self.pull_from_jira()
        if self.sheets.configured:
            out["sheets_push"] = self.push_to_sheets()
        return out
