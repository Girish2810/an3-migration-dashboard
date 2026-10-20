from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, HTTPException, Query, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from app.config import Config
from app.db import Database
from app.sync import SyncEngine
from app.workflow_client import WorkflowClient

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "web"

cfg = Config.load()
db = Database(cfg.db_path)
sync = SyncEngine(cfg, db)

app = FastAPI(title="AN3 Migration Dashboard", version="0.2.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["https://girish2810.github.io", "http://localhost:8790", "http://localhost:3000"],
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)


@app.middleware("http")
async def basic_auth_middleware(request, call_next):
    """Protect all routes including static files when auth is enabled."""
    if not cfg.auth_enabled:
        return await call_next(request)
    if request.url.path == "/api/healthz":
        return await call_next(request)
    header = request.headers.get("Authorization") or ""
    import base64
    import secrets as sec

    ok = False
    if header.startswith("Basic "):
        try:
            raw = base64.b64decode(header[6:].encode("ascii")).decode("utf-8")
            user, _, password = raw.partition(":")
            ok = sec.compare_digest(user, cfg.auth_username) and sec.compare_digest(
                password, cfg.auth_password
            )
        except Exception:
            ok = False
    if not ok:
        return Response(
            content="Unauthorized",
            status_code=401,
            headers={"WWW-Authenticate": 'Basic realm="AN3 Migration Dashboard"'},
            media_type="text/plain",
        )
    return await call_next(request)


class DeliveryUpdate(BaseModel):
    status: str = ""
    remarks: str = ""
    initial_start_date: str = ""
    eta_provided_date: str = ""
    completed_date: str = ""
    distribution_id: str = ""
    jira_creation_key: str = ""
    jira_switchover_key: str = ""
    jira_extra_key: str = ""
    channel_id: str = ""
    channel_name: str = ""
    platform: str = ""
    platform_id: str = ""
    stream_type: str = ""


class TicketEntry(BaseModel):
    """Add existing Jira ticket(s) + optional delivery details (local, pending Drive sync)."""

    account_name: str
    amg_id: str
    jira_creation_key: str = ""
    jira_switchover_key: str = ""
    jira_extra_key: str = ""
    channel_id: str = ""
    channel_name: str = ""
    platform: str = ""
    platform_id: str = ""
    distribution_id: str = ""
    stream_type: str = "hls"
    status: str = "non-ssai pending"
    remarks: str = ""
    initial_start_date: str = ""
    eta_provided_date: str = ""
    completed_date: str = ""


class DistributionEntry(BaseModel):
    channel_id: str = ""
    distribution_id: str = ""
    status: str = "not-started"


class AddAccountRequest(BaseModel):
    """Add a new account with multiple distributions and up to 3 account-level Jira keys."""

    amg_id: str
    account_name: str
    jira_keys: list[str] = []
    distributions: list[DistributionEntry] = []
    initial_start_date: str = ""
    eta_provided_date: str = ""
    remarks: str = ""


class BulkTicketEntries(BaseModel):
    entries: list[TicketEntry]


@app.post("/api/sheet/refresh")
def refresh_sheet_auto() -> dict[str, Any]:
    """One click: auto paste/send in Claude Desktop → wait for CSV → load dashboard."""
    try:
        result = sync.start_sheet_refresh()
        if not result.get("ok"):
            raise HTTPException(400, result.get("error") or "Refresh failed")
        return result
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(400, str(exc)) from exc


@app.post("/api/sheet/push")
def push_sheet_auto() -> dict[str, Any]:
    """One click: auto paste/send push prompt in Claude Desktop."""
    try:
        result = sync.start_sheet_push()
        if not result.get("ok"):
            raise HTTPException(400, result.get("error") or "Push failed")
        return result
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(400, str(exc)) from exc


@app.post("/api/sync/mark-synced")
def mark_synced() -> dict[str, Any]:
    return sync.mark_synced()


