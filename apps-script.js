// Delivery Migration Dashboard — status update + workflow management endpoint
// Deploy as: Web App | Execute as: Me | Access: Anyone
//
// doGet receives query params including: key, action, and action-specific fields.

const SHEET_ID     = '1CkDZuX_Cs0gRhzZT_iKgMIQUyowUyo4u3l4uI5UFrWw';
const SHEET_NAME   = 'data';
const VALID_STATUS = ['switchover completed','ready for switchover','in-progress','blocked','not-started','non-ssai pending','no need of migration','not yet picked for migration'];
const EDIT_KEY     = 'an3-migrate-2026';

// Workflow sheet names
const WF_SHEET   = 'AN3_Workflow';
const HIST_SHEET = 'AN3_History';
const WF_HEADERS   = ['AMG ID','Account Name','Type','Stage','Assigned To','Stage Since','Jira Tickets','Notes','Pending On','Pending Reason'];
const HIST_HEADERS = ['Timestamp','AMG ID','Account Name','From Stage','To Stage','Assigned To','Actor','Jira Ticket','Notes'];

function doGet(e) {
  const cb = (e.parameter.callback || '').replace(/[^a-zA-Z0-9_]/g, '');
  try {
    const key    = (e.parameter.key    || '').trim();
    const action = (e.parameter.action || '').trim();

    if (!key) return json({ ok: false, error: 'Missing key' }, cb);
    if (key !== EDIT_KEY) return json({ ok: false, error: 'Invalid key' }, cb);

    // ── Workflow actions ────────────────────────────────────────────────────────
    if (action === 'setupWorkflow') {
      return json(setupWorkflowSheets(), cb);
    }
    if (action === 'addWorkflow') {
      return json(addWorkflowRow(e.parameter), cb);
    }
    if (action === 'updateWorkflow') {
      return json(updateWorkflowRow(e.parameter), cb);
    }
    if (action === 'appendHistory') {
      return json(appendHistoryRow(e.parameter), cb);
    }
    if (action === 'jiraSync') {
      return json(syncFromJira(), cb);
    }

    // ── Legacy delivery status actions ─────────────────────────────────────────
    const distribution_id = (e.parameter.distribution_id || '').trim();
    const new_status      = (e.parameter.new_status      || '').trim();
    const row_num         = parseInt(e.parameter.row_num  || '0', 10);

    if (distribution_id === '__probe__') return json({ ok: true, probe: true }, cb);

    if (action === 'appendAccount') {
      const amg_id       = (e.parameter.amg_id       || '').trim();
      const account_name = (e.parameter.account_name || '').trim();
      const status       = (e.parameter.status       || 'not yet picked for migration').trim();
      if (!account_name) return json({ ok: false, error: 'account_name required' }, cb);
      return json(appendAccountRow(amg_id, account_name, status), cb);
    }

    if (action === 'updateRowFull')  { return json(updateRowFull(e.parameter), cb); }
    if (action === 'deleteRow')      { return json(deleteRow(e.parameter), cb); }
    if (action === 'updateMergeRow') { return json(updateMergeRow(e.parameter), cb); }
    if (action === 'deleteMergeRow') { return json(deleteMergeRow(e.parameter), cb); }
    if (action === 'capabilities') {
      return json({
        ok: true,
        version: '2026-10-23-merge-edit',
        actions: ['setupWorkflow','addWorkflow','updateWorkflow','appendHistory','jiraSync',
                  'appendAccount','updateRowFull','deleteRow','updateMergeRow','deleteMergeRow','capabilities'],
      }, cb);
    }
    if (action) {
      return json({ ok: false, error: 'Unknown action: ' + action + ' — redeploy Apps Script from apps-script.js' }, cb);
    }

    if (!new_status) return json({ ok: false, error: 'Missing fields: new_status required for status update' }, cb);
    if (!VALID_STATUS.includes(new_status))
      return json({ ok: false, error: 'Invalid status: ' + new_status }, cb);

    if (distribution_id) return json(updateRow(distribution_id, new_status), cb);
    if (row_num > 1)     return json(updateRowByNum(row_num, new_status), cb);

    return json({ ok: false, error: 'Need distribution_id or row_num' }, cb);

  } catch (err) {
    return json({ ok: false, error: err.message }, cb);
  }
}

