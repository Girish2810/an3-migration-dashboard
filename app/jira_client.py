from __future__ import annotations

import re
from datetime import date
from typing import Any

import requests

from app.config import Config

DIST_RE = re.compile(r"\b(amg\d+d\d+)\b", re.I)
DONE_STATUSES = {"done", "resolved", "closed", "complete", "completed"}


class JiraClient:
    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg
        self.base = f"https://api.atlassian.com/ex/jira/{cfg.jira_cloud_id}/rest/api/3"
        self.session = requests.Session()
        if cfg.jira_email and cfg.jira_api_token:
            self.session.auth = (cfg.jira_email, cfg.jira_api_token)
        self.session.headers.update({"Accept": "application/json", "Content-Type": "application/json"})

    @property
    def configured(self) -> bool:
        return bool(self.cfg.jira_email and self.cfg.jira_api_token and self.cfg.jira_cloud_id)

    def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        url = f"{self.base}{path}"
        resp = self.session.request(method, url, timeout=60, **kwargs)
        if resp.status_code >= 400:
            raise RuntimeError(f"Jira {method} {path} -> {resp.status_code}: {resp.text[:500]}")
        if resp.text:
            return resp.json()
        return None

    def search(self, jql: str, *, max_results: int = 100) -> list[dict[str, Any]]:
        data = self._request(
            "POST",
            "/search/jql",
            json={
                "jql": jql,
                "maxResults": max_results,
                "fields": [
                    "summary", "status", "description", "comment", "created", "updated",
                    "issuelinks", "parent", "labels",
                ],
            },
        )
        return list(data.get("issues") or [])

    def get_issue(self, key: str) -> dict[str, Any]:
        return self._request(
            "GET",
            f"/issue/{key}",
            params={"fields": "summary,status,description,comment,created,updated,issuelinks,parent,labels,assignee"},
        )

    def get_issue_with_history(self, key: str) -> dict[str, Any]:
        """Fetch issue including status-change changelog and comments."""
        return self._request(
            "GET",
            f"/issue/{key}",
            params={
                "fields": "summary,status,description,comment,created,updated,issuelinks,parent,labels,assignee,priority",
                "expand": "changelog",
            },
        )

    def create_linked_issue(
        self,
        *,
        summary: str,
        description: str,
        link_to: str,
        link_type: str = "Relates",
    ) -> str:
        fields: dict[str, Any] = {
            "project": {"key": self.cfg.jira_project_key},
            "issuetype": {"name": self.cfg.jira_issue_type},
            "summary": summary,
            "description": {
                "type": "doc",
                "version": 1,
                "content": [
                    {
                        "type": "paragraph",
                        "content": [{"type": "text", "text": description}],
                    }
                ],
            },
            "labels": ["migration-dashboard"],
        }
        created = self._request("POST", "/issue", json={"fields": fields})
        key = created["key"]
        self._request(
            "POST",
            "/issueLink",
            json={
                "type": {"name": link_type},
                "inwardIssue": {"key": link_to},
                "outwardIssue": {"key": key},
            },
        )
        return key

    def create_creation_ticket(
        self, *, account: str, channel_id: str, platform: str, distribution_id: str = ""
    ) -> str:
        summary = f"{account} | {channel_id} | {platform} | NM URL creation"
        desc = (
            f"Account: {account}\nChannel: {channel_id}\nPlatform: {platform}\n"
            f"Distribution: {distribution_id or 'TBD'}\n"
            f"Parent tracker: {self.cfg.jira_parent_creation}\n"
        )
        return self.create_linked_issue(
            summary=summary,
            description=desc,
            link_to=self.cfg.jira_parent_creation,
        )

    def create_switchover_ticket(
        self, *, account: str, channel_id: str, platform: str, distribution_id: str
    ) -> str:
        summary = f"{account} | {channel_id} | {platform} | {distribution_id} | switchover"
        desc = (
            f"Account: {account}\nChannel: {channel_id}\nPlatform: {platform}\n"
            f"Distribution: {distribution_id}\n"
            f"Parent tracker: {self.cfg.jira_parent_switchover}\n"
        )
        return self.create_linked_issue(
            summary=summary,
            description=desc,
            link_to=self.cfg.jira_parent_switchover,
        )

    @staticmethod
    def _comments_text(issue: dict[str, Any]) -> str:
        comments = (((issue.get("fields") or {}).get("comment") or {}).get("comments") or [])
        parts = []
        for c in comments:
            body = c.get("body")
            if isinstance(body, str):
                parts.append(body)
            elif isinstance(body, dict):
                parts.append(str(body))
        return "\n".join(parts)

    @staticmethod
    def _adf_to_text(node: Any) -> str:
        """Recursively extract plain text from Atlassian Document Format."""
        if isinstance(node, str):
            return node
        if isinstance(node, list):
            return " ".join(JiraClient._adf_to_text(n) for n in node).strip()
        if not isinstance(node, dict):
            return ""
        if node.get("type") == "text":
            return node.get("text", "")
        parts = []
        for v in node.values():
            if isinstance(v, (list, dict)):
                parts.append(JiraClient._adf_to_text(v))
        return " ".join(p for p in parts if p).strip()

    @staticmethod
    def parse_comments(issue: dict[str, Any]) -> list[dict[str, Any]]:
        """Return list of {author, created, body} dicts for an issue."""
        raw = (((issue.get("fields") or {}).get("comment") or {}).get("comments") or [])
        out = []
        for c in raw:
            body = c.get("body", "")
            if isinstance(body, dict):
                body = JiraClient._adf_to_text(body)
            out.append({
                "author": ((c.get("author") or {}).get("displayName") or ""),
                "created": (c.get("created") or "")[:16].replace("T", " "),
                "body": body,
            })
        return out

    @staticmethod
    def parse_changelog(issue: dict[str, Any]) -> list[dict[str, Any]]:
        """Return list of status-change events: {when, author, from_status, to_status}."""
        histories = ((issue.get("changelog") or {}).get("histories") or [])
        events = []
        for h in histories:
            when = (h.get("created") or "")[:16].replace("T", " ")
            author = ((h.get("author") or {}).get("displayName") or "")
            for item in (h.get("items") or []):
                if item.get("field") == "status":
                    events.append({
                        "when": when,
                        "author": author,
                        "from_status": item.get("fromString") or "",
                        "to_status": item.get("toString") or "",
                    })
        return events

    @staticmethod
    def parse_linked_issues(issue: dict[str, Any]) -> list[dict[str, Any]]:
        """Return list of {key, summary, status, relation} for linked issues."""
        links = ((issue.get("fields") or {}).get("issuelinks") or [])
        out = []
        for lnk in links:
            relation = (lnk.get("type") or {}).get("name", "")
            for direction in ("inwardIssue", "outwardIssue"):
                linked = lnk.get(direction)
                if linked:
                    out.append({
                        "key": linked.get("key", ""),
                        "summary": (linked.get("fields") or {}).get("summary", ""),
                        "status": (((linked.get("fields") or {}).get("status") or {}).get("name") or ""),
                        "relation": relation,
                    })
        return out

    @staticmethod
    def parse_distribution_id(text: str) -> str:
        m = DIST_RE.search(text or "")
        return m.group(1).lower() if m else ""

    def poll_updates(self) -> list[dict[str, Any]]:
        """Return delivery patch dicts inferred from Jira issue state/comments."""
        if not self.configured:
            return []
        jql = self.cfg.jira_poll_jql or (
            f'project = {self.cfg.jira_project_key} AND labels = migration-dashboard ORDER BY updated DESC'
        )
        issues = self.search(jql, max_results=200)
        patches: list[dict[str, Any]] = []
        for issue in issues:
            fields = issue.get("fields") or {}
            status_name = ((fields.get("status") or {}).get("name") or "").lower()
            key = issue.get("key") or ""
            summary = fields.get("summary") or ""
            text = summary + "\n" + self._comments_text(issue)
            if isinstance(fields.get("description"), dict):
                text += "\n" + str(fields["description"])
            dist = self.parse_distribution_id(text)
            patch: dict[str, Any] = {"jira_key": key, "distribution_id": dist}
            updated = (fields.get("updated") or "")[:10]
            if status_name in DONE_STATUSES:
                patch["completed_date"] = updated or date.today().isoformat()
                if "switchover" in summary.lower():
                    patch["status"] = "switchover completed"
                    patch["jira_switchover_key"] = key
                else:
                    patch["status"] = "ready for switchover"
                    patch["jira_creation_key"] = key
                    if not patch.get("initial_start_date"):
                        patch["initial_start_date"] = (fields.get("created") or "")[:10]
            if dist:
                patches.append(patch)
            elif key:
                patches.append(patch)
        return patches