@app.get("/api/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/health")
def health() -> dict[str, Any]:
    from app import claude_sync

    st = db.stats()
    return {
        "status": "ok",
        "mode": "shared",
        "public_url": cfg.public_url,
        "auth_enabled": cfg.auth_enabled,
        "claude_desktop": claude_sync.claude_desktop_available(),
        "claude_enabled": cfg.claude_enabled,
        "pending_sync": st.get("pending_sync", 0),
        "jira_configured": sync.jira.configured,
        "fabric_configured": sync.fabric.configured,
        "spreadsheet_id": cfg.spreadsheet_id,
        "spreadsheet_url": (
            f"https://docs.google.com/spreadsheets/d/{cfg.spreadsheet_id}"
            if cfg.spreadsheet_id
            else ""
        ),
        "jira_parents": {
            "creation": cfg.jira_parent_creation,
            "switchover": cfg.jira_parent_switchover,
        },
    }


@app.get("/api/stats")
def stats() -> dict[str, Any]:
    return db.stats()


@app.get("/api/accounts")
def accounts() -> dict[str, Any]:
    return {"accounts": db.list_accounts()}


@app.get("/api/deliveries")
def deliveries(
    account_name: str = Query(""),
    amg_id: str = Query(""),
) -> dict[str, Any]:
    return {"deliveries": db.list_deliveries(account_name=account_name, amg_id=amg_id)}


@app.get("/api/deliveries/{delivery_id}")
def get_delivery(delivery_id: int) -> dict[str, Any]:
    rows = db.list_deliveries()
    row = next((r for r in rows if r["id"] == delivery_id), None)
    if not row:
        raise HTTPException(404, "not found")
    return {"delivery": row}


@app.post("/api/accounts/add")
def add_account(body: AddAccountRequest) -> dict[str, Any]:
    """Add a new account with multiple distributions; appends rows to Google Sheet immediately."""
    try:
        return sync.add_account_with_distributions(body.model_dump())
    except Exception as exc:
        raise HTTPException(400, str(exc)) from exc


@app.post("/api/entries")
def add_ticket_entry(body: TicketEntry) -> dict[str, Any]:
    """Add locally; marks pending. Does not call Claude/Drive."""
    try:
        return sync.append_entry(body.model_dump())
    except Exception as exc:
        raise HTTPException(400, str(exc)) from exc


@app.post("/api/entries/bulk")
def add_ticket_entries_bulk(body: BulkTicketEntries) -> dict[str, Any]:
    """Add many tickets at once locally; one Claude Drive sync later."""
    try:
        return sync.append_entries_bulk([e.model_dump() for e in body.entries])
    except Exception as exc:
        raise HTTPException(400, str(exc)) from exc


@app.post("/api/sync/prepare-claude")
def prepare_claude_sync() -> dict[str, Any]:
    """Build pending pack + prompt for Claude Drive (one shot). No Claude call here."""
    try:
        return sync.prepare_claude_drive_sync()
    except Exception as exc:
        raise HTTPException(400, str(exc)) from exc


@app.post("/api/csv/import")
async def import_csv_upload(
    file: UploadFile = File(...),
    replace: bool = Query(True),
) -> dict[str, Any]:
    """Refresh from Sheet CSV (button only — not on a timer)."""
    try:
        raw = await file.read()
        text = raw.decode("utf-8-sig")
        return sync.import_csv_text(text, replace=replace)
    except Exception as exc:
        raise HTTPException(400, str(exc)) from exc


@app.get("/api/csv/export")
def export_csv() -> Response:
    """Download current dashboard as CSV to paste/import into Google Sheet."""
    text = sync.export_csv_text()
    return Response(
        content=text,
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="an3-migration-export.csv"'},
    )


@app.patch("/api/deliveries/{delivery_id}")
def update_delivery(delivery_id: int, body: DeliveryUpdate) -> dict[str, Any]:
    try:
        return sync.update_and_push_status(
            delivery_id, {k: v for k, v in body.model_dump().items() if v != ""}
        )
    except Exception as exc:
        raise HTTPException(400, str(exc)) from exc


@app.post("/api/sync/sheets/pull")
def sync_sheets_pull() -> dict[str, Any]:
    try:
        return sync.pull_from_sheets()
    except Exception as exc:
        raise HTTPException(400, str(exc)) from exc


@app.post("/api/sync/sheets/push")
def sync_sheets_push() -> dict[str, Any]:
    try:
        return sync.push_to_sheets()
    except Exception as exc:
        raise HTTPException(400, str(exc)) from exc