function doPost(e) {
  try {
    const body = JSON.parse(e.postData.contents);
    const { key, distribution_id, new_status } = body;
    if (!key || !distribution_id || !new_status) return json({ ok: false, error: 'Missing fields' });
    if (key !== EDIT_KEY) return json({ ok: false, error: 'Invalid key' });
    if (!VALID_STATUS.includes(new_status)) return json({ ok: false, error: 'Invalid status: ' + new_status });
    return json(updateRow(distribution_id, new_status));
  } catch (err) {
    return json({ ok: false, error: err.message });
  }
}

// ── Workflow sheet helpers ────────────────────────────────────────────────────

function getWorkflowSheet() {
  const ss = SpreadsheetApp.openById(SHEET_ID);
  return ss.getSheetByName(WF_SHEET);
}

function getHistorySheet() {
  const ss = SpreadsheetApp.openById(SHEET_ID);
  return ss.getSheetByName(HIST_SHEET);
}

function setupWorkflowSheets() {
  const ss = SpreadsheetApp.openById(SHEET_ID);
  var created = [];

  if (!ss.getSheetByName(WF_SHEET)) {
    const s = ss.insertSheet(WF_SHEET);
    s.appendRow(WF_HEADERS);
    s.getRange(1, 1, 1, WF_HEADERS.length).setFontWeight('bold').setBackground('#e8f0fe');
    s.setFrozenRows(1);
    created.push(WF_SHEET);
  }

  if (!ss.getSheetByName(HIST_SHEET)) {
    const s = ss.insertSheet(HIST_SHEET);
    s.appendRow(HIST_HEADERS);
    s.getRange(1, 1, 1, HIST_HEADERS.length).setFontWeight('bold').setBackground('#fce8e6');
    s.setFrozenRows(1);
    created.push(HIST_SHEET);
  }

  return { ok: true, created: created, message: created.length ? 'Created: ' + created.join(', ') : 'Sheets already exist' };
}

function addWorkflowRow(p) {
  const sheet = getWorkflowSheet();
  if (!sheet) return { ok: false, error: 'Run setupWorkflow first' };

  const amg_id = (p.amg_id || '').trim();
  if (!amg_id) return { ok: false, error: 'amg_id required' };

  // Check if already exists
  const data = sheet.getDataRange().getValues();
  for (var i = 1; i < data.length; i++) {
    if ((data[i][0] || '').toString().trim() === amg_id) {
      return { ok: false, error: 'Account ' + amg_id + ' already in workflow. Use updateWorkflow.' };
    }
  }

  const today = new Date().toISOString().slice(0, 10);
  const row = [
    amg_id,
    (p.account_name || '').trim(),
    (p.type         || 'migration').trim(),
    (p.stage        || 'identified').trim(),
    (p.assigned_to  || '').trim(),
    p.stage_since   || today,
    (p.jira_tickets  || '').trim(),
    (p.notes         || '').trim(),
    (p.pending_on    || '').trim(),
    (p.pending_reason|| '').trim(),
  ];
  sheet.appendRow(row);
  Logger.log('[%s] addWorkflow: %s - %s', today, amg_id, p.account_name);
  return { ok: true, amg_id: amg_id, account_name: p.account_name, stage: row[3] };
}

function ensureColumns(sheet, cols) {
  // Add header columns that don't exist yet. cols = [{key, label}]
  var lastCol = sheet.getLastColumn();
  var headers = sheet.getRange(1, 1, 1, lastCol).getValues()[0]
    .map(function(h) { return (h||'').toString().trim().toLowerCase().replace(/\s+/g,'_'); });
  cols.forEach(function(c) {
    if (headers.indexOf(c.key) === -1) {
      lastCol++;
      sheet.getRange(1, lastCol).setValue(c.label).setFontWeight('bold').setBackground('#e8f0fe');
      headers.push(c.key);
    }
  });
}

