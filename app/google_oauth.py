from __future__ import annotations

"""One-time Google OAuth for private Sheets access (no service account)."""

from pathlib import Path

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow

from app.config import Config

SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]


def load_user_credentials(cfg: Config) -> Credentials | None:
    token_path = Path(cfg.oauth_token_json) if cfg.oauth_token_json else None
    if not token_path or not token_path.is_file():
        return None
    creds = Credentials.from_authorized_user_file(str(token_path), SCOPES)
    if creds and creds.expired and creds.refresh_token:
        creds.refresh(Request())
        token_path.write_text(creds.to_json())
    return creds


def run_oauth_login(cfg: Config) -> Path:
    """
    Opens a browser once. User logs into Google and grants Sheets access.
    Saves access + refresh token to oauth_token_json for unattended reuse.
    """
    secrets = Path(cfg.oauth_client_secrets_json) if cfg.oauth_client_secrets_json else None
    if not secrets or not secrets.is_file():
        raise RuntimeError(
            "Set google_sheets.oauth_client_secrets_json to your OAuth Desktop client JSON "
            "(from Google Cloud → APIs → Credentials → OAuth client ID → Desktop app)."
        )
    token_path = Path(cfg.oauth_token_json) if cfg.oauth_token_json else Path("secrets/google-token.json")
    token_path.parent.mkdir(parents=True, exist_ok=True)

    flow = InstalledAppFlow.from_client_secrets_file(str(secrets), SCOPES)
    # Local loopback — free, no public redirect URL needed for Desktop clients
    creds = flow.run_local_server(port=0, prompt="consent", access_type="offline")
    token_path.write_text(creds.to_json())
    return token_path
