"""
Add AMG IDs that are not yet in the migration sheet.

Writes directly via Google Sheets API (no Apps Script redeploy needed).

First-time setup:
  python cli.py auth-google      # opens browser once, saves token

Then run:
  python add_missing_accounts.py
"""

import json
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

BASE      = Path(__file__).parent
SHEET_ID  = '1CkDZuX_Cs0gRhzZT_iKgMIQUyowUyo4u3l4uI5UFrWw'
SHEET_TAB = 'data'
STATUS    = 'not yet picked for migration'

SCOPES = ['https://www.googleapis.com/auth/spreadsheets']

# ------------------------------------------------------------------
# Accounts to track (amg_id, customer_name)
# Ones already in the sheet will be skipped automatically.
# ------------------------------------------------------------------
ALL_ACCOUNTS = [
    ('AMG17724', 'Puro Digital'),
    ('AMG17271', 'Swank MP'),
    ('AMG27045', '4Fangs / Versatile Communication'),
    ('AMG16120', 'Impact TV'),
    ('AMG02757', 'LooLooKids'),
    ('AMG01077', 'Gusto'),
    ('AMG13231', 'Actve'),
    ('AMG01416', 'Faststudios'),
    ('',         'NIAB'),
    ('AMG00567', 'Estrella Media'),
    ('AMG00069', 'highview'),
    ('AMG02724', 'Somos'),
    ('AMG30874', 'WaterBear / OurFrame'),
    ('AMG00170', 'Curiosity'),
    ('AMG36366', 'Curiosity EU'),
    ('AMG16368', 'WQED'),
    ('AMG17334', 'Lucha Libre'),
    ('AMG02051', 'ADME / Soul Publishing'),
    ('AMG00470', 'amazonfreevee'),
    ('AMG00426', 'LDS'),
    ('AMG00220', 'iHeart'),
    ('AMG01639', 'Youlook'),
    ('AMG01602', 'Thema Vive Kanal D'),
    ('AMG02451', 'C3'),
    ('AMG11279', 'Amuz'),
    ('AMG01579', 'Jewelry TV (JTV)'),
    ('AMG02411', 'VVS Films'),
]


# ------------------------------------------------------------------
# Credentials
# ------------------------------------------------------------------
def get_credentials():
    token_path  = BASE / 'secrets' / 'google-token.json'
    client_path = BASE / 'secrets' / 'google-oauth-client.json'
    sa_path     = BASE / 'secrets' / 'google-service-account.json'

    if token_path.exists():
        from google.oauth2.credentials import Credentials
        from google.auth.transport.requests import Request
        creds = Credentials.from_authorized_user_file(str(token_path), SCOPES)
        if creds.expired and creds.refresh_token:
            creds.refresh(Request())
            token_path.write_text(creds.to_json())
        return creds

    if client_path.exists():
        from google_auth_oauthlib.flow import InstalledAppFlow
        flow  = InstalledAppFlow.from_client_secrets_file(str(client_path), SCOPES)
        creds = flow.run_local_server(port=0)
        token_path.parent.mkdir(exist_ok=True)
        token_path.write_text(creds.to_json())
        print(f'  Token saved → {token_path}')
        return creds

    if sa_path.exists():
        from google.oauth2 import service_account
        return service_account.Credentials.from_service_account_file(str(sa_path), scopes=SCOPES)

    try:
        import google.auth
        creds, _ = google.auth.default(scopes=SCOPES)
        return creds
    except Exception:
        pass

    return None


# ------------------------------------------------------------------
# Sheet helpers (public gviz — no auth needed for reading)
# ------------------------------------------------------------------
def _gviz_fetch():
    url = (
        f'https://docs.google.com/spreadsheets/d/{SHEET_ID}'
        f'/gviz/tq?tqx=out:json&headers=1&sheet={urllib.parse.quote(SHEET_TAB)}'
    )
    with urllib.request.urlopen(url, timeout=15) as r:
        raw = r.read().decode()
    start = raw.index('(') + 1
    end   = raw.rindex(')')
    return json.loads(raw[start:end])


def fetch_headers():
    data   = _gviz_fetch()
    return [
        (c.get('label') or c.get('id') or '').strip().lower()
                                                              .replace(' ', '_')
                                                              .replace('-', '_')
        for c in data['table']['cols']
    ]


def fetch_existing_amg_ids():
    data   = _gviz_fetch()
    labels = fetch_headers()
    try:
        amg_col = labels.index('amg_id')
    except ValueError:
        print('WARNING: amg_id column not found in sheet headers:', labels)
        return set()

    ids = set()
    for row in data['table']['rows']:
        cells = row.get('c') or []
        cell  = cells[amg_col] if amg_col < len(cells) else None
        val   = (cell.get('v') or cell.get('f') or '') if cell else ''
        ids.add(str(val).strip().lower())
    return ids


# ------------------------------------------------------------------
# Append rows via Sheets API
# ------------------------------------------------------------------
def append_rows_api(service, rows_to_add, headers):
    """Build a row for each account matching the actual sheet column order."""
    values = []
    for amg_id, account_name in rows_to_add:
        row = ['' ] * len(headers)
        for i, h in enumerate(headers):
            if h == 'amg_id':       row[i] = amg_id
            if h == 'account_name': row[i] = account_name
            if h == 'status':       row[i] = STATUS
        values.append(row)

    body = {'values': values}
    result = (
        service.spreadsheets().values().append(
            spreadsheetId=SHEET_ID,
            range=f'{SHEET_TAB}!A1',
            valueInputOption='USER_ENTERED',
            insertDataOption='INSERT_ROWS',
            body=body,
        ).execute()
    )
    return result.get('updates', {}).get('updatedRows', len(values))


# ------------------------------------------------------------------
# Main
# ------------------------------------------------------------------
def main():
    print('Fetching current sheet…')
    try:
        existing = fetch_existing_amg_ids()
        headers  = fetch_headers()
    except Exception as e:
        print(f'ERROR reading sheet: {e}')
        sys.exit(1)

    print(f'Sheet columns : {headers}')
    print(f'Existing AMG IDs in sheet: {len(existing)}\n')

    # Deduplicate by normalised AMG ID
    seen            = set()
    already_there   = []
    to_add          = []
    for amg_id, name in ALL_ACCOUNTS:
        key = amg_id.strip().lower()
        if key in seen:
            continue
        seen.add(key)
        if key and key in existing:
            already_there.append((amg_id, name))
        else:
            to_add.append((amg_id, name))

    print(f'Already in sheet ({len(already_there)}):')
    for a, n in already_there:
        print(f'  ✓  {a:<12}  {n}')

    print(f'\nTo add ({len(to_add)}):')
    for a, n in to_add:
        print(f'  +  {a:<12}  {n}')

    if not to_add:
        print('\nNothing to add — all accounts already in sheet.')
        return

    print('\nAuthenticating with Google…')
    creds = get_credentials()
    if not creds:
        print(
            '\nERROR: No Google credentials found.\n'
            'Run this first:\n'
            '  python cli.py auth-google\n'
            'Then re-run this script.'
        )
        sys.exit(1)

    from googleapiclient.discovery import build
    service = build('sheets', 'v4', credentials=creds)

    print(f'\nAppending {len(to_add)} rows to "{SHEET_TAB}" tab…')
    try:
        added = append_rows_api(service, to_add, headers)
        print(f'\nDone — {added} rows added successfully.')
    except Exception as e:
        print(f'\nERROR writing to sheet: {e}')
        sys.exit(1)


if __name__ == '__main__':
    main()