function updateWorkflowRow(p) {
  const sheet = getWorkflowSheet();
  if (!sheet) return { ok: false, error: 'Run setupWorkflow first' };

  const amg_id = (p.amg_id || '').trim();
  if (!amg_id) return { ok: false, error: 'amg_id required' };

  // Auto-add Pending On / Pending Reason columns if missing
  if (p.pending_on !== undefined || p.pending_reason !== undefined) {
    ensureColumns(sheet, [
      { key: 'pending_on',     label: 'Pending On' },
      { key: 'pending_reason', label: 'Pending Reason' },
    ]);
  }

  const data    = sheet.getDataRange().getValues();
  const headers = data[0].map(function(h) { return (h || '').toString().trim().toLowerCase().replace(/\s+/g, '_'); });

  const colIdx = {};
  headers.forEach(function(h, i) { colIdx[h] = i; });

  for (var i = 1; i < data.length; i++) {
    if ((data[i][colIdx['amg_id']] || '').toString().trim() === amg_id) {
      const rowNum = i + 1;
      const today = new Date().toISOString().slice(0, 10);

      if (p.stage)       sheet.getRange(rowNum, colIdx['stage'] + 1).setValue(p.stage.trim());
      if (p.assigned_to !== undefined) sheet.getRange(rowNum, colIdx['assigned_to'] + 1).setValue(p.assigned_to.trim());
      if (p.stage_since) sheet.getRange(rowNum, colIdx['stage_since'] + 1).setValue(p.stage_since);
      else if (p.stage)  sheet.getRange(rowNum, colIdx['stage_since'] + 1).setValue(today);
      if (p.jira_tickets !== undefined) sheet.getRange(rowNum, colIdx['jira_tickets'] + 1).setValue(p.jira_tickets.trim());
      if (p.notes)       sheet.getRange(rowNum, colIdx['notes'] + 1).setValue(p.notes.trim());
      if (p.pending_on !== undefined && colIdx['pending_on'] !== undefined)
        sheet.getRange(rowNum, colIdx['pending_on'] + 1).setValue(p.pending_on.trim());
      if (p.pending_reason !== undefined && colIdx['pending_reason'] !== undefined)
        sheet.getRange(rowNum, colIdx['pending_reason'] + 1).setValue(p.pending_reason.trim());

      Logger.log('[%s] updateWorkflow: %s → stage=%s', today, amg_id, p.stage || '(unchanged)');
      return { ok: true, amg_id: amg_id, updated: true };
    }
  }
  return { ok: false, error: 'amg_id not found: ' + amg_id };
}

function appendHistoryRow(p) {
  const sheet = getHistorySheet();
  if (!sheet) return { ok: false, error: 'Run setupWorkflow first' };

  const now = new Date().toISOString().replace('T', ' ').slice(0, 19);
  const row = [
    now,
    (p.amg_id       || '').trim(),
    (p.account_name || '').trim(),
    (p.from_stage   || '').trim(),
    (p.to_stage     || '').trim(),
    (p.assigned_to  || '').trim(),
    (p.actor        || '').trim(),
    (p.jira_ticket  || '').trim(),
    (p.notes        || '').trim(),
  ];
  sheet.appendRow(row);
  return { ok: true, logged: true };
}

// ── Main delivery sheet helpers ───────────────────────────────────────────────

function getSheet() {
  const ss = SpreadsheetApp.openById(SHEET_ID);
  return ss.getSheetByName(SHEET_NAME) || ss.getSheets()[0];
}

function updateRowByNum(row_num, new_status) {
  const sheet = getSheet();
  const data  = sheet.getDataRange().getValues();
  const headers   = data[0].map(function(h) { return (h || '').toString().trim().toLowerCase().replace(/\s+/g, '_'); });
  const statusCol = headers.indexOf('status');
  if (statusCol < 0) return { ok: false, error: 'status column not found' };
  if (row_num > sheet.getLastRow()) return { ok: false, error: 'row_num out of range' };
  sheet.getRange(row_num, statusCol + 1).setValue(new_status);
  Logger.log('[%s] row %s → %s', new Date().toISOString(), row_num, new_status);
  return { ok: true, row: row_num, new_status: new_status };
}

function updateRow(distribution_id, new_status) {
  const sheet = getSheet();
  const data  = sheet.getDataRange().getValues();

  if (data.length < 2) return { ok: false, error: 'Sheet is empty' };

  const headers   = data[0].map(function(h) { return (h || '').toString().trim().toLowerCase().replace(/\s+/g, '_'); });
  const distCol   = headers.indexOf('distribution_id');
  const statusCol = headers.indexOf('status');

  if (distCol < 0 || statusCol < 0) {
    return { ok: false, error: 'Columns not found. Headers: ' + headers.join(', ') };
  }

  for (var i = 1; i < data.length; i++) {
    if ((data[i][distCol] || '').toString().trim() === distribution_id) {
      sheet.getRange(i + 1, statusCol + 1).setValue(new_status);
      Logger.log('[%s] %s → %s (row %s)', new Date().toISOString(), distribution_id, new_status, i + 1);
      return { ok: true, distribution_id: distribution_id, new_status: new_status, row: i + 1 };
    }
  }

  return { ok: false, error: 'distribution_id not found: ' + distribution_id };
}

