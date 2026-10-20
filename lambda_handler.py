"""Lambda entry point. Bootstraps Google OAuth token from env var, then serves FastAPI."""
from __future__ import annotations

import base64
import os
from pathlib import Path


def _bootstrap() -> None:
    """Decode Google credentials from env vars into /tmp so the app can use them."""
    Path("/tmp/secrets").mkdir(parents=True, exist_ok=True)

    sa_b64 = os.environ.get("GOOGLE_SA_BASE64", "")
    if sa_b64:
        try:
            Path("/tmp/secrets/google-service-account.json").write_bytes(base64.b64decode(sa_b64))
        except Exception:
            pass

    token_b64 = os.environ.get("GOOGLE_TOKEN_BASE64", "")
    if token_b64:
        try:
            dest = Path(os.environ.get("OAUTH_TOKEN_JSON", "/tmp/secrets/google-token.json"))
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(base64.b64decode(token_b64))
        except Exception:
            pass


_bootstrap()

from app.main import app  # noqa: E402
from mangum import Mangum  # noqa: E402

handler = Mangum(app, lifespan="off")