@app.post("/api/sync/jira/pull")
def sync_jira_pull() -> dict[str, Any]:
    try:
        return sync.pull_from_jira()
    except Exception as exc:
        raise HTTPException(400, str(exc)) from exc


@app.post("/api/pipeline/sync")
def pipeline_sync() -> dict[str, Any]:
    """Run jira-sync: read Jira step tickets → update AN3_Workflow sheet."""
    import re, sys
    from pathlib import Path
    ROOT_DIR = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(ROOT_DIR))
    from app.jira_client import JiraClient

    jira = JiraClient(cfg)
    if not jira.configured:
        raise HTTPException(400, "Jira not configured")

    wf = WorkflowClient(cfg)
    _STEP_RE  = re.compile(r'Step\s+(\d+)\s*\|\s*(.+?)\s*\|\s*(.+)', re.I)
    _AMG_RE   = re.compile(r'\b(amg\d{4,6})\b', re.I)
    _KEY_RE   = re.compile(r'\b(AN3OB-\d+)\b', re.I)
    _DONE_ST  = {'resolved','done','closed','complete','completed','moved to production','rejected'}

    def _extract_text(node):
        parts = []
        def walk(n):
            if isinstance(n, dict):
                if n.get('type') == 'text': parts.append(n.get('text', ''))
                for v in n.values():
                    if isinstance(v, (list, dict)): walk(v)
            elif isinstance(n, list):
                for item in n: walk(item)
        walk(node)
        return ' '.join(parts)

    def _step_to_stage(n):
        if n <= 1:  return 'identified'
        if n <= 3:  return 'data_gathering'
        if n <= 7:  return 'em_verification'
        if n <= 12: return 'hls_check'
        if n <= 14: return 'ready'
        return 'done'

    issues = jira.search(
        'project = AN3OB AND issueType = "Migration-Support" AND summary ~ "Step" ORDER BY summary ASC',
        max_results=500,
    )

    buckets: dict = {}
    for iss in issues:
        f = iss['fields']
        m = _STEP_RE.match(f.get('summary', ''))
        if not m: continue
        num = int(m.group(1)); slug = m.group(2).strip(); step_name = m.group(3).strip()
        status = (f.get('status', {}).get('name', '') or '').lower()
        key_norm = re.sub(r'[^a-z0-9]', '', slug.lower())
        if key_norm not in buckets:
            buckets[key_norm] = {'slug': slug, 'steps': [], 'amg_ids': set()}
        buckets[key_norm]['steps'].append((num, step_name, status, iss['key']))
        for amg in _AMG_RE.findall(_extract_text(f.get('description'))):
            buckets[key_norm]['amg_ids'].add(amg.lower())

    accounts = []
    for info in buckets.values():
        steps = sorted(info['steps'])
        active = [(n,nm,st,k) for n,nm,st,k in steps if st not in _DONE_ST]
        done   = [(n,nm,st,k) for n,nm,st,k in steps if st in _DONE_ST]
        if active:   cur_step, cur_name, _, cur_key = active[0]; stage = _step_to_stage(cur_step)
        elif done:   cur_step, cur_name, _, cur_key = done[-1];  stage = 'done'
        else: continue
        amg_list = sorted(info['amg_ids'])
        if not amg_list: continue
        accounts.append({'amg_id': amg_list[0], 'account_name': ' '.join(w.capitalize() for w in re.split(r'[-_\s]+', info['slug'])),
                         'stage': stage, 'account_type': 'migration', 'jira_tickets': cur_key, 'notes': f'Step {cur_step}: {cur_name}'})

    # Enrich with delivery tickets from main sheet
    try:
        gs_cfg = cfg.raw.get('google_sheets', {})
        delivery_rows = wf._gviz(gs_cfg.get('worksheet_name', 'Deliveries'))
        delivery_map: dict = {}
        for row in delivery_rows:
            amg = (row.get('amg_id') or '').strip().lower()
            if not amg: continue
            for col in ('jira_links', 'jira_creation_key', 'jira_switchover_key'):
                for key in _KEY_RE.findall(row.get(col) or ''):
                    delivery_map.setdefault(amg, [])
                    if key.upper() not in delivery_map[amg]:
                        delivery_map[amg].append(key.upper())
        for a in accounts:
            tickets = delivery_map.get(a['amg_id'].strip().lower(), [])
            if tickets: a['jira_tickets'] = ', '.join(tickets[:3])
    except Exception:
        pass

    result = wf.bulk_sync(accounts, actor='api-sync', force_ticket_update=True)
    return {'ok': True, 'added': len(result['added']), 'updated': len(result['updated']),
            'skipped': len(result['skipped']), 'total': len(accounts)}