function appendAccountRow(amg_id, account_name, status) {
  const sheet   = getSheet();
  const headers = sheet.getRange(1, 1, 1, sheet.getLastColumn()).getValues()[0]
    .map(function(h) { return (h || '').toString().trim().toLowerCase().replace(/[\s\-]+/g, '_').replace(/[^a-z0-9_]/g, ''); });

  const newRow = new Array(headers.length).fill('');
  headers.forEach(function(h, i) {
    if (h === 'amg_id')       newRow[i] = amg_id;
    if (h === 'account_name') newRow[i] = account_name;
    if (h === 'status')       newRow[i] = status;
  });

  sheet.appendRow(newRow);
  Logger.log('[%s] appended account %s (%s) → %s', new Date().toISOString(), account_name, amg_id, status);
  return { ok: true, amg_id: amg_id, account_name: account_name, status: status };
}

function updateRowFull(p) {
  const row_num = parseInt(p.row_num || '0', 10);
  if (row_num < 2) return { ok: false, error: 'row_num required (>=2)' };
  const sheet = getSheet();
  if (row_num > sheet.getLastRow()) return { ok: false, error: 'row_num out of range' };
  const headers = sheet.getRange(1, 1, 1, sheet.getLastColumn()).getValues()[0]
    .map(function(h) { return (h || '').toString().trim().toLowerCase().replace(/[\s\-]+/g, '_').replace(/[^a-z0-9_]/g, ''); });
  var EDITABLE = ['amg_id','account_name','channel_id','distribution_id','status',
                  'remarks','prepare_date','eta_provided_date','switchover_date',
                  'hls_sheet','non_hls_sheet','discrepancies','jira_links'];
  var updated = [];
  EDITABLE.forEach(function(field) {
    if (p[field] === undefined) return;
    var colIdx = headers.indexOf(field);
    if (colIdx < 0) return;
    sheet.getRange(row_num, colIdx + 1).setValue(p[field]);
    updated.push(field);
  });
  Logger.log('[%s] updateRowFull: row %s updated: %s', new Date().toISOString(), row_num, updated.join(', '));
  return { ok: true, row: row_num, updated: updated };
}

function deleteRow(p) {
  const sheet = getSheet();
  var row_num = parseInt(p.row_num || '0', 10);
  const distribution_id = (p.distribution_id || '').trim();

  // Prefer explicit sheet row; fall back to lookup by distribution_id
  if (row_num < 2 && distribution_id) {
    const data = sheet.getDataRange().getValues();
    const headers = data[0].map(function(h) {
      return (h || '').toString().trim().toLowerCase().replace(/\s+/g, '_');
    });
    const distCol = headers.indexOf('distribution_id');
    if (distCol < 0) return { ok: false, error: 'distribution_id column not found' };
    for (var i = 1; i < data.length; i++) {
      if ((data[i][distCol] || '').toString().trim() === distribution_id) {
        row_num = i + 1;
        break;
      }
    }
  }

  if (row_num < 2) return { ok: false, error: 'row_num required (>=2) or distribution_id' };
  if (row_num > sheet.getLastRow()) return { ok: false, error: 'row_num out of range' };
  sheet.deleteRow(row_num);
  Logger.log('[%s] deleteRow: row %s deleted', new Date().toISOString(), row_num);
  return { ok: true, row: row_num, deleted: true };
}

// ── Channel Merge sheet helpers ───────────────────────────────────────────────

const MERGE_SHEET_GID = 334319022;

function getMergeSheet() {
  const ss = SpreadsheetApp.openById(SHEET_ID);
  return ss.getSheets().filter(function(s) { return s.getSheetId() === MERGE_SHEET_GID; })[0];
}

function updateMergeRow(p) {
  var row_num = parseInt(p.row_num || '0', 10);
  if (row_num < 2) return { ok: false, error: 'row_num required (>=2)' };
  var sheet = getMergeSheet();
  if (!sheet) return { ok: false, error: 'Channel Merge sheet not found (GID ' + MERGE_SHEET_GID + ')' };
  if (row_num > sheet.getLastRow()) return { ok: false, error: 'row_num out of range' };
  var headers = sheet.getRange(1, 1, 1, sheet.getLastColumn()).getValues()[0]
    .map(function(h) { return (h || '').toString().trim().toLowerCase().replace(/[\s\-]+/g, '_').replace(/[^a-z0-9_]/g, ''); });
  var EDITABLE = ['account_name','amgid','channels_to_be_merged','channels_merge_completed','jira','status','remarks'];
  var updated = [];
  EDITABLE.forEach(function(field) {
    if (p[field] === undefined) return;
    var colIdx = headers.indexOf(field);
    if (colIdx < 0) return;
    sheet.getRange(row_num, colIdx + 1).setValue(p[field]);
    updated.push(field);
  });
  Logger.log('[%s] updateMergeRow: row %s updated: %s', new Date().toISOString(), row_num, updated.join(', '));
  return { ok: true, row: row_num, updated: updated };
}

