from __future__ import annotations

from typing import Any

OUR_TEAM_EMAILS = {'sanjana.srinivas@amagi.com', 'varun.ks@amagi.com', 'girish.tammanagoudar@amagi.com'}
_MODEL = 'qwen/qwen3.8-27b'


def analyze_jira_pending(
    *,
    ticket_key: str,
    summary: str,
    status: str,
    assignee_name: str,
    assignee_email: str,
    comments: list[dict[str, Any]],
    api_key: str,
) -> dict[str, str]:
    """
    Call Groq/Llama to determine who the ticket is pending on and why.
    Returns {'pending_on': 'our_team'|'onboarding', 'name': str, 'reason': str}.
    Falls back to assignee-email heuristic if Groq is unavailable.
    """
    if not api_key:
        return _heuristic_fallback(assignee_email, assignee_name, comments)

    try:
        from groq import Groq  # type: ignore
    except ImportError:
        return _heuristic_fallback(assignee_email, assignee_name, comments)

    comment_block = "\n".join(
        f"[{c['created']}] {c['author']}: {c['body'][:300]}"
        for c in comments[-10:]
    ) or "(no comments)"

    prompt = f"""You are analyzing a Jira ticket for an AN3 media delivery migration.

Ticket: {ticket_key}
Summary: {summary}
Status: {status}
Assignee: {assignee_name} <{assignee_email}>

Our migration team members (their emails):
- girish.tammanagoudar@amagi.com
- sanjana.srinivas@amagi.com
- varun.ks@amagi.com

The "onboarding" team is the customer-facing onboarding/ops team (anyone not in our team above).

Recent comments (oldest to newest):
{comment_block}

Based on the comments and current assignee, answer:
1. Who is this ticket CURRENTLY pending on? Reply with exactly one of: OUR_TEAM or ONBOARDING
2. Name of the person it's pending on (just first name or display name, or "Unknown")
3. One sentence reason (what needs to happen next, based on the latest comment)

Reply in this exact format:
PENDING: OUR_TEAM
NAME: <name>
REASON: <one sentence>"""

    try:
        client = Groq(api_key=api_key)
        resp = client.chat.completions.create(
            model=_MODEL,
            max_tokens=150,
            temperature=0,
            messages=[{"role": "user", "content": prompt}],
        )
        return _parse_response(resp.choices[0].message.content or "", assignee_email, assignee_name)
    except Exception:
        return _heuristic_fallback(assignee_email, assignee_name, comments)


def _parse_response(text: str, assignee_email: str, assignee_name: str) -> dict[str, str]:
    pending_on = 'our_team' if 'OUR_TEAM' in text.upper() else 'onboarding'
    name, reason = '', ''
    for line in text.splitlines():
        if line.upper().startswith('NAME:'):
            name = line.split(':', 1)[1].strip()
        elif line.upper().startswith('REASON:'):
            reason = line.split(':', 1)[1].strip()
    if not name:
        name = assignee_name
    return {'pending_on': pending_on, 'name': name, 'reason': reason}


def _heuristic_fallback(assignee_email: str, assignee_name: str, comments: list[dict[str, Any]]) -> dict[str, str]:
    email = (assignee_email or '').lower()
    pending_on = 'our_team' if email in OUR_TEAM_EMAILS else 'onboarding'
    reason = ''
    if comments:
        last = comments[-1]
        reason = f"Last comment by {last['author']}: {last['body'][:100]}"
    return {'pending_on': pending_on, 'name': assignee_name or 'Unknown', 'reason': reason}
