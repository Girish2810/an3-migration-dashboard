from __future__ import annotations

import json
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator


SCHEMA = """
CREATE TABLE IF NOT EXISTS deliveries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    amg_id TEXT NOT NULL,
    account_name TEXT NOT NULL,
    channel_id TEXT NOT NULL,
    channel_name TEXT DEFAULT '',
    platform TEXT DEFAULT '',
    platform_id TEXT DEFAULT '',
    distribution_id TEXT DEFAULT '',
    stream_type TEXT DEFAULT 'hls',
    status TEXT DEFAULT '',
    remarks TEXT DEFAULT '',
    initial_start_date TEXT DEFAULT '',
    eta_provided_date TEXT DEFAULT '',
    completed_date TEXT DEFAULT '',
    jira_creation_key TEXT DEFAULT '',
    jira_switchover_key TEXT DEFAULT '',
    jira_extra_key TEXT DEFAULT '',
    migration_resources TEXT DEFAULT '',
    non_monetized_url TEXT DEFAULT '',
    monetized_url TEXT DEFAULT '',
    ts_ingress_url TEXT DEFAULT '',
    dirty INTEGER DEFAULT 0,
    updated_at TEXT NOT NULL,
    sheet_row INTEGER
);
CREATE INDEX IF NOT EXISTS idx_deliveries_account ON deliveries(account_name);
CREATE INDEX IF NOT EXISTS idx_deliveries_amg ON deliveries(amg_id);
CREATE INDEX IF NOT EXISTS idx_deliveries_dist ON deliveries(distribution_id);
CREATE TABLE IF NOT EXISTS sync_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    direction TEXT NOT NULL,
    target TEXT NOT NULL,
    detail TEXT,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS channel_deletions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    amg_id TEXT NOT NULL DEFAULT '',
    account_name TEXT DEFAULT '',
    channel_name TEXT DEFAULT '',
    channel_id TEXT DEFAULT '',
    deletion_status TEXT DEFAULT 'Pending',
    jira TEXT DEFAULT '',
    dirty INTEGER DEFAULT 0,
    updated_at TEXT NOT NULL,
    sheet_row INTEGER
);
CREATE INDEX IF NOT EXISTS idx_channel_del_amg ON channel_deletions(amg_id);
CREATE INDEX IF NOT EXISTS idx_channel_del_account ON channel_deletions(account_name);
"""


def utcnow() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