function deleteMergeRow(p) {
  var row_num = parseInt(p.row_num || '0', 10);
  if (row_num < 2) return { ok: false, error: 'row_num required (>=2)' };
  var sheet = getMergeSheet();
  if (!sheet) return { ok: false, error: 'Channel Merge sheet not found (GID ' + MERGE_SHEET_GID + ')' };
  if (row_num > sheet.getLastRow()) return { ok: false, error: 'row_num out of range' };
  sheet.deleteRow(row_num);
  Logger.log('[%s] deleteMergeRow: row %s deleted', new Date().toISOString(), row_num);
  return { ok: true, row: row_num, deleted: true };
}

function json(data, callback) {
  const s = JSON.stringify(data);
  if (callback) {
    return ContentService
      .createTextOutput(callback + '(' + s + ')')
      .setMimeType(ContentService.MimeType.JAVASCRIPT);
  }
  return ContentService
    .createTextOutput(s)
    .setMimeType(ContentService.MimeType.JSON);
}

// ── Jira Auto-Sync ───────────────────────────────────────────────────────────
// Reads Jira step tickets → updates AN3_Workflow sheet automatically.
// Credentials stored in Script Properties (Project Settings → Script Properties):
//   JIRA_EMAIL     e.g. girish.tammanagoudar@amagi.com
//   JIRA_API_TOKEN the Atlassian API token
// Run setupJiraSyncTrigger() ONCE from the editor to schedule every 30 minutes.

var JIRA_SITE    = 'https://amagiengg.atlassian.net';
var JIRA_PROJECT = 'AN3OB';
var ALL_STAGES_ORDER = ['identified','data_gathering','em_verification','hls_check','discrepancy','ready','approved','done'];

function _jiraHeaders() {
  var props = PropertiesService.getScriptProperties();
  var email = props.getProperty('JIRA_EMAIL') || '';
  var token = props.getProperty('JIRA_API_TOKEN') || '';
  if (!email || !token) throw new Error('Set JIRA_EMAIL and JIRA_API_TOKEN in Script Properties');
  return {
    'Authorization': 'Basic ' + Utilities.base64Encode(email + ':' + token),
    'Accept': 'application/json',
    'Content-Type': 'application/json',
  };
}

function _jiraSearch(jql, maxResults) {
  var url = JIRA_SITE + '/rest/api/3/search?maxResults=' + (maxResults || 500) +
            '&fields=summary,status,description,assignee&jql=' + encodeURIComponent(jql);
  var resp = UrlFetchApp.fetch(url, { headers: _jiraHeaders(), muteHttpExceptions: true });
  if (resp.getResponseCode() !== 200) {
    Logger.log('Jira error %s: %s', resp.getResponseCode(), resp.getContentText().slice(0, 200));
    return [];
  }
  return JSON.parse(resp.getContentText()).issues || [];
}

function _extractText(node) {
  var parts = [];
  function walk(n) {
    if (!n) return;
    if (typeof n === 'string') { parts.push(n); return; }
    if (n.type === 'text' && n.text) parts.push(n.text);
    if (n.content) n.content.forEach(walk);
  }
  walk(node);
  return parts.join(' ');
}

function _stepToStage(n) {
  if (n <= 1)  return 'identified';
  if (n <= 3)  return 'data_gathering';
  if (n <= 7)  return 'em_verification';
  if (n <= 12) return 'hls_check';
  if (n <= 14) return 'ready';
  return 'done';
}

function _stageIdx(s) {
  var i = ALL_STAGES_ORDER.indexOf(s);
  return i < 0 ? 0 : i;
}

