"""HTTP Basic Auth for shared hosting."""

from __future__ import annotations

import secrets
from typing import Callable

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPBasic, HTTPBasicCredentials

from app.config import Config

_basic = HTTPBasic(auto_error=False)


def require_basic_auth(cfg: Config) -> Callable:
    """FastAPI dependency — no-op when auth.disabled."""

    def _dep(
        request: Request,
        credentials: HTTPBasicCredentials | None = Depends(_basic),
    ) -> None:
        if not cfg.auth_enabled:
            return
        user_ok = credentials is not None and secrets.compare_digest(
            credentials.username.encode("utf-8"),
            cfg.auth_username.encode("utf-8"),
        )
        pass_ok = credentials is not None and secrets.compare_digest(
            credentials.password.encode("utf-8"),
            cfg.auth_password.encode("utf-8"),
        )
        if not (user_ok and pass_ok):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid username or password",
                headers={"WWW-Authenticate": 'Basic realm="AN3 Migration Dashboard"'},
            )

    return _dep
