from __future__ import annotations

import json
from typing import Any

import requests

from app.config import Config


class FabricClient:
    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg
        self.session = requests.Session()
        if cfg.fabric_cert and cfg.fabric_key:
            self.session.cert = (cfg.fabric_cert, cfg.fabric_key)
        self.session.verify = False

    @property
    def configured(self) -> bool:
        return bool(self.cfg.fabric_host and self.cfg.fabric_cert and self.cfg.fabric_key)

    def get_delivery(self, amg_id: str, distribution_id: str) -> dict[str, Any]:
        url = f"https://{self.cfg.fabric_host}/api/v3/deliveries/{distribution_id}"
        resp = self.session.get(url, headers={"X-Account-Id": amg_id}, timeout=60)
        if resp.status_code >= 400:
            raise RuntimeError(f"Fabric GET {distribution_id} -> {resp.status_code}")
        return resp.json()

    @staticmethod
    def enrich(body: dict[str, Any]) -> dict[str, Any]:
        cfg_body = body.get("configuration") or {}
        specs = cfg_body.get("distribution_specification") or []
        types = [s.get("distribution_type") for s in specs if s.get("distribution_type")]
        cdn = (specs[0].get("cdn") if specs else {}) or {}
        nm = cdn.get("non_monetized_url") or ""
        mon = cdn.get("monetized_url") or ""
        mono = cfg_body.get("monetization_specification") or {}
        ssai = (cfg_body.get("ssai_configurations") or [{}])[0]
        ssai_mono = ssai.get("monetization_specification") or {}
        tsr = mono.get("ts_delivery_response") or ssai_mono.get("ts_delivery_response") or {}
        ingress = ""
        if isinstance(tsr, dict):
            ingress = (tsr.get("ingress_properties") or {}).get("stream_url") or ""
        resources = (body.get("resources") or {}).get("distributions") or []
        resource_summary = [
            {
                "cdn_id": r.get("cdn_distribution_id"),
                "domain": r.get("cloudfront_domain"),
                "cname": r.get("route53_cname"),
                "used_by": r.get("used_by"),
            }
            for r in resources
        ]
        non_hls_types = {"srt_listener", "srt_caller", "zixi_push", "zixi_pull", "entitlement", "media_package"}
        is_hls = not any(t in non_hls_types for t in types) and "cdn" in types if types else bool(nm)
        if not types and not nm:
            is_hls = False
        stream_type = "hls" if is_hls else "non-hls"
        tags = body.get("tags") or {}
        return {
            "channel_name": tags.get("ChannelName") or "",
            "platform": tags.get("Platform") or tags.get("Customer") or "",
            "platform_id": body.get("platform_id") or "",
            "stream_type": stream_type,
            "distribution_types": types,
            "non_monetized_url": nm,
            "monetized_url": mon,
            "ts_ingress_url": ingress,
            "migration_resources": json.dumps(resource_summary),
            "state": (body.get("metadata") or {}).get("state") or "",
            "cdn_count": len(resources),
        }
