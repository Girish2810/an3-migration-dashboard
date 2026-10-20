from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


ROOT = Path(__file__).resolve().parents[1]


def _expand(p: str) -> str:
    return str(Path(os.path.expanduser(p)).resolve()) if p else ""


@dataclass
class Config:
    raw: dict[str, Any]
    db_path: Path
    spreadsheet_id: str
    worksheet_name: str
    sheet_columns: list[str]
    deletion_worksheet_name: str
    deletion_columns: list[str]
    deletion_spreadsheet_id: str
    deletion_sheet_gid: str
    # Prefer OAuth (your Google login + refresh token). Service account optional fallback.
    oauth_client_secrets_json: str
    oauth_token_json: str
    service_account_json: str
    jira_cloud_id: str
    jira_site_url: str
    jira_email: str
    jira_api_token: str
    jira_project_key: str
    jira_issue_type: str
    jira_parent_creation: str
    jira_parent_switchover: str
    jira_poll_jql: str
    fabric_host: str
    fabric_cert: str
    fabric_key: str
    gaia_host: str
    gaia_cert: str
    gaia_key: str
    server_host: str
    server_port: int
    public_url: str
    auth_enabled: bool
    auth_username: str
    auth_password: str
    claude_enabled: bool
    claude_model: str
    groq_api_key: str

    @classmethod
    def load(cls, path: Path | None = None) -> Config:
        cfg_path = path or ROOT / "config.yaml"
        if not cfg_path.exists():
            cfg_path = ROOT / "config.example.yaml"
        raw = yaml.safe_load(cfg_path.read_text()) or {}
        gs = raw.get("google_sheets") or {}
        jira = raw.get("jira") or {}
        fabric = raw.get("fabric") or {}
        gaia = raw.get("gaia") or {}
        server = raw.get("server") or {}
        db = raw.get("database") or {}
        claude = raw.get("claude") or {}
        auth = raw.get("auth") or {}
        token = os.environ.get("JIRA_API_TOKEN") or jira.get("api_token") or ""
        default_token = str(ROOT / "secrets" / "google-token.json")
        auth_user = os.environ.get("DASHBOARD_USER") or auth.get("username") or "migration"
        auth_pass = os.environ.get("DASHBOARD_PASSWORD") or auth.get("password") or ""
        auth_on = bool(auth.get("enabled", False))
        if os.environ.get("DASHBOARD_PASSWORD"):
            auth_on = True
        # Lambda overrides: env vars take priority so /tmp paths work on Lambda
        db_path_override = os.environ.get("DB_PATH")
        oauth_token_override = os.environ.get("OAUTH_TOKEN_JSON")
        return cls(
            raw=raw,
            db_path=Path(db_path_override) if db_path_override else Path(_expand(db.get("path") or str(ROOT / "data" / "migration.db"))),
            spreadsheet_id=gs.get("spreadsheet_id") or "",
            worksheet_name=gs.get("worksheet_name") or "Deliveries",
            sheet_columns=list(gs.get("columns") or []),
            deletion_worksheet_name=gs.get("deletion_worksheet_name") or "Channel deletion sheet",
            deletion_columns=list(gs.get("deletion_columns") or ["amg_id", "account_name", "channel_name", "channel_id", "deletion_status", "jira"]),
            deletion_spreadsheet_id=gs.get("deletion_spreadsheet_id") or gs.get("spreadsheet_id") or "",
            deletion_sheet_gid=str(gs.get("deletion_sheet_gid") or ""),
            oauth_client_secrets_json=_expand(gs.get("oauth_client_secrets_json") or ""),
            oauth_token_json=oauth_token_override or _expand(gs.get("oauth_token_json") or default_token),
            service_account_json=os.environ.get("SERVICE_ACCOUNT_JSON") or _expand(gs.get("service_account_json") or ""),
            jira_cloud_id=jira.get("cloud_id") or "",
            jira_site_url=(jira.get("site_url") or "").rstrip("/"),
            jira_email=jira.get("email") or "",
            jira_api_token=token,
            jira_project_key=jira.get("project_key") or "AN3OB",
            jira_issue_type=jira.get("issue_type") or "Migration-Support",
            jira_parent_creation=jira.get("parent_creation") or "AN3OB-8205",
            jira_parent_switchover=jira.get("parent_switchover") or "AN3OB-8206",
            jira_poll_jql=jira.get("poll_jql") or "",
            fabric_host=fabric.get("host") or "",
            fabric_cert=_expand(fabric.get("cert") or ""),
            fabric_key=_expand(fabric.get("key") or ""),
            gaia_host=gaia.get("host") or "",
            gaia_cert=_expand(gaia.get("cert") or ""),
            gaia_key=_expand(gaia.get("key") or ""),
            server_host=server.get("host") or "127.0.0.1",
            server_port=int(server.get("port") or 8790),
            public_url=(server.get("public_url") or "").rstrip("/"),
            auth_enabled=auth_on and bool(auth_pass),
            auth_username=auth_user,
            auth_password=auth_pass,
            claude_enabled=bool(claude.get("enabled", True)),
            claude_model=str(claude.get("model") or os.environ.get("CLAUDE_MODEL") or "haiku"),
            groq_api_key=os.environ.get("GROQ_API_KEY") or str(raw.get("groq", {}).get("api_key") or ""),
        )