function syncFromJira() {
  var STEP_RE = /Step\s+(\d+)\s*\|\s*(.+?)\s*\|\s*(.+)/i;
  var AMG_RE  = /\b(amg\d{4,6})\b/gi;
  var KEY_RE  = /\b(AN3OB-\d+)\b/gi;
  var DONE_ST = {'resolved':1,'done':1,'closed':1,'complete':1,'completed':1,'moved to production':1,'rejected':1};

  Logger.log('syncFromJira: fetching step tickets…');
  var issues = _jiraSearch(
    'project = ' + JIRA_PROJECT + ' AND issueType = "Migration-Support" AND summary ~ "Step" ORDER BY summary ASC',
    500
  );
  Logger.log('Found %s step tickets', issues.length);

  // Group by account slug
  var buckets = {};
  issues.forEach(function(iss) {
    var f = iss.fields;
    var m = STEP_RE.exec(f.summary || '');
    if (!m) return;
    var num = parseInt(m[1], 10);
    var slug = m[2].trim();
    var stepName = m[3].trim();
    var status = ((f.status || {}).name || '').toLowerCase();
    var keyNorm = slug.toLowerCase().replace(/[^a-z0-9]/g, '');
    if (!buckets[keyNorm]) buckets[keyNorm] = { slug: slug, steps: [], amgIds: {}, jiraKeys: [] };
    buckets[keyNorm].steps.push({ num: num, name: stepName, status: status, key: iss.key });
    buckets[keyNorm].jiraKeys.push(iss.key);
    var descText = _extractText((f.description || null));
    var amgMatch;
    AMG_RE.lastIndex = 0;
    while ((amgMatch = AMG_RE.exec(descText)) !== null) {
      buckets[keyNorm].amgIds[amgMatch[1].toLowerCase()] = 1;
    }
  });

  // Resolve each account's current stage
  var accounts = [];
  Object.keys(buckets).forEach(function(k) {
    var info = buckets[k];
    var steps = info.steps.sort(function(a,b){ return a.num - b.num; });
    var active = steps.filter(function(s){ return !DONE_ST[s.status]; });
    var done   = steps.filter(function(s){ return  DONE_ST[s.status]; });
    var cur, stage;
    if (active.length)     { cur = active[0];   stage = _stepToStage(cur.num); }
    else if (done.length)  { cur = done[done.length-1]; stage = 'done'; }
    else return;
    var amgList = Object.keys(info.amgIds);
    if (!amgList.length) return;
    accounts.push({
      amg_id:       amgList[0],
      account_name: info.slug.split(/[-_\s]+/).map(function(w){ return w.charAt(0).toUpperCase()+w.slice(1); }).join(' '),
      stage:        stage,
      cur_key:      cur.key,
      notes:        'Step ' + cur.num + ': ' + cur.name,
    });
  });
  Logger.log('Resolved %s accounts with AMG IDs', accounts.length);

  // Build delivery ticket map from main Deliveries sheet
  var deliveryMap = {};
  try {
    var ss = SpreadsheetApp.openById(SHEET_ID);
    var delSheet = ss.getSheetByName('Deliveries') || ss.getSheets()[0];
    var delData = delSheet.getDataRange().getValues();
    var delHeaders = delData[0].map(function(h){ return (h||'').toString().trim().toLowerCase().replace(/\s+/g,'_'); });
    var amgCol  = delHeaders.indexOf('amg_id');
    var jiraCol = delHeaders.indexOf('jira_links');
    for (var i = 1; i < delData.length; i++) {
      var amg = amgCol >= 0 ? (delData[i][amgCol]||'').toString().trim().toLowerCase() : '';
      if (!amg) continue;
      var jiraLinks = jiraCol >= 0 ? (delData[i][jiraCol]||'').toString() : '';
      if (!deliveryMap[amg]) deliveryMap[amg] = [];
      var km;
      KEY_RE.lastIndex = 0;
      while ((km = KEY_RE.exec(jiraLinks)) !== null) {
        if (deliveryMap[amg].indexOf(km[1]) < 0) deliveryMap[amg].push(km[1]);
      }
    }
  } catch(e) { Logger.log('delivery lookup error: ' + e.message); }

  // Batch-fetch actual Jira status of all delivery tickets
  var JIRA_DONE  = {'done':1,'resolved':1,'closed':1,'complete':1,'completed':1,'moved to production':1,'switched over':1};
  var JIRA_READY = {'ready for switchover':1,'ready':1,'approved':1};
  var ticketStatus = {};  // key → status name lower
  var allKeys = [];
  Object.keys(deliveryMap).forEach(function(amg) {
    deliveryMap[amg].forEach(function(k) { if (allKeys.indexOf(k) < 0) allKeys.push(k); });
  });
  if (allKeys.length) {
    try {
      var stIssues = _jiraSearch('key in (' + allKeys.join(',') + ')', allKeys.length + 10);
      stIssues.forEach(function(iss) {
        ticketStatus[iss.key] = ((iss.fields.status || {}).name || '').toLowerCase();
      });
      Logger.log('Fetched status for %s delivery tickets', Object.keys(ticketStatus).length);
    } catch(e) { Logger.log('ticket status fetch error: ' + e.message); }
  }

  // Override stage for accounts with delivery tickets based on actual Jira status
  accounts.forEach(function(acc) {
    var tickets = deliveryMap[acc.amg_id.toLowerCase()] || [];
    if (!tickets.length) return;
    acc.jira_tickets_override = tickets.slice(0,3).join(', ');
    var statuses = tickets.map(function(k){ return ticketStatus[k] || ''; }).filter(Boolean);
    if (statuses.length && statuses.every(function(s){ return JIRA_DONE[s]; })) {
      acc.stage = 'done';
    } else if (statuses.some(function(s){ return JIRA_READY[s]; })) {
      acc.stage = 'ready';
    }
  });

  // Upsert into AN3_Workflow
  var wfSheet = getWorkflowSheet();
  if (!wfSheet) { Logger.log('AN3_Workflow sheet not found — run setupWorkflow first'); return; }
  var wfData    = wfSheet.getDataRange().getValues();
  var wfHeaders = wfData[0].map(function(h){ return (h||'').toString().trim().toLowerCase().replace(/\s+/g,'_'); });
  var colIdx = {};
  wfHeaders.forEach(function(h, i){ colIdx[h] = i; });
  // Build existing row index: amg_id → row index in wfData
  var existing = {};
  for (var r = 1; r < wfData.length; r++) {
    var a = (wfData[r][colIdx['amg_id']]||'').toString().trim().toLowerCase();
    if (a) existing[a] = r;
  }

  var histSheet = getHistorySheet();
  var today = new Date().toISOString().slice(0,10);
  var now   = new Date().toISOString().replace('T',' ').slice(0,19);
  var added = 0, updated = 0, skipped = 0;

  accounts.forEach(function(acc) {
    var amg = acc.amg_id.toLowerCase();
    var jiraTickets = acc.jira_tickets_override || acc.cur_key || '';
    var rowIdx = existing[amg];

    if (rowIdx === undefined) {
      // New account — add
      wfSheet.appendRow([amg, acc.account_name, 'migration', acc.stage, '', today, jiraTickets, acc.notes]);
      if (histSheet) histSheet.appendRow([now, amg, acc.account_name, '', acc.stage, '', 'jira-sync', acc.cur_key, 'Added from Jira']);
      added++;
      Logger.log('Added: %s (%s) → %s', acc.account_name, amg, acc.stage);
    } else {
      var curStage      = (wfData[rowIdx][colIdx['stage']]||'').toString().trim();
      var curAssignedTo = (wfData[rowIdx][colIdx['assigned_to']]||'').toString().trim();
      var curStageSince = (wfData[rowIdx][colIdx['stage_since']]||'').toString().trim();
      var curTickets    = (wfData[rowIdx][colIdx['jira_tickets']]||'').toString().trim();
      var newIsLater    = _stageIdx(acc.stage) > _stageIdx(curStage);

      if (newIsLater) {
        // Calculate days spent in previous stage for accountability
        var daysInStage = 0;
        if (curStageSince) {
          try {
            var sinceDate = new Date(curStageSince);
            daysInStage = Math.floor((new Date() - sinceDate) / 86400000);
          } catch(e) {}
        }
        var note = daysInStage > 0
          ? 'Was in ' + curStage + ' for ' + daysInStage + ' day' + (daysInStage===1?'':'s') + (curAssignedTo ? ' (held by ' + curAssignedTo + ')' : '')
          : 'Moved by Jira sync';

        wfSheet.getRange(rowIdx+1, colIdx['stage']+1).setValue(acc.stage);
        wfSheet.getRange(rowIdx+1, colIdx['stage_since']+1).setValue(today);
        // Log history with who held it and for how long
        if (histSheet) histSheet.appendRow([now, amg, acc.account_name, curStage, acc.stage, curAssignedTo, 'jira-sync', acc.cur_key, note]);
        updated++;
        Logger.log('Updated: %s %s → %s (held by %s for %sd)', amg, curStage, acc.stage, curAssignedTo||'?', daysInStage);
      } else if (jiraTickets && jiraTickets !== curTickets) {
        updated++;
      } else {
        skipped++;
      }
      if (jiraTickets !== curTickets) {
        wfSheet.getRange(rowIdx+1, colIdx['jira_tickets']+1).setValue(jiraTickets);
      }
    }
  });

  // Also pull all unique accounts from Deliveries sheet (adds any not yet in pipeline)
  try {
    var delData2 = (SpreadsheetApp.openById(SHEET_ID).getSheetByName('Deliveries') || SpreadsheetApp.openById(SHEET_ID).getSheets()[0]).getDataRange().getValues();
    var dh = delData2[0].map(function(h){ return (h||'').toString().trim().toLowerCase().replace(/\s+/g,'_'); });
    var dAmgCol  = dh.indexOf('amg_id');
    var dNameCol = dh.indexOf('account_name');
    var dStCol   = dh.indexOf('status');
    // Aggregate statuses per account
    var delAccts = {};
    for (var di = 1; di < delData2.length; di++) {
      var dAmg  = dAmgCol  >= 0 ? (delData2[di][dAmgCol] ||'').toString().trim().toLowerCase() : '';
      var dName = dNameCol >= 0 ? (delData2[di][dNameCol]||'').toString().trim() : '';
      var dSt   = dStCol   >= 0 ? (delData2[di][dStCol]  ||'').toString().trim().toLowerCase() : '';
      if (!dAmg) continue;
      if (!delAccts[dAmg]) delAccts[dAmg] = { name: dName, statuses: {} };
      if (dSt) delAccts[dAmg].statuses[dSt] = (delAccts[dAmg].statuses[dSt]||0) + 1;
    }
    Object.keys(delAccts).forEach(function(dAmg) {
      if (existing[dAmg] !== undefined) return; // already in workflow
      var sts = delAccts[dAmg].statuses;
      var keys = Object.keys(sts);
      // Skip accounts with no migration need
      if (keys.length === 1 && keys[0] === 'no need of migration') return;
      if (keys.every(function(s){ return s === 'no need of migration'; })) return;
      // Determine stage
      var dStage;
      if (keys.every(function(s){ return s === 'switchover completed'; })) dStage = 'done';
      else if (keys.some(function(s){ return s === 'ready for switchover'; }))  dStage = 'ready';
      else if (keys.some(function(s){ return s === 'in-progress'; }))           dStage = 'hls_check';
      else if (keys.some(function(s){ return s === 'blocked'; }))               dStage = 'discrepancy';
      else dStage = 'identified';
      var dName = delAccts[dAmg].name || dAmg;
      var dTickets = (deliveryMap[dAmg] || []).slice(0,3).join(', ');
      wfSheet.appendRow([dAmg, dName, 'migration', dStage, '', today, dTickets, 'from Deliveries sheet']);
      if (histSheet) histSheet.appendRow([now, dAmg, dName, '', dStage, '', 'jira-sync', '', 'Added from Deliveries sheet']);
      added++;
      // Mark as existing so we don't add duplicates in same run
      existing[dAmg] = wfSheet.getLastRow() - 1;
      Logger.log('Added from Deliveries: %s (%s) → %s', dName, dAmg, dStage);
    });
  } catch(e2) { Logger.log('Deliveries sheet merge error: ' + e2.message); }

  Logger.log('syncFromJira done: +%s added, %s updated, %s skipped', added, updated, skipped);
  return { ok: true, added: added, updated: updated, skipped: skipped };
}

// Run this ONCE from the Apps Script editor (Run → setupJiraSyncTrigger)
// It creates a trigger to auto-sync every 30 minutes. No cron, no server needed.
function setupJiraSyncTrigger() {
  // Remove existing triggers for this function first
  ScriptApp.getProjectTriggers().forEach(function(t) {
    if (t.getHandlerFunction() === 'syncFromJira') ScriptApp.deleteTrigger(t);
  });
  ScriptApp.newTrigger('syncFromJira')
    .timeBased()
    .everyMinutes(30)
    .create();
  Logger.log('Trigger created: syncFromJira runs every 30 minutes');
  return 'Trigger set up — syncFromJira will run every 30 minutes';
}

// Test: Run this in the Apps Script editor
function testSetupWorkflow() {
  Logger.log(JSON.stringify(setupWorkflowSheets()));
}
function testAddWorkflow() {
  Logger.log(JSON.stringify(addWorkflowRow({
    amg_id: 'amg02042', account_name: 'OTT Studio', type: 'migration',
    stage: 'identified', assigned_to: 'varun', notes: 'test entry',
  })));
}
function testJiraSync() {
  Logger.log(JSON.stringify(syncFromJira()));
}
