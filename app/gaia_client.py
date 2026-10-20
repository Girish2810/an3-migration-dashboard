from __future__ import annotations

from typing import Any

import requests
import urllib3

from app.config import Config

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


class GaiaClient:
    _BASE = "/api/v1"

    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg
        self.session = requests.Session()
        if cfg.gaia_cert and cfg.gaia_key:
            self.session.cert = (cfg.gaia_cert, cfg.gaia_key)
        self.session.verify = False

    @property
    def configured(self) -> bool:
        return bool(self.cfg.gaia_host and self.cfg.gaia_cert and self.cfg.gaia_key)

    def _url(self, path: str) -> str:
        return f"{self.cfg.gaia_host.rstrip('/')}{self._BASE}{path}"

    def post_distribution(self, payload: dict[str, Any]) -> dict[str, Any]:
        resp = self.session.post(self._url("/distributions"), json=payload, timeout=60)
        if resp.status_code >= 400:
            try:
                detail = resp.json()
            except Exception:
                detail = resp.text
            raise RuntimeError(f"Gaia POST /distributions -> {resp.status_code}: {detail}")
        return resp.json()

    def get_distributions(self, amg_id: str, channel_id: str = "") -> list[dict[str, Any]]:
        params: dict[str, Any] = {"amg_id": amg_id, "per_page": 100}
        if channel_id:
            params["channel_id"] = channel_id
        resp = self.session.get(self._url("/distributions"), params=params, timeout=60)
        if resp.status_code >= 400:
            raise RuntimeError(f"Gaia GET /distributions -> {resp.status_code}: {resp.text}")
        return resp.json().get("result", [])