class Database:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as conn:
            conn.executescript(SCHEMA)
            cols = {r[1] for r in conn.execute("PRAGMA table_info(deliveries)").fetchall()}
            if "dirty" not in cols:
                conn.execute("ALTER TABLE deliveries ADD COLUMN dirty INTEGER DEFAULT 0")
            if "jira_extra_key" not in cols:
                conn.execute("ALTER TABLE deliveries ADD COLUMN jira_extra_key TEXT DEFAULT ''")
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_deliveries_dirty ON deliveries(dirty)"
            )


    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def log_sync(self, direction: str, target: str, detail: str = "") -> None:
        with self.connect() as conn:
            conn.execute(
                "INSERT INTO sync_log(direction,target,detail,created_at) VALUES (?,?,?,?)",
                (direction, target, detail, utcnow()),
            )


    def list_accounts(self) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT account_name,
                       MAX(CASE WHEN amg_id != '' THEN amg_id ELSE NULL END) AS amg_id,
                       COUNT(*) AS total,
                       SUM(CASE WHEN status='switchover completed' THEN 1 ELSE 0 END) AS completed,
                       SUM(CASE WHEN status='ready for switchover' THEN 1 ELSE 0 END) AS ready,
                       SUM(CASE WHEN status='blocked' THEN 1 ELSE 0 END) AS blocked,
                       SUM(CASE WHEN status='non-ssai pending' THEN 1 ELSE 0 END) AS pending
                FROM deliveries
                GROUP BY account_name
                ORDER BY account_name
                """
            ).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            total = int(d.get("total") or 0)
            completed = int(d.get("completed") or 0)
            d["fully_migrated"] = total > 0 and completed == total
            out.append(d)
        return out

    def list_deliveries(
        self, *, account_name: str = "", amg_id: str = ""
    ) -> list[dict[str, Any]]:
        q = "SELECT * FROM deliveries WHERE 1=1"
        params: list[Any] = []
        if account_name:
            q += " AND account_name = ?"
            params.append(account_name)
        if amg_id:
            q += " AND amg_id = ?"
            params.append(amg_id)
        q += " ORDER BY channel_id, platform, distribution_id"
        with self.connect() as conn:
            rows = conn.execute(q, params).fetchall()
        return [dict(r) for r in rows]

    def upsert_delivery(self, row: dict[str, Any]) -> int:
        row = {**row, "updated_at": utcnow()}
        if "dirty" not in row:
            row["dirty"] = 1
        cols = [
            "amg_id", "account_name", "channel_id", "channel_name", "platform",
            "platform_id", "distribution_id", "stream_type", "status", "remarks",
            "initial_start_date", "eta_provided_date", "completed_date",
            "jira_creation_key", "jira_switchover_key", "jira_extra_key",
            "migration_resources",
            "non_monetized_url", "monetized_url", "ts_ingress_url", "dirty",
            "updated_at", "sheet_row",
        ]
        existing_id = row.get("id")
        with self.connect() as conn:
            if existing_id:
                sets = ", ".join(f"{c}=?" for c in cols)
                conn.execute(
                    f"UPDATE deliveries SET {sets} WHERE id=?",
                    [row.get(c, "") if c != "dirty" else int(row["dirty"] if "dirty" in row else 1) for c in cols]
                    + [existing_id],
                )
        if existing_id:
    
            return int(existing_id)
        with self.connect() as conn:
            placeholders = ", ".join("?" for _ in cols)
            cur = conn.execute(
                f"INSERT INTO deliveries ({', '.join(cols)}) VALUES ({placeholders})",
                [row.get(c, "") if c != "dirty" else int(row.get("dirty") or 1) for c in cols],
            )
            result_id = int(cur.lastrowid)

        return result_id

    def get_by_distribution_id(self, distribution_id: str) -> dict[str, Any] | None:
        if not distribution_id:
            return None
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM deliveries WHERE distribution_id = ? LIMIT 1",
                (distribution_id,),
            ).fetchone()
        return dict(row) if row else None

    def get_by_jira_key(self, jira_key: str) -> dict[str, Any] | None:
        if not jira_key:
            return None
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT * FROM deliveries
                WHERE jira_creation_key = ? OR jira_switchover_key = ?
                LIMIT 1
                """,
                (jira_key, jira_key),
            ).fetchone()
        return dict(row) if row else None

    def find_existing(self, data: dict[str, Any]) -> dict[str, Any] | None:
        dist = (data.get("distribution_id") or "").strip()
        if dist:
            found = self.get_by_distribution_id(dist)
            if found:
                return found
        for key in (data.get("jira_creation_key"), data.get("jira_switchover_key")):
            if key:
                found = self.get_by_jira_key(key.strip())
                if found:
                    return found
        amg = (data.get("amg_id") or "").strip()
        channel = (data.get("channel_id") or "").strip()
        platform = (data.get("platform") or data.get("platform_id") or "").strip()
        if amg and channel and platform:
            with self.connect() as conn:
                row = conn.execute(
                    """
                    SELECT * FROM deliveries
                    WHERE amg_id = ? AND channel_id = ?
                      AND (platform = ? OR platform_id = ?)
                    LIMIT 1
                    """,
                    (amg, channel, platform, platform),
                ).fetchone()
            if row:
                return dict(row)
        return None

    def import_rows(self, rows: list[dict[str, Any]], *, replace: bool = False) -> int:
        if replace:
            with self.connect() as conn:
                conn.execute("DELETE FROM deliveries")
        n = 0
        for row in rows:
            self.upsert_delivery(row)
            n += 1

        return n

    def to_sheet_rows(self, columns: list[str]) -> list[list[str]]:
        deliveries = self.list_deliveries()
        out: list[list[str]] = []
        for d in deliveries:
            out.append([str(d.get(c) or "") for c in columns])
        return out

    def apply_sheet_rows(
        self, columns: list[str], values: list[list[str]], *, replace: bool = True
    ) -> int:
        """On pull, sheet is source of truth (replace=True clears local then reloads)."""
        if replace:
            with self.connect() as conn:
                conn.execute("DELETE FROM deliveries")
        n = 0
        for i, row_vals in enumerate(values, start=2):
            if not any(str(cell).strip() for cell in row_vals):
                continue
            data = {
                columns[j]: (row_vals[j] if j < len(row_vals) else "")
                for j in range(len(columns))
            }
            if not (data.get("account_name") or data.get("amg_id")):
                continue
            dist = (data.get("distribution_id") or "").strip()
            existing = None if replace else self.find_existing(data)
            amg_id_val = data.get("amg_id", "")
            if not amg_id_val and not replace:
                acct = data.get("account_name", "").strip()
                if acct:
                    with self.connect() as _conn:
                        _row = _conn.execute(
                            "SELECT amg_id FROM deliveries WHERE account_name=? AND amg_id!='' LIMIT 1",
                            (acct,),
                        ).fetchone()
                    if _row:
                        amg_id_val = _row[0]
            payload = {
                "id": existing["id"] if existing else None,
                "amg_id": amg_id_val,
                "account_name": data.get("account_name", ""),
                "channel_id": data.get("channel_id", "") or "(pending)",
                "channel_name": data.get("channel_name", ""),
                "platform": data.get("platform", ""),
                "platform_id": data.get("platform_id", ""),
                "distribution_id": dist,
                "stream_type": data.get("stream_type", "hls") or "hls",
                "status": data.get("status", ""),
                "remarks": data.get("remarks", ""),
                "initial_start_date": data.get("initial_start_date", ""),
                "eta_provided_date": data.get("eta_provided_date", ""),
                "completed_date": data.get("completed_date", ""),
                "jira_creation_key": data.get("jira_creation_key", ""),
                "jira_switchover_key": data.get("jira_switchover_key", ""),
                "jira_extra_key": data.get("jira_extra_key", ""),
                "migration_resources": data.get("migration_resources", ""),
                "sheet_row": i,
                "dirty": 0,
            }
            self.upsert_delivery(payload)
            n += 1
        return n

    def list_deletion_accounts(self) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT
                    COALESCE(NULLIF(amg_id,''), account_name) AS group_key,
                    MAX(CASE WHEN amg_id != '' THEN amg_id ELSE NULL END) AS amg_id,
                    MAX(CASE WHEN account_name != '' THEN account_name ELSE NULL END) AS account_name,
                    COUNT(*) AS total,
                    SUM(CASE WHEN LOWER(deletion_status)='pending' THEN 1 ELSE 0 END) AS pending,
                    SUM(CASE WHEN LOWER(deletion_status)='deleted' THEN 1 ELSE 0 END) AS deleted_count
                FROM channel_deletions
                GROUP BY COALESCE(NULLIF(amg_id,''), account_name)
                ORDER BY account_name
                """
            ).fetchall()
        return [dict(r) for r in rows]

    def list_deletion_channels(self, *, amg_id: str = "", account_name: str = "") -> list[dict[str, Any]]:
        q = "SELECT * FROM channel_deletions WHERE 1=1"
        params: list[Any] = []
        if amg_id:
            q += " AND amg_id = ?"
            params.append(amg_id)
        if account_name:
            q += " AND account_name = ?"
            params.append(account_name)
        q += " ORDER BY channel_id"
        with self.connect() as conn:
            rows = conn.execute(q, params).fetchall()
        return [dict(r) for r in rows]

    def upsert_deletion(self, row: dict[str, Any]) -> int:
        row = {**row, "updated_at": utcnow()}
        if "dirty" not in row:
            row["dirty"] = 1
        cols = ["amg_id", "account_name", "channel_name", "channel_id", "deletion_status", "jira", "dirty", "updated_at", "sheet_row"]
        existing_id = row.get("id")
        if existing_id:
            with self.connect() as conn:
                sets = ", ".join(f"{c}=?" for c in cols)
                vals = []
                for c in cols:
                    if c == "dirty":
                        vals.append(int(row.get("dirty") or 0))
                    elif c == "sheet_row":
                        vals.append(row.get("sheet_row"))
                    else:
                        vals.append(row.get(c, "") or "")
                conn.execute(f"UPDATE channel_deletions SET {sets} WHERE id=?", vals + [existing_id])
            return int(existing_id)
        with self.connect() as conn:
            placeholders = ", ".join("?" for _ in cols)
            vals = []
            for c in cols:
                if c == "dirty":
                    vals.append(int(row.get("dirty") or 1))
                elif c == "sheet_row":
                    vals.append(row.get("sheet_row"))
                else:
                    vals.append(row.get(c, "") or "")
            cur = conn.execute(
                f"INSERT INTO channel_deletions ({', '.join(cols)}) VALUES ({placeholders})",
                vals,
            )
            return int(cur.lastrowid)

    def apply_deletion_sheet_rows(self, columns: list[str], values: list[list[str]], *, replace: bool = True) -> int:
        if replace:
            with self.connect() as conn:
                conn.execute("DELETE FROM channel_deletions")
        n = 0
        for i, row_vals in enumerate(values, start=2):
            if not any(str(cell).strip() for cell in row_vals):
                continue
            data = {columns[j]: (row_vals[j] if j < len(row_vals) else "") for j in range(len(columns))}
            amg_id = data.get("amg_id", "") or ""
            account_name = data.get("account_name", "") or ""
            channel_id = data.get("channel_id", "") or ""
            if not (amg_id or account_name or channel_id):
                continue
            payload = {
                "amg_id": amg_id,
                "account_name": account_name,
                "channel_name": data.get("channel_name", "") or "",
                "channel_id": channel_id,
                "deletion_status": data.get("deletion_status", "") or "Pending",
                "jira": (data.get("jira", "") or "").strip(),
                "sheet_row": i,
                "dirty": 0,
            }
            self.upsert_deletion(payload)
            n += 1
        return n

    def deletion_to_sheet_rows(self, columns: list[str]) -> list[list[str]]:
        channels = self.list_deletion_channels()
        return [[str(c.get(col) or "") for col in columns] for c in channels]

    def list_dirty(self) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM deliveries WHERE dirty = 1 ORDER BY updated_at DESC"
            ).fetchall()
        return [dict(r) for r in rows]

    def clear_dirty(self, ids: list[int] | None = None) -> int:
        with self.connect() as conn:
            if ids:
                placeholders = ",".join("?" for _ in ids)
                cur = conn.execute(
                    f"UPDATE deliveries SET dirty = 0 WHERE id IN ({placeholders})",
                    ids,
                )
            else:
                cur = conn.execute("UPDATE deliveries SET dirty = 0 WHERE dirty = 1")
            rowcount = int(cur.rowcount)

        return rowcount

    def stats(self) -> dict[str, Any]:
        with self.connect() as conn:
            total = conn.execute("SELECT COUNT(*) FROM deliveries").fetchone()[0]
            dirty = conn.execute(
                "SELECT COUNT(*) FROM deliveries WHERE dirty = 1"
            ).fetchone()[0]
            by_status = conn.execute(
                "SELECT status, COUNT(*) c FROM deliveries GROUP BY status"
            ).fetchall()
        return {
            "total": total,
            "pending_sync": dirty,
            "by_status": {r["status"] or "(empty)": r["c"] for r in by_status},
        }

    def recent_sync(self, limit: int = 20) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM sync_log ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
        return [dict(r) for r in rows]
