from __future__ import annotations

from pathlib import Path
from typing import Any

from google.oauth2 import service_account
from googleapiclient.discovery import build

from app.config import Config
from app.google_oauth import SCOPES, load_user_credentials


class SheetsClient:
    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg
        self._service = None
        self._auth_mode = ""

    @property
    def configured(self) -> bool:
        if not self.cfg.spreadsheet_id:
            return False
        # Prefer OAuth user token (private sheet, no service account)
        if self.cfg.oauth_token_json and Path(self.cfg.oauth_token_json).is_file():
            return True
        if self.cfg.oauth_client_secrets_json and Path(self.cfg.oauth_client_secrets_json).is_file():
            # secrets present but not logged in yet — not ready for API calls
            return False
        return bool(
            self.cfg.service_account_json and Path(self.cfg.service_account_json).is_file()
        )

    @property
    def auth_mode(self) -> str:
        if self._auth_mode:
            return self._auth_mode
        if self.cfg.oauth_token_json and Path(self.cfg.oauth_token_json).is_file():
            return "oauth"
        if self.cfg.service_account_json and Path(self.cfg.service_account_json).is_file():
            return "service_account"
        return "none"

    def _creds(self):
        oauth = load_user_credentials(self.cfg)
        if oauth:
            self._auth_mode = "oauth"
            return oauth
        if self.cfg.service_account_json and Path(self.cfg.service_account_json).is_file():
            self._auth_mode = "service_account"
            return service_account.Credentials.from_service_account_file(
                self.cfg.service_account_json, scopes=SCOPES
            )
        raise RuntimeError(
            "Google Sheets not authorized. Run: python cli.py auth-google "
            "(OAuth once), or set service_account_json."
        )

    def _svc(self):
        if self._service is None:
            self._service = build("sheets", "v4", credentials=self._creds(), cache_discovery=False)
        return self._service

    def read_all(self) -> tuple[list[str], list[list[str]]]:
        if not self.cfg.spreadsheet_id:
            raise RuntimeError("spreadsheet_id missing in config")
        rng = f"{self.cfg.worksheet_name}!A1:Z"
        result = (
            self._svc()
            .spreadsheets()
            .values()
            .get(spreadsheetId=self.cfg.spreadsheet_id, range=rng)
            .execute()
        )
        values = result.get("values") or []
        if not values:
            return self.cfg.sheet_columns, []
        header = [h.strip() for h in values[0]]
        return header, values[1:]

    def write_all(self, header: list[str], rows: list[list[str]]) -> None:
        body = {"values": [header] + rows}
        rng = f"{self.cfg.worksheet_name}!A1"
        (
            self._svc()
            .spreadsheets()
            .values()
            .update(
                spreadsheetId=self.cfg.spreadsheet_id,
                range=rng,
                valueInputOption="USER_ENTERED",
                body=body,
            )
            .execute()
        )

    def read_tab(self, worksheet_name: str) -> tuple[list[str], list[list[str]]]:
        if not self.cfg.spreadsheet_id:
            raise RuntimeError("spreadsheet_id missing in config")
        rng = f"'{worksheet_name}'!A1:Z"
        result = (
            self._svc()
            .spreadsheets()
            .values()
            .get(spreadsheetId=self.cfg.spreadsheet_id, range=rng)
            .execute()
        )
        values = result.get("values") or []
        if not values:
            return [], []
        header = [h.strip() for h in values[0]]
        return header, values[1:]

    def write_tab(self, worksheet_name: str, header: list[str], rows: list[list[str]]) -> None:
        body = {"values": [header] + rows}
        rng = f"'{worksheet_name}'!A1"
        (
            self._svc()
            .spreadsheets()
            .values()
            .update(
                spreadsheetId=self.cfg.spreadsheet_id,
                range=rng,
                valueInputOption="USER_ENTERED",
                body=body,
            )
            .execute()
        )

    def append_rows(self, rows: list[list[str]]) -> None:
        (
            self._svc()
            .spreadsheets()
            .values()
            .append(
                spreadsheetId=self.cfg.spreadsheet_id,
                range=f"{self.cfg.worksheet_name}!A1",
                valueInputOption="USER_ENTERED",
                insertDataOption="INSERT_ROWS",
                body={"values": rows},
            )
            .execute()
        )