@app.post("/api/sync/full")
def sync_full() -> dict[str, Any]:
    try:
        return sync.full_sync()
    except Exception as exc:
        raise HTTPException(400, str(exc)) from exc


@app.post("/api/deliveries/{delivery_id}/enrich")
def enrich_one(delivery_id: int) -> dict[str, Any]:
    try:
        return sync.enrich_delivery(delivery_id)
    except Exception as exc:
        raise HTTPException(400, str(exc)) from exc


@app.post("/api/accounts/{account_name}/enrich")
def enrich_account(account_name: str) -> dict[str, Any]:
    try:
        return sync.enrich_account(account_name)
    except Exception as exc:
        raise HTTPException(400, str(exc)) from exc


@app.get("/api/sync/log")
def sync_log() -> dict[str, Any]:
    return {"log": db.recent_sync()}


@app.on_event("startup")
def bootstrap_csv_if_empty() -> None:
    if db.stats()["total"] > 0:
        return
    csv_path = ROOT.parent / "OTT Studios - tracking sheet.csv"
    if csv_path.exists():
        sync.import_csv(csv_path, replace=True)


class DeletionUpdate(BaseModel):
    deletion_status: str = ""
    jira: str = ""
    channel_name: str = ""
    channel_id: str = ""


class DeletionEntry(BaseModel):
    amg_id: str
    account_name: str = ""
    channel_name: str = ""
    channel_id: str = ""
    deletion_status: str = "Pending"
    jira: str = ""


@app.get("/api/deletion/accounts")
def deletion_accounts() -> dict[str, Any]:
    return {"accounts": db.list_deletion_accounts()}


@app.get("/api/deletion/channels")
def deletion_channels(
    amg_id: str = Query(""),
    account_name: str = Query(""),
) -> dict[str, Any]:
    return {"channels": db.list_deletion_channels(amg_id=amg_id, account_name=account_name)}


@app.patch("/api/deletion/channels/{channel_id}")
def update_deletion_channel(channel_id: int, body: DeletionUpdate) -> dict[str, Any]:
    try:
        channels = db.list_deletion_channels()
        row = next((r for r in channels if r["id"] == channel_id), None)
        if not row:
            raise HTTPException(404, "not found")
        updates = {k: v for k, v in body.model_dump().items() if v}
        row.update(updates)
        row["dirty"] = 1
        db.upsert_deletion(row)
        return {"ok": True}
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(400, str(exc)) from exc


@app.post("/api/deletion/channels")
def add_deletion_channel(body: DeletionEntry) -> dict[str, Any]:
    try:
        row_id = db.upsert_deletion(body.model_dump())
        return {"ok": True, "id": row_id}
    except Exception as exc:
        raise HTTPException(400, str(exc)) from exc


@app.post("/api/deletion/sheet/refresh")
def deletion_sheet_refresh() -> dict[str, Any]:
    try:
        return sync.pull_deletion_from_sheets()
    except Exception as exc:
        raise HTTPException(400, str(exc)) from exc


@app.post("/api/deletion/sheet/push")
def deletion_sheet_push() -> dict[str, Any]:
    try:
        return sync.push_deletion_to_sheets()
    except Exception as exc:
        raise HTTPException(400, str(exc)) from exc


