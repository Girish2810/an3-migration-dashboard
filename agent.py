#!/usr/bin/env python3
"""AN3 Migration Agent — workflow management CLI.

Usage examples:
  python agent.py setup
  python agent.py add amg02042 "OTT Studio" --em varun --em suchitra
  python agent.py move amg02042 em_verification --note "Data gathered, sent to EM"
  python agent.py assign amg02042 --em varun
  python agent.py link amg02042 AN3OB-9394
  python agent.py status
  python agent.py pending varun
  python agent.py history amg02042
  python agent.py jira amg02042              # pending on our team / onboarding + recent comments
"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import date
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import re

from app.config import Config
from app.jira_client import JiraClient
from app.groq_agent import analyze_jira_pending
from app.workflow_client import WorkflowClient, ALL_STAGES, STAGE_LABELS, MIGRATION_STAGES, DELETION_STAGES

# ANSI colours (disabled on Windows)
_NO_COLOR = os.environ.get('NO_COLOR') or not sys.stdout.isatty()
def _c(code: str, text: str) -> str:
    return text if _NO_COLOR else f'\033[{code}m{text}\033[0m'

def bold(t: str) -> str: return _c('1', t)
def green(t: str) -> str: return _c('32', t)
def yellow(t: str) -> str: return _c('33', t)
def red(t: str) -> str: return _c('31', t)
def cyan(t: str) -> str: return _c('36', t)
def muted(t: str) -> str: return _c('2', t)

def stale_color(days: int, text: str) -> str:
    if days >= 7:  return red(text)
    if days >= 3:  return yellow(text)
    return green(text)

def stage_label(s: str) -> str:
    return STAGE_LABELS.get(s, s)


# ── Commands ──────────────────────────────────────────────────────────────────

def cmd_setup(wf: WorkflowClient, _args: argparse.Namespace) -> None:
    print('Setting up workflow sheets…')
    result = wf.setup()
    if result.get('ok'):
        print(green('✓ ' + result.get('message', 'Done')))
    else:
        print(red('✗ ' + result.get('error', 'Failed')))


def cmd_add(wf: WorkflowClient, args: argparse.Namespace) -> None:
    amg_id       = args.amg_id.strip()
    account_name = args.account_name.strip()
    ems          = [e.strip() for e in (args.em or []) if e.strip()]
    account_type = args.type or 'migration'
    actor        = args.actor or os.environ.get('USER', 'me')
    notes        = args.note or ''

    print(f'Adding {bold(account_name)} ({cyan(amg_id)}) to pipeline…')
    result = wf.add_account(amg_id, account_name, account_type=account_type,
                            ems=ems, notes=notes, actor=actor)
    if result.get('ok'):
        print(green(f'✓ Added to pipeline'))
        print(f'  Stage    : {bold("identified")}')
        print(f'  Type     : {account_type}')
        print(f'  Assigned : {", ".join(ems) if ems else muted("(unassigned)")}')
        if notes:
            print(f'  Notes    : {notes}')
        print()
        print(muted('Tip: create a Jira ticket and link it with:'))
        print(muted(f'  python agent.py link {amg_id} <TICKET-ID>'))
    else:
        print(red('✗ ' + result.get('error', 'Failed')))


def cmd_move(wf: WorkflowClient, args: argparse.Namespace) -> None:
    amg_id = args.amg_id.strip()
    stage  = args.stage.strip()
    ems    = [e.strip() for e in (args.em or []) if e.strip()] or None
    actor  = args.actor or os.environ.get('USER', 'me')
    notes  = args.note or ''
    jira   = args.jira or ''

    valid = ALL_STAGES
    if stage not in valid:
        print(red(f'✗ Unknown stage "{stage}". Valid: {", ".join(valid)}'))
        sys.exit(1)

    print(f'Moving {cyan(amg_id)} → {bold(stage_label(stage))}…')
    result = wf.move(amg_id, stage, ems=ems, jira_ticket=jira, notes=notes, actor=actor)
    if result.get('ok'):
        print(green(f'✓ Stage updated to {stage_label(stage)}'))
        if ems:
            print(f'  Now assigned to: {", ".join(ems)}')
        if notes:
            print(f'  Note: {notes}')
    else:
        print(red('✗ ' + result.get('error', 'Failed')))


def cmd_assign(wf: WorkflowClient, args: argparse.Namespace) -> None:
    amg_id = args.amg_id.strip()
    ems    = [e.strip() for e in (args.em or []) if e.strip()]
    actor  = args.actor or os.environ.get('USER', 'me')

    if not ems:
        print(red('✗ Provide at least one --em name'))
        sys.exit(1)

    print(f'Assigning {cyan(amg_id)} → {", ".join(ems)}…')
    result = wf.assign(amg_id, ems, actor=actor)
    if result.get('ok'):
        print(green(f'✓ Assigned to {", ".join(ems)}'))
    else:
        print(red('✗ ' + result.get('error', 'Failed')))


def cmd_link(wf: WorkflowClient, args: argparse.Namespace) -> None:
    amg_id = args.amg_id.strip()
    ticket = args.ticket.strip()
    print(f'Linking {cyan(amg_id)} ↔ {ticket}…')
    result = wf.link_jira(amg_id, ticket)
    if result.get('ok'):
        print(green(f'✓ Linked {ticket}'))
    else:
        print(red('✗ ' + result.get('error', 'Failed')))


def cmd_status(wf: WorkflowClient, args: argparse.Namespace) -> None:
    rows = wf.get_all()
    if not rows:
        print(muted('No accounts in pipeline yet. Use: python agent.py add'))
        return

    # Filter
    if args.stage:
        rows = [r for r in rows if (r.get('stage') or '').strip() == args.stage]
    if args.type:
        rows = [r for r in rows if (r.get('type') or '').strip().lower() == args.type.lower()]
    q = (args.q or '').lower()
    if q:
        rows = [r for r in rows if q in (r.get('account_name') or '').lower()
                or q in (r.get('amg_id') or '').lower()]

    if not rows:
        print(muted('No matching accounts.'))
        return

    # Group by stage
    stage_order = ALL_STAGES + [s for s in {r.get('stage','') for r in rows} if s not in ALL_STAGES]
    by_stage: dict[str, list] = {s: [] for s in stage_order}
    for r in rows:
        by_stage.setdefault(r.get('stage', 'unknown'), []).append(r)

    total    = len(rows)
    done_cnt = len([r for r in rows if r.get('stage') == 'done'])
    stale    = [r for r in rows if wf.days_in_stage(r) >= 5 and r.get('stage') != 'done']

    print()
    print(bold(f'AN3 Pipeline — {date.today().isoformat()}'))
    print(f'  {bold(str(total))} accounts  •  {green(str(done_cnt) + " done")}', end='')
    if stale:
        print(f'  •  {red(str(len(stale)) + " stale (≥5 days)")}', end='')
    print('\n')

    for stage in stage_order:
        stage_rows = by_stage.get(stage, [])
        if not stage_rows:
            continue
        label = stage_label(stage)
        print(f'  {bold(label)}  {muted(f"({len(stage_rows)})")}')
        for r in stage_rows:
            days = wf.days_in_stage(r)
            name = r.get('account_name') or r.get('amg_id') or '?'
            amg  = r.get('amg_id') or ''
            em   = r.get('assigned_to') or muted('unassigned')
            jira = r.get('jira_tickets') or ''
            day_str = stale_color(days, f'{days}d') if days else muted('today')
            jira_str = f'  [{cyan(jira)}]' if jira else ''
            print(f'    {name}  {muted(amg)}  →  {em}  {muted("•")} {day_str}{jira_str}')
        print()


def cmd_pending(wf: WorkflowClient, args: argparse.Namespace) -> None:
    person = args.person.strip()
    rows = wf.get_pending_on(person)
    if not rows:
        print(green(f'✓ Nothing pending on {person}'))
        return

    print(f'\n{bold(f"Pending on {person}")}  ({len(rows)} accounts)\n')
    for r in rows:
        days  = wf.days_in_stage(r)
        stage = stage_label(r.get('stage') or '')
        name  = r.get('account_name') or r.get('amg_id') or '?'
        amg   = r.get('amg_id') or ''
        jira  = r.get('jira_tickets') or ''
        day_str = stale_color(days, f'{days}d in {stage}')
        jira_str = f'  {cyan(jira)}' if jira else ''
        print(f'  {bold(name)}  {muted(amg)}{jira_str}')
        print(f'    {day_str}')
    print()


_STEP_RE  = re.compile(r'Step\s+(\d+)\s*\|\s*(.+?)\s*\|\s*(.+)', re.I)
_AMG_RE   = re.compile(r'\b(amg\d{4,6})\b', re.I)
_DONE_ST  = {'resolved','done','closed','complete','completed','moved to production','rejected'}

def _extract_text(node: Any) -> str:
    parts: list[str] = []
    def walk(n: Any) -> None:
        if isinstance(n, dict):
            if n.get('type') == 'text': parts.append(n.get('text', ''))
            for v in n.values():
                if isinstance(v, (list, dict)): walk(v)
        elif isinstance(n, list):
            for item in n: walk(item)
    walk(node)
    return ' '.join(parts)

def _step_to_stage(n: int) -> str:
    if n <= 1:  return 'identified'
    if n <= 3:  return 'data_gathering'
    if n <= 7:  return 'em_verification'
    if n <= 12: return 'hls_check'
    if n <= 14: return 'ready'
    return 'done'

def _clean_name(slug: str) -> str:
    return ' '.join(w.capitalize() for w in re.split(r'[-_\s]+', slug))

def _parse_jira_accounts(issues: list[dict]) -> list[dict]:
    buckets: dict[str, dict] = {}
    for iss in issues:
        f = iss['fields']
        summary = f.get('summary', '')
        m = _STEP_RE.match(summary)
        if not m:
            continue
        num   = int(m.group(1))
        slug  = m.group(2).strip()
        step_name = m.group(3).strip()
        status = (f.get('status', {}).get('name', '') or '').lower()
        key_norm = re.sub(r'[^a-z0-9]', '', slug.lower())

        if key_norm not in buckets:
            buckets[key_norm] = {
                'slug': slug,
                'steps': [],
                'amg_ids': set(),
                'jira_keys': [],
                'assignees': set(),
            }
        buckets[key_norm]['steps'].append((num, step_name, status, iss['key']))
        buckets[key_norm]['jira_keys'].append(iss['key'])

        desc_text = _extract_text(f.get('description'))
        for amg in _AMG_RE.findall(desc_text):
            buckets[key_norm]['amg_ids'].add(amg.lower())
        if f.get('assignee'):
            buckets[key_norm]['assignees'].add(f['assignee']['displayName'])

    result = []
    for info in buckets.values():
        steps = sorted(info['steps'])
        active = [(n, nm, st, k) for n, nm, st, k in steps if st not in _DONE_ST]
        done   = [(n, nm, st, k) for n, nm, st, k in steps if st in _DONE_ST]

        if active:
            cur_step, cur_name, _, cur_key = active[0]
            stage = _step_to_stage(cur_step)
        elif done:
            cur_step, cur_name, _, cur_key = done[-1]
            stage = 'done'
        else:
            continue

        amg_list = sorted(info['amg_ids'])
        amg_id   = amg_list[0] if amg_list else ''

        result.append({
            'amg_id':       amg_id,
            'account_name': _clean_name(info['slug']),
            'stage':        stage,
            'account_type': 'migration',
            'jira_tickets': cur_key,           # current active ticket only
            'notes':        f'Step {cur_step}: {cur_name}',
        })
    return result


def cmd_jira(wf: WorkflowClient, args: argparse.Namespace) -> None:
    cfg  = Config.load()
    jira = JiraClient(cfg)
    if not jira.configured:
        print(red('✗ Jira not configured. Check config.yaml jira section.'))
        sys.exit(1)

    amg_id = args.amg_id.strip()

    # Get most recent linked ticket (prefer current row, fall back to history)
    all_accounts = wf.get_all()
    current = next((r for r in all_accounts if (r.get('amg_id') or '').lower() == amg_id.lower()), None)
    history_rows = wf.get_history(amg_id)

    account_name = (current or {}).get('account_name') or (
        history_rows[-1].get('account_name') if history_rows else amg_id
    )

    # Latest ticket: prefer current workflow row, then most recent history entry
    raw_ticket = (current or {}).get('jira_tickets') or ''
    if not raw_ticket and history_rows:
        for row in reversed(history_rows):
            raw_ticket = row.get('jira_ticket') or ''
            if raw_ticket:
                break

    key = re.split(r'[,\s]+', raw_ticket.strip())[0].strip().upper() if raw_ticket.strip() else ''
    if not key:
        print(muted(f'No Jira ticket linked to {amg_id}'))
        print(muted(f'  Link one with: python agent.py link {amg_id} <TICKET-ID>'))
        return

    try:
        issue = jira.get_issue_with_history(key)
    except Exception as e:
        print(red(f'✗ Could not fetch {key}: {e}'))
        return

    fields         = issue.get('fields') or {}
    status         = ((fields.get('status') or {}).get('name') or '')
    assignee_name  = ((fields.get('assignee') or {}).get('displayName') or '')
    assignee_email = ((fields.get('assignee') or {}).get('emailAddress') or '').lower()
    comments       = JiraClient.parse_comments(issue)

    print(f'\n{bold(account_name)}  {muted(amg_id)}  •  {cyan(key)}  [{bold(status)}]')
    print(muted('  Analyzing comments…'), end='\r', flush=True)

    result = analyze_jira_pending(
        ticket_key=key,
        summary=(fields.get('summary') or ''),
        status=status,
        assignee_name=assignee_name,
        assignee_email=assignee_email,
        comments=comments,
        api_key=cfg.groq_api_key,
    )

    if result['pending_on'] == 'our_team':
        pending_label = green('Pending on our team') + f'  {muted("(" + result["name"] + ")")}'
    else:
        pending_label = yellow('Pending on onboarding') + f'  {muted("(" + result["name"] + ")")}'

    print(f'  {pending_label}          ')  # spaces clear the "Analyzing…" line
    if result.get('reason'):
        print(f'  {muted(result["reason"])}')
    print()

    # Recent comments
    if comments:
        shown = comments[-5:]
        print(f'  {bold("Recent comments")} ({len(comments)} total):')
        for cm in shown:
            body_lines = [ln for ln in (cm['body'] or '').splitlines() if ln.strip()]
            first_line = (body_lines[0] if body_lines else '')[:120]
            tail = muted(' …') if len(body_lines) > 1 else ''
            print(f'    {muted(cm["created"])}  {bold(cm["author"])}: {first_line}{tail}')
    else:
        print(f'  {muted("No comments on this ticket")}')
    print()


def cmd_jira_sync(wf: WorkflowClient, args: argparse.Namespace) -> None:
    cfg  = Config.load()
    jira = JiraClient(cfg)
    if not jira.configured:
        print(red('✗ Jira not configured. Check config.yaml jira section.'))
        sys.exit(1)

    print('Fetching Jira migration tickets…')
    issues = jira.search(
        'project = AN3OB AND issueType = "Migration-Support" AND summary ~ "Step" ORDER BY summary ASC',
        max_results=500,
    )
    print(f'  Found {len(issues)} step tickets')

    accounts = _parse_jira_accounts(issues)
    no_amg   = [a for a in accounts if not a['amg_id']]
    with_amg = [a for a in accounts if a['amg_id']]

    if no_amg:
        print(yellow(f'\n  {len(no_amg)} account(s) skipped — no AMG ID found in ticket descriptions:'))
        for a in no_amg:
            print(f'    {a["account_name"]}  →  {a["jira_tickets"].split(",")[0].strip() if a["jira_tickets"] else "?"}')

    # Build delivery ticket map from main sheet (jira_links column)
    print('\nLooking up delivery tickets from main sheet…')
    _KEY_RE = re.compile(r'\b(AN3OB-\d+)\b', re.I)
    delivery_map: dict[str, list[str]] = {}
    try:
        gs_cfg        = cfg.raw.get('google_sheets', {})
        sheet_name    = gs_cfg.get('worksheet_name', 'Deliveries')
        delivery_rows = wf._gviz(sheet_name)
        for row in delivery_rows:
            amg = (row.get('amg_id') or '').strip().lower()
            if not amg:
                continue
            for col in ('jira_links', 'jira_creation_key', 'jira_switchover_key'):
                for key in _KEY_RE.findall(row.get(col) or ''):
                    delivery_map.setdefault(amg, [])
                    if key.upper() not in delivery_map[amg]:
                        delivery_map[amg].append(key.upper())
    except Exception as e:
        print(yellow(f'  Could not fetch delivery tickets from sheet: {e}'))

    # Batch-fetch actual Jira status of all delivery tickets
    _JIRA_DONE  = {'done','resolved','closed','complete','completed','moved to production',
                   'switched over','won\'t do','cancelled','rejected'}
    _JIRA_READY = {'ready for switchover','ready','approved','cx to verify','awaiting switchover'}
    all_keys = list({k for keys in delivery_map.values() for k in keys})
    ticket_status: dict[str, str] = {}  # key → status name lower
    if all_keys:
        print(f'  Fetching Jira status for {len(all_keys)} delivery tickets…')
        try:
            jql = f'key in ({",".join(all_keys)})'
            status_issues = jira.search(jql, max_results=len(all_keys) + 10)
            for iss in status_issues:
                st = (iss['fields'].get('status') or {}).get('name', '')
                ticket_status[iss['key']] = st.lower()
        except Exception as e:
            print(yellow(f'  Could not fetch ticket statuses: {e}'))

    found = sum(1 for a in with_amg if delivery_map.get(a['amg_id'].strip().lower()))
    print(f'  Found delivery tickets for {found}/{len(with_amg)} accounts')

    for a in with_amg:
        amg_lower = a['amg_id'].strip().lower()
        tickets = delivery_map.get(amg_lower, [])
        if tickets:
            a['jira_tickets'] = ', '.join(tickets[:3])
            # Override stage from actual Jira ticket status
            statuses = [ticket_status.get(k, '') for k in tickets]
            if statuses and all(s in _JIRA_DONE for s in statuses if s):
                a['stage'] = 'done'
            elif any(s in _JIRA_READY for s in statuses):
                a['stage'] = 'ready'
        # else: keep step-ticket-derived stage and jira_tickets as fallback

    print(f'\n  {len(with_amg)} accounts with AMG IDs ready to sync:')
    for a in with_amg:
        tkt = muted(a['jira_tickets'] or 'no delivery ticket')
        print(f'    {a["account_name"]:35s}  {cyan(a["amg_id"]):14s}  {bold(stage_label(a["stage"]))}  {tkt}')

    # Also add accounts from Deliveries sheet not yet in the pipeline
    print('\nMerging accounts from Deliveries sheet…')
    try:
        gs_name = cfg.raw.get('google_sheets', {}).get('worksheet_name', 'Deliveries')
        del_rows = wf._gviz(gs_name)
        del_accts: dict[str, dict] = {}
        for row in del_rows:
            amg  = (row.get('amg_id') or '').strip().lower()
            name = (row.get('account_name') or '').strip()
            st   = (row.get('status') or '').strip().lower()
            if not amg: continue
            del_accts.setdefault(amg, {'name': name, 'statuses': []})
            if st: del_accts[amg]['statuses'].append(st)

        existing_amgs = {a['amg_id'].strip().lower() for a in with_amg}
        del_accounts = []
        for amg, info in del_accts.items():
            if amg in existing_amgs: continue
            sts = set(info['statuses'])
            if sts and sts <= {'no need of migration'}: continue
            if sts == {'switchover completed'}: stage = 'done'
            elif 'ready for switchover' in sts:  stage = 'ready'
            elif 'in-progress' in sts:            stage = 'hls_check'
            elif 'blocked' in sts:                stage = 'discrepancy'
            else:                                 stage = 'identified'
            tickets_for_acct = ', '.join(delivery_map.get(amg, [])[:3])
            del_accounts.append({'amg_id': amg, 'account_name': info['name'] or amg,
                                  'stage': stage, 'account_type': 'migration',
                                  'jira_tickets': tickets_for_acct, 'notes': 'from Deliveries sheet'})

        if del_accounts:
            print(f'  {len(del_accounts)} additional account(s) from Deliveries sheet:')
            for a in del_accounts:
                print(f'    {a["account_name"]:35s}  {cyan(a["amg_id"]):14s}  {bold(stage_label(a["stage"]))}')
            with_amg = with_amg + del_accounts
        else:
            print(muted('  All Deliveries accounts already in pipeline.'))
    except Exception as e:
        print(yellow(f'  Could not merge Deliveries accounts: {e}'))

    if args.dry_run:
        print(yellow(f'\n[dry-run] Would sync {len(with_amg)} accounts total. No changes written.'))
        return

    print('\nWriting to AN3_Workflow sheet…')
    result = wf.bulk_sync(with_amg, actor='jira-sync', force_ticket_update=True)

    if result['added']:
        print(green(f'\n✓ Added ({len(result["added"])}):'))
        for r in result['added']:
            print(f'  + {r["name"]}  {muted(r["amg_id"])}  →  {r["stage"]}')

    if result['updated']:
        print(green(f'\n✓ Updated ({len(result["updated"])}):'))
        for r in result['updated']:
            print(f'  ↑ {r["name"]}  {muted(r["amg_id"])}  {muted(r["from"])} → {bold(r["to"])}')

    if result['skipped']:
        print(muted(f'\n  Skipped ({len(result["skipped"])}): already up-to-date'))

    print(green('\n✓ Sync complete. Refresh the Pipeline tab in the dashboard.'))

    if getattr(args, 'skip_analysis', False):
        return

    # ── Groq pending-on analysis ──────────────────────────────────────────────
    if not cfg.groq_api_key:
        print(muted('\n  (Groq not configured — skipping pending-on analysis)'))
        return

    print('\nAnalyzing Jira pending status for each account…')
    all_rows = wf.get_all()
    to_analyze = [r for r in all_rows if (r.get('jira_tickets') or '').strip()]
    if not to_analyze:
        print(muted('  No accounts with Jira tickets to analyze.'))
        return

    for r in to_analyze:
        amg_id = (r.get('amg_id') or '').strip()
        name   = (r.get('account_name') or amg_id)
        raw_ticket = (r.get('jira_tickets') or '').strip()
        key = re.split(r'[,\s]+', raw_ticket)[0].strip().upper() if raw_ticket else ''
        if not key:
            continue
        try:
            issue          = jira.get_issue_with_history(key)
            fields         = issue.get('fields') or {}
            status         = ((fields.get('status') or {}).get('name') or '')
            assignee_name  = ((fields.get('assignee') or {}).get('displayName') or '')
            assignee_email = ((fields.get('assignee') or {}).get('emailAddress') or '').lower()
            comments       = JiraClient.parse_comments(issue)
            result = analyze_jira_pending(
                ticket_key=key,
                summary=(fields.get('summary') or ''),
                status=status,
                assignee_name=assignee_name,
                assignee_email=assignee_email,
                comments=comments,
                api_key=cfg.groq_api_key,
            )
            wf.write_pending(amg_id, result['pending_on'], result.get('reason') or '')
            label = green('our team') if result['pending_on'] == 'our_team' else yellow('onboarding')
            person = result.get('name') or assignee_name
            print(f'  {cyan(amg_id):14s}  {name:35s}  →  Pending on {label} ({person})')
        except Exception as e:
            print(yellow(f'  {amg_id}: could not analyze — {e}'))

    print(green('\n✓ Pending-on analysis written to sheet. Refresh the Jira Status tab.'))


def cmd_history(wf: WorkflowClient, args: argparse.Namespace) -> None:
    rows = wf.get_history(args.amg_id)
    if not rows:
        print(muted(f'No history found for {args.amg_id}'))
        return

    account_name = rows[-1].get('account_name') or args.amg_id
    print(f'\n{bold(f"History: {account_name}")}  {muted(f"({args.amg_id})")}\n')
    for r in rows:
        ts         = str(r.get('timestamp') or '')[:16]
        from_stage = r.get('from_stage') or ''
        to_stage   = r.get('to_stage') or ''
        actor      = r.get('actor') or ''
        assigned   = r.get('assigned_to') or ''
        jira       = r.get('jira_ticket') or ''
        notes      = r.get('notes') or ''

        arrow = f'{muted(stage_label(from_stage))} → {bold(stage_label(to_stage))}' if from_stage else bold(stage_label(to_stage))
        print(f'  {muted(ts)}  {arrow}')
        if assigned: print(f'    Assigned : {assigned}')
        if actor:    print(f'    By       : {actor}')
        if jira:     print(f'    Jira     : {cyan(jira)}')
        if notes:    print(f'    Note     : {notes}')
    print()


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    p = argparse.ArgumentParser(
        description='AN3 Migration Agent — track accounts through the migration pipeline',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    sub = p.add_subparsers(dest='cmd', required=True)

    # setup
    sub.add_parser('setup', help='Create AN3_Workflow + AN3_History sheets')

    # add
    add_p = sub.add_parser('add', help='Add account to pipeline')
    add_p.add_argument('amg_id',       help='e.g. amg02042')
    add_p.add_argument('account_name', help='e.g. "OTT Studio"')
    add_p.add_argument('--em',  action='append', metavar='NAME', help='EM name (repeatable)')
    add_p.add_argument('--type', choices=['migration','deletion'], default='migration')
    add_p.add_argument('--note', help='Optional note')
    add_p.add_argument('--actor', help='Your name (default: $USER)')

    # move
    mv_p = sub.add_parser('move', help='Move account to a new stage')
    mv_p.add_argument('amg_id')
    mv_p.add_argument('stage', choices=ALL_STAGES)
    mv_p.add_argument('--em',   action='append', metavar='NAME', help='Re-assign to EM (optional)')
    mv_p.add_argument('--note', help='Note for history')
    mv_p.add_argument('--jira', help='Jira ticket to link (e.g. AN3OB-9394)')
    mv_p.add_argument('--actor', help='Your name (default: $USER)')

    # assign
    as_p = sub.add_parser('assign', help='Reassign account to EMs')
    as_p.add_argument('amg_id')
    as_p.add_argument('--em', action='append', metavar='NAME', required=True)
    as_p.add_argument('--actor', help='Your name (default: $USER)')

    # link
    lk_p = sub.add_parser('link', help='Link a Jira ticket to an account')
    lk_p.add_argument('amg_id')
    lk_p.add_argument('ticket', help='e.g. AN3OB-9394')

    # status
    st_p = sub.add_parser('status', help='Show full pipeline')
    st_p.add_argument('--stage',  choices=ALL_STAGES, help='Filter by stage')
    st_p.add_argument('--type',   choices=['migration','deletion'])
    st_p.add_argument('--q',      help='Search term')

    # pending
    pn_p = sub.add_parser('pending', help='Show accounts pending on a person')
    pn_p.add_argument('person', help='EM name')

    # history
    hi_p = sub.add_parser('history', help='Show history for an account')
    hi_p.add_argument('amg_id')

    # jira (per-account detail)
    jd_p = sub.add_parser('jira', help='Show latest Jira ticket status for an account')
    jd_p.add_argument('amg_id', help='e.g. amg02042')

    # jira-sync
    js_p = sub.add_parser('jira-sync', help='Read Jira step tickets and populate the pipeline')
    js_p.add_argument('--dry-run', action='store_true', help='Show what would change without writing')
    js_p.add_argument('--skip-analysis', action='store_true', help='Skip Groq pending-on analysis')

    args = p.parse_args()
    cfg  = Config.load()
    wf   = WorkflowClient(cfg)

    dispatch = {
        'setup':     cmd_setup,
        'add':       cmd_add,
        'move':      cmd_move,
        'assign':    cmd_assign,
        'link':      cmd_link,
        'status':    cmd_status,
        'pending':   cmd_pending,
        'history':   cmd_history,
        'jira':      cmd_jira,
        'jira-sync': cmd_jira_sync,
    }
    dispatch[args.cmd](wf, args)


if __name__ == '__main__':
    main()
