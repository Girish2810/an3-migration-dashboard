"""Workflow state management — writes via Apps Script, reads via gviz."""
from __future__ import annotations

import json
import re
from datetime import date, datetime
from typing import Any

import requests

from app.config import Config

WORKFLOW_SHEET  = 'AN3_Workflow'
HISTORY_SHEET   = 'AN3_History'

MIGRATION_STAGES = [
    'identified', 'data_gathering', 'em_verification',
    'hls_check', 'discrepancy', 'ready', 'done',
]
DELETION_STAGES = [
    'identified', 'em_verification', 'approved', 'done',
]
ALL_STAGES = list(dict.fromkeys(MIGRATION_STAGES + DELETION_STAGES))

STAGE_LABELS = {
    'identified':     'Identified',
    'data_gathering': 'Data Gathering',
    'em_verification':'EM Verification',
    'hls_check':      'HLS Check',
    'discrepancy':    'Discrepancy',
    'ready':          'Ready',
    'approved':       'Approved',
    'done':           'Done',
}


class WorkflowClient:
    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg
        gs = cfg.raw.get('google_sheets', {})
        self.apps_script_url = gs.get('apps_script_url', '')
        self.edit_key        = gs.get('apps_script_key', '') or 'an3-migrate-2026'
        self.sheet_id        = cfg.spreadsheet_id

    # ── Low-level ────────────────────────────────────────────────────────────

    def _call(self, params: dict[str, Any], _retries: int = 2) -> dict[str, Any]:
        params['key'] = self.edit_key
        last_err: Exception | None = None
        for attempt in range(_retries):
            try:
                resp = requests.get(self.apps_script_url, params=params, timeout=60,
                                    allow_redirects=True)
                if resp.status_code == 404 and attempt < _retries - 1:
                    import time; time.sleep(2)
                    continue
                resp.raise_for_status()
                text = resp.text.strip()
                m = re.match(r'^[A-Za-z_$][A-Za-z0-9_$]*\((.*)\);?\s*$', text, re.DOTALL)
                text = m.group(1) if m else text
                return json.loads(text)
            except Exception as e:
                last_err = e
                if attempt < _retries - 1:
                    import time; time.sleep(3)
        raise last_err

    def _gviz(self, sheet_name: str) -> list[dict[str, Any]]:
        url = f'https://docs.google.com/spreadsheets/d/{self.sheet_id}/gviz/tq'
        resp = requests.get(url, params={'tqx': 'out:json', 'headers': '1', 'sheet': sheet_name}, timeout=30)
        resp.raise_for_status()
        m = re.search(r'google\.visualization\.Query\.setResponse\(([\s\S]*)\);?\s*$', resp.text)
        if not m:
            return []
        data = json.loads(m.group(1))
        if data.get('status') == 'error':
            return []
        table = data.get('table', {})
        cols = [c.get('label') or c.get('id') or '' for c in table.get('cols', [])]
        rows = []
        for row in table.get('rows', []):
            cells = row.get('c') or []
            record: dict[str, Any] = {}
            for i, col in enumerate(cols):
                cell = cells[i] if i < len(cells) else None
                val  = cell.get('v') if isinstance(cell, dict) else None
                record[col.lower().replace(' ', '_')] = val
            rows.append(record)
        return rows

    # ── Setup ────────────────────────────────────────────────────────────────

    def setup(self) -> dict[str, Any]:
        return self._call({'action': 'setupWorkflow'})

    # ── Write ────────────────────────────────────────────────────────────────

    def add_account(
        self,
        amg_id: str,
        account_name: str,
        *,
        account_type: str = 'migration',
        ems: list[str] | None = None,
        jira_tickets: str = '',
        notes: str = '',
        actor: str = '',
    ) -> dict[str, Any]:
        stage = 'identified'
        assigned = ', '.join(ems) if ems else ''
        result = self._call({
            'action':       'addWorkflow',
            'amg_id':       amg_id.strip(),
            'account_name': account_name.strip(),
            'type':         account_type,
            'stage':        stage,
            'assigned_to':  assigned,
            'stage_since':  date.today().isoformat(),
            'jira_tickets': jira_tickets,
            'notes':        notes,
        })
        if result.get('ok'):
            self._log_history(amg_id, account_name, '', stage, assigned, actor or 'system', notes=notes)
        return result

    def move(
        self,
        amg_id: str,
        stage: str,
        *,
        ems: list[str] | None = None,
        jira_ticket: str = '',
        notes: str = '',
        actor: str = '',
    ) -> dict[str, Any]:
        # Read current state to get account name + previous stage for history
        current = self._find(amg_id)
        account_name = current.get('account_name', amg_id) if current else amg_id
        from_stage   = current.get('stage', '') if current else ''
        assigned = ', '.join(ems) if ems is not None else (current.get('assigned_to', '') if current else '')

        params: dict[str, Any] = {
            'action':      'updateWorkflow',
            'amg_id':      amg_id.strip(),
            'stage':       stage.strip(),
            'stage_since': date.today().isoformat(),
            'assigned_to': assigned,
        }
        if notes:
            params['notes'] = notes
        if jira_ticket:
            existing = (current.get('jira_tickets') or '') if current else ''
            tickets = ', '.join(filter(None, [existing.strip(), jira_ticket.strip()]))
            params['jira_tickets'] = tickets

        result = self._call(params)
        if result.get('ok'):
            self._log_history(amg_id, account_name, from_stage, stage, assigned, actor or 'system', jira_ticket, notes)
        return result

    def assign(
        self,
        amg_id: str,
        ems: list[str],
        *,
        actor: str = '',
    ) -> dict[str, Any]:
        current = self._find(amg_id)
        account_name = current.get('account_name', amg_id) if current else amg_id
        stage        = current.get('stage', '') if current else ''
        assigned     = ', '.join(ems)
        result = self._call({
            'action':      'updateWorkflow',
            'amg_id':      amg_id.strip(),
            'assigned_to': assigned,
        })
        if result.get('ok'):
            self._log_history(amg_id, account_name, stage, stage, assigned, actor or 'system', notes='Reassigned')
        return result

    def bulk_sync(
        self,
        accounts: list[dict[str, Any]],
        actor: str = 'jira-sync',
        dry_run: bool = False,
        force_ticket_update: bool = False,
    ) -> dict[str, Any]:
        """Upsert a list of account dicts from Jira into the workflow sheet.

        Each dict should have: amg_id, account_name, stage, account_type,
        jira_tickets, notes.  Accounts already in the sheet at the same stage
        are skipped; stage advances are updated; new accounts are added.
        """
        existing = {(r.get('amg_id') or '').strip().lower(): r for r in self.get_all() if r.get('amg_id')}
        added, updated, skipped = [], [], []

        for acc in accounts:
            amg   = (acc.get('amg_id') or '').strip().lower()
            name  = (acc.get('account_name') or amg).strip()
            stage = (acc.get('stage') or 'identified').strip()
            atype = acc.get('account_type', 'migration')
            jira  = acc.get('jira_tickets', '')
            notes = acc.get('notes', '')

            if not amg:
                skipped.append({'name': name, 'reason': 'no amg_id'})
                continue

            cur = existing.get(amg)
            if cur is None:
                if not dry_run:
                    r = self.add_account(amg, name, account_type=atype,
                                         jira_tickets=jira, notes=notes, actor=actor)
                    if r.get('ok') and stage != 'identified':
                        self.move(amg, stage, jira_ticket=jira, notes='synced from Jira', actor=actor)
                added.append({'amg_id': amg, 'name': name, 'stage': stage})
            else:
                cur_stage = (cur.get('stage') or '').strip()
                stage_order = {s: i for i, s in enumerate(ALL_STAGES)}
                cur_idx = stage_order.get(cur_stage, 0)
                new_idx = stage_order.get(stage, 0)
                if new_idx > cur_idx:
                    if not dry_run:
                        self.move(amg, stage, jira_ticket=jira, notes='synced from Jira', actor=actor)
                    updated.append({'amg_id': amg, 'name': name, 'from': cur_stage, 'to': stage})
                elif force_ticket_update and jira and jira != (cur.get('jira_tickets') or '').strip():
                    if not dry_run:
                        self._call({'action': 'updateWorkflow', 'amg_id': amg, 'jira_tickets': jira})
                    updated.append({'amg_id': amg, 'name': name, 'from': cur_stage, 'to': cur_stage})
                else:
                    skipped.append({'amg_id': amg, 'name': name, 'reason': f'already at {cur_stage}'})

        return {'added': added, 'updated': updated, 'skipped': skipped}

    def write_pending(self, amg_id: str, pending_on: str, pending_reason: str) -> dict[str, Any]:
        return self._call({
            'action':         'updateWorkflow',
            'amg_id':         amg_id.strip(),
            'pending_on':     pending_on.strip(),
            'pending_reason': pending_reason.strip(),
        })

    def link_jira(self, amg_id: str, ticket: str) -> dict[str, Any]:
        current = self._find(amg_id)
        existing = (current.get('jira_tickets') or '') if current else ''
        tickets = ', '.join(filter(None, [existing.strip(), ticket.strip()]))
        return self._call({
            'action':       'updateWorkflow',
            'amg_id':       amg_id.strip(),
            'jira_tickets': tickets,
        })

    def _log_history(
        self,
        amg_id: str,
        account_name: str,
        from_stage: str,
        to_stage: str,
        assigned_to: str,
        actor: str,
        jira_ticket: str = '',
        notes: str = '',
    ) -> None:
        try:
            self._call({
                'action':       'appendHistory',
                'amg_id':       amg_id,
                'account_name': account_name,
                'from_stage':   from_stage,
                'to_stage':     to_stage,
                'assigned_to':  assigned_to,
                'actor':        actor,
                'jira_ticket':  jira_ticket,
                'notes':        notes,
            })
        except Exception:
            pass  # history logging is best-effort

    # ── Read ─────────────────────────────────────────────────────────────────

    def _find(self, amg_id: str) -> dict[str, Any] | None:
        rows = self._gviz(WORKFLOW_SHEET)
        amg_id = amg_id.strip().lower()
        for r in rows:
            if (r.get('amg_id') or '').strip().lower() == amg_id:
                return r
        return None

    def get_all(self) -> list[dict[str, Any]]:
        return self._gviz(WORKFLOW_SHEET)

    def get_history(self, amg_id: str | None = None) -> list[dict[str, Any]]:
        rows = self._gviz(HISTORY_SHEET)
        if amg_id:
            amg_id = amg_id.strip().lower()
            rows = [r for r in rows if (r.get('amg_id') or '').strip().lower() == amg_id]
        return rows

    def get_pending_on(self, person: str) -> list[dict[str, Any]]:
        person_lower = person.strip().lower()
        return [
            r for r in self.get_all()
            if person_lower in (r.get('assigned_to') or '').lower()
            and (r.get('stage') or '').strip() != 'done'
        ]

    def days_in_stage(self, row: dict[str, Any]) -> int:
        raw = row.get('stage_since') or ''
        if not raw:
            return 0
        # gviz sometimes returns a Date() string
        if isinstance(raw, str) and raw.startswith('Date('):
            parts = re.findall(r'\d+', raw)
            if len(parts) >= 3:
                yr, mo, dy = int(parts[0]), int(parts[1]) + 1, int(parts[2])
                d = date(yr, mo, dy)
            else:
                return 0
        else:
            try:
                d = datetime.fromisoformat(str(raw)[:10]).date()
            except ValueError:
                return 0
        return (date.today() - d).days