class NonHlsDistEntry(BaseModel):
    amg_id: str
    channel_id: str
    platform_id: str
    platform_amg_id: str = ""
    distribution_type: str  # srt_listener | srt_caller | zixi_push | zixi_pull | entitlement
    # SRT Listener
    srt_listener_port: int = 0
    srt_listener_cidr: str = ""
    srt_listener_latency: int = 2000
    # SRT Caller
    srt_caller_port: int = 0
    srt_caller_address: str = ""
    srt_caller_latency: int = 2000
    srt_caller_stream_id: str = ""
    # Zixi Push
    zixi_push_port: int = 0
    zixi_push_address: str = ""
    zixi_push_latency: int = 2000
    zixi_push_stream_id: str = ""
    # Zixi Pull
    zixi_pull_remote_id: str = ""
    zixi_pull_cidr: str = ""
    zixi_pull_stream_id: str = ""
    zixi_pull_latency: int = 2000
    # Entitlement
    entitlement_id: str = ""


class BulkNonHlsDist(BaseModel):
    entries: list[NonHlsDistEntry]


@app.get("/api/nonhls/distributions")
def nonhls_get_distributions(
    amg_id: str = Query(...),
    channel_id: str = Query(""),
) -> dict[str, Any]:
    from app.gaia_client import GaiaClient
    gaia = GaiaClient(cfg)
    if not gaia.configured:
        raise HTTPException(400, "Gaia not configured — check gaia.host/cert/key in config.yaml")
    try:
        results = gaia.get_distributions(amg_id, channel_id)
        non_hls_types = {"srt_listener", "srt_caller", "zixi_push", "zixi_pull", "entitlement"}
        filtered = []
        for d in results:
            specs = (d.get("configuration") or {}).get("distribution_specification") or []
            types = {s.get("distribution_type") for s in specs}
            if types & non_hls_types:
                filtered.append(d)
        return {"distributions": filtered, "total": len(filtered)}
    except Exception as exc:
        raise HTTPException(400, str(exc)) from exc


@app.post("/api/nonhls/distributions/seed")
def nonhls_seed_distributions(body: BulkNonHlsDist) -> dict[str, Any]:
    from app.gaia_client import GaiaClient
    gaia = GaiaClient(cfg)
    if not gaia.configured:
        raise HTTPException(400, "Gaia not configured — check gaia.host/cert/key in config.yaml")

    results = []
    for entry in body.entries:
        e = entry.model_dump()
        dist_type = e["distribution_type"]

        type_config: dict[str, Any] = {}
        if dist_type == "srt_listener":
            type_config = {"destination_port": e["srt_listener_port"], "cidr": e["srt_listener_cidr"], "min_latency": e["srt_listener_latency"]}
        elif dist_type == "srt_caller":
            type_config = {"destination_port": e["srt_caller_port"], "destination_address": e["srt_caller_address"], "min_latency": e["srt_caller_latency"], "stream_id": e["srt_caller_stream_id"]}
        elif dist_type == "zixi_push":
            type_config = {"destination_port": e["zixi_push_port"], "destination_address": e["zixi_push_address"], "max_latency": e["zixi_push_latency"], "stream_id": e["zixi_push_stream_id"]}
        elif dist_type == "zixi_pull":
            type_config = {"remote_id": e["zixi_pull_remote_id"], "cidr": e["zixi_pull_cidr"], "stream_id": e["zixi_pull_stream_id"], "max_latency": e["zixi_pull_latency"]}
        elif dist_type == "entitlement":
            type_config = {"entitlement_id": e["entitlement_id"]}

        payload: dict[str, Any] = {
            "channel_id": e["channel_id"],
            "platform_id": e["platform_id"],
            "platform_amg_id": e["platform_amg_id"] or e["amg_id"],
            "configuration": {
                "distribution_specification": [{**{"distribution_type": dist_type}, **{dist_type: type_config}}]
            },
        }

        try:
            resp = gaia.post_distribution(payload)
            results.append({
                "amg_id": e["amg_id"], "channel_id": e["channel_id"],
                "platform_id": e["platform_id"], "distribution_type": dist_type,
                "ok": True, "distribution_id": resp.get("distribution_id", ""),
            })
        except Exception as exc:
            results.append({
                "amg_id": e["amg_id"], "channel_id": e["channel_id"],
                "platform_id": e["platform_id"], "distribution_type": dist_type,
                "ok": False, "error": str(exc),
            })

    succeeded = sum(1 for r in results if r["ok"])
    return {"results": results, "total": len(results), "succeeded": succeeded, "failed": len(results) - succeeded}


app.mount("/static", StaticFiles(directory=STATIC), name="static")


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC / "index.html")
