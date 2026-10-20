const $ = (s) => document.querySelector(s);
const STATUSES = ["not-started", "in-progress", "non-ssai pending", "ready for switchover", "switchover completed", "blocked"];
const DEL_STATUSES = ["Pending", "Deleted"];

let accountsCache = [];
let selected = null;
let detailRows = [];
let sheetUrl = "";
let delAccountsCache = [];
let delSelected = null;
let delDetailRows = [];

function toast(msg) {
  const el = $("#toast");
  el.textContent = msg;
  el.style.display = "block";
  setTimeout(() => { el.style.display = "none"; }, 4500);
}

async function api(path, opts = {}) {
  const res = await fetch(path, {
    headers: opts.body instanceof FormData ? undefined : { "Content-Type": "application/json" },
    ...opts,
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    const detail = data.detail;
    throw new Error(typeof detail === "string" ? detail : (detail?.[0]?.msg || res.statusText));
  }
  return data;
}

/** Long-running Claude Desktop automation (up to ~3 min). */
async function apiLong(path) {
  const res = await fetch(path, { method: "POST", headers: { "Content-Type": "application/json" } });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    const detail = data.detail;
    throw new Error(typeof detail === "string" ? detail : (detail?.[0]?.msg || res.statusText));
  }
  return data;
}

function pct(n, total) {
  if (!total) return 0;
  return Math.round((100 * n) / total);
}

function ringSvg(percent) {
  const r = 42;
  const c = 2 * Math.PI * r;
  const offset = c - (percent / 100) * c;
  return `<svg width="110" height="110" viewBox="0 0 110 110">
    <circle cx="55" cy="55" r="${r}" fill="none" stroke="#eef2f6" stroke-width="10"/>
    <circle cx="55" cy="55" r="${r}" fill="none" stroke="#1a7f37" stroke-width="10"
      stroke-linecap="round" stroke-dasharray="${c}" stroke-dashoffset="${offset}"
      transform="rotate(-90 55 55)"/>
    <text x="55" y="60" text-anchor="middle" font-size="18" font-weight="700" fill="#1a2332">${percent}%</text>
  </svg>`;
}

function closeModal() {
  $("#modal").classList.add("hidden");
  $("#modal").innerHTML = "";
}

function showModal(html, wide = false) {
  const m = $("#modal");
  m.classList.remove("hidden");
  m.innerHTML = `<div class="panel${wide ? " panel-lg" : ""}">${html}</div>`;
}

async function refreshAll() {
  const [h, stats, { accounts }] = await Promise.all([
    api("/api/health"),
    api("/api/stats"),
    api("/api/accounts"),
  ]);
  accountsCache = accounts;
  sheetUrl = h.spreadsheet_url || "";
  const by = stats.by_status || {};
  const done = by["switchover completed"] || 0;
  const ready = by["ready for switchover"] || 0;
  const pending = by["non-ssai pending"] || 0;
  const blocked = by["blocked"] || 0;
  const total = stats.total || 0;

  $("#banner").innerHTML = `
    ${total} deliveries · ${accounts.length} accounts ·
    unsynced: <strong>${h.pending_sync || 0}</strong> ·
    Sheet sync ·
    <a href="${sheetUrl}" target="_blank">Open Sheet</a>
  `;

  $("#kpis").innerHTML = `
    <div class="kpi"><div class="label">Deliveries</div><div class="value">${total}</div></div>
    <div class="kpi"><div class="label">Accounts</div><div class="value">${accounts.length}</div></div>
    <div class="kpi done"><div class="label">Completed</div><div class="value">${done}</div></div>
    <div class="kpi ready"><div class="label">Ready</div><div class="value">${ready}</div></div>
    <div class="kpi blocked"><div class="label">Blocked</div><div class="value">${blocked}</div></div>
    <div class="kpi"><div class="label">Pending NM</div><div class="value">${pending}</div></div>
  `;

  const rows = [
    ["Completed", done, "done"],
    ["Ready for switchover", ready, "ready"],
    ["Non-SSAI pending", pending, "pending"],
    ["Blocked", blocked, "blocked"],
  ];
  $("#status-chart").innerHTML = rows.map(([label, count, cls]) => `
    <div class="bar-row">
      <div class="bar-label">${label}</div>
      <div class="bar-track"><div class="bar-fill ${cls}" style="width:${pct(count, total)}%"></div></div>
      <div class="bar-count">${count}</div>
    </div>`).join("");

  $("#progress-block").innerHTML = `
    <div class="progress-ring-wrap">
      ${ringSvg(pct(done, total))}
      <div class="progress-copy">
        <h3>${pct(done, total)}% switchover completed</h3>
        <p>${done} of ${total} deliveries across ${accounts.length} accounts.</p>
      </div>
    </div>`;

  renderAccounts($("#account-search").value || "");
}

function renderAccounts(filter = "") {
  const q = filter.trim().toLowerCase();
  const list = accountsCache.filter((a) =>
    !q || a.account_name.toLowerCase().includes(q) || a.amg_id.toLowerCase().includes(q)
  );
  const grid = $("#accounts-grid");
  if (!list.length) {
    grid.innerHTML = `<p class="muted">No accounts yet. Click <strong>Refresh from Sheet</strong>.</p>`;
    return;
  }
  grid.innerHTML = list.map((a) => {
    const t = a.total || 1;
    const w = (n) => `${pct(n || 0, t)}%`;
    return `
    <div class="account ${a.fully_migrated ? "migrated" : ""}" data-name="${encodeURIComponent(a.account_name)}" data-amg="${a.amg_id}">
      ${a.fully_migrated ? `<span class="badge-done">Fully migrated</span>` : ""}
      <h3>${a.account_name}</h3>
      <div class="meta">${a.amg_id} · ${a.total} deliveries</div>
      <div class="mini-bars">
        <span style="width:${w(a.completed)};background:#1a7f37"></span>
        <span style="width:${w(a.ready)};background:#d4a72c"></span>
        <span style="width:${w(a.pending)};background:#6e7781"></span>
        <span style="width:${w(a.blocked)};background:#cf222e"></span>
      </div>
      <div class="pills">
        <span class="pill done">done ${a.completed || 0}</span>
        <span class="pill ready">ready ${a.ready || 0}</span>
        <span class="pill">pending ${a.pending || 0}</span>
        <span class="pill blocked">blocked ${a.blocked || 0}</span>
      </div>
    </div>`;
  }).join("");

  grid.querySelectorAll(".account").forEach((el) => {
    el.addEventListener("click", () => openAccount(decodeURIComponent(el.dataset.name), el.dataset.amg));
  });
}

async function openAccount(name, amg) {
  selected = { name, amg };
  $("#overview").classList.add("hidden");
  $("#detail").classList.remove("hidden");
  $("#detail-title").textContent = name;
  const { deliveries } = await api(`/api/deliveries?account_name=${encodeURIComponent(name)}`);
  detailRows = deliveries;
  const done = deliveries.filter((d) => d.status === "switchover completed").length;
  $("#detail-meta").textContent = `${deliveries.length} deliveries · ${done} completed · ${amg}`;
  renderDetailTable();
}

function renderDetailTable() {
  const status = $("#filter-status").value;
  const q = ($("#filter-q").value || "").toLowerCase();
  const rows = detailRows.filter((d) => {
    if (status && d.status !== status) return false;
    if (!q) return true;
    return [d.channel_id, d.platform, d.distribution_id, d.remarks, d.jira_creation_key, d.jira_switchover_key]
      .join(" ").toLowerCase().includes(q);
  });

  $("#detail-table").innerHTML = `
    <table>
      <thead>
        <tr>
          <th>Channel</th><th>Platform</th><th>Distribution</th><th>Type</th>
          <th>Status</th><th>Dates</th><th>Remarks</th><th>Jira</th>
        </tr>
      </thead>
      <tbody>
        ${rows.map((d) => {
          const jira = [d.jira_creation_key, d.jira_switchover_key].filter(Boolean)
            .map((k) => `<a href="https://amagiengg.atlassian.net/browse/${k}" target="_blank">${k}</a>`).join("<br>") || "—";
          const opts = STATUSES.map((s) => `<option value="${s}" ${d.status === s ? "selected" : ""}>${s}</option>`).join("");
          return `<tr class="${d.status === "switchover completed" ? "row-done" : ""} ${d.dirty ? "row-dirty" : ""}">
            <td>${d.channel_id}<div class="muted">${d.channel_name || ""}</div></td>
            <td>${d.platform || d.platform_id || "—"}</td>
            <td>${d.distribution_id || "—"}</td>
            <td>${d.stream_type || "hls"}</td>
            <td><select class="status-select" data-id="${d.id}">${opts}</select></td>
            <td>${d.initial_start_date || ""}<br>${d.completed_date || ""}</td>
            <td class="muted">${d.remarks || ""}</td>
            <td>${jira}</td>
          </tr>`;
        }).join("") || `<tr><td colspan="8" class="muted">No rows match.</td></tr>`}
      </tbody>
    </table>`;

  $("#detail-table").querySelectorAll(".status-select").forEach((sel) => {
    sel.addEventListener("change", async () => {
      await api(`/api/deliveries/${sel.dataset.id}`, {
        method: "PATCH",
        body: JSON.stringify({ status: sel.value }),
      });
      toast("Saved locally — click Push changes to Sheet when ready");
      openAccount(selected.name, selected.amg);
      refreshAll();
    });
  });
}

function showAddModal() {
  const accountOpts = accountsCache.map((a) =>
    `<option value="${a.account_name}" data-amg="${a.amg_id}" ${selected?.name === a.account_name ? "selected" : ""}>${a.account_name}</option>`
  ).join("");
  showModal(`
    <h3>Add ticket</h3>
    <p class="muted">Saved in the dashboard. Push to Sheet when you want the Google Sheet updated.</p>
    <form id="add-form" class="form-grid">
      <label>Account
        <select id="acc-pick"><option value="">new…</option>${accountOpts}</select>
      </label>
      <label>Account name<input name="account_name" value="${selected?.name || ""}" required></label>
      <label>AMG ID<input name="amg_id" value="${selected?.amg || ""}" required></label>
      <label>Jira NM<input name="jira_creation_key" placeholder="AN3OB-…"></label>
      <label>Jira SW<input name="jira_switchover_key" placeholder="AN3OB-…"></label>
      <label>Channel<input name="channel_id"></label>
      <label>Platform<input name="platform"></label>
      <label>Distribution<input name="distribution_id"></label>
      <label>Status
        <select name="status">${STATUSES.map((s) => `<option>${s}</option>`).join("")}</select>
      </label>
      <label class="full">Remarks<textarea name="remarks"></textarea></label>
      <div class="form-actions full">
        <button class="btn primary" type="submit">Save</button>
        <button class="btn" type="button" id="cancel-add">Cancel</button>
      </div>
    </form>`);
  $("#acc-pick").onchange = (e) => {
    const opt = e.target.selectedOptions[0];
    if (!opt?.value) return;
    const f = $("#add-form");
    f.account_name.value = opt.value;
    f.amg_id.value = opt.dataset.amg || "";
  };
  $("#cancel-add").onclick = closeModal;
  $("#add-form").onsubmit = async (e) => {
    e.preventDefault();
    const body = Object.fromEntries(new FormData(e.target).entries());
    body.stream_type = "hls";
    await api("/api/entries", { method: "POST", body: JSON.stringify(body) });
    closeModal();
    toast("Saved — Push to Sheet when ready");
    await refreshAll();
    if (selected) openAccount(selected.name, selected.amg);
  };
}

function showAddAccountModal(prefill = {}) {
  const statusOpts = STATUSES.map((s) =>
    `<option value="${s}"${s === "not-started" ? " selected" : ""}>${s}</option>`
  ).join("");

  showModal(`
    <h3>Add Account</h3>

    <div class="section-label">Account Info</div>
    <div class="form-grid">
      <label>Account Name<input id="aa-name" value="${prefill.account_name || ""}" required placeholder="e.g. Hasbro"></label>
      <label>AMG ID<input id="aa-amg" value="${prefill.amg_id || ""}" required placeholder="e.g. amg02713"></label>
    </div>

    <div class="section-label">Jira Tickets <span class="section-sub">(account-level · up to 3)</span></div>
    <div class="dynamic-list" id="jira-list"></div>
    <button type="button" class="btn btn-sm" id="add-jira-btn">+ Add Jira</button>

    <div class="section-label" style="margin-top:1rem">Distributions</div>
    <div class="dist-header">
      <span>Channel ID</span><span>Distribution ID</span><span>Status</span><span></span>
    </div>
    <div class="dynamic-list" id="dist-list"></div>
    <button type="button" class="btn btn-sm" id="add-dist-btn">+ Add Row</button>

    <div class="section-label" style="margin-top:1rem">Dates &amp; Notes <span class="section-sub">(optional)</span></div>
    <div class="form-grid">
      <label>Initial Start Date<input type="date" id="aa-start" value="${prefill.initial_start_date || ""}"></label>
      <label>ETA Date<input type="date" id="aa-eta" value="${prefill.eta_provided_date || ""}"></label>
      <label class="full">Remarks<textarea id="aa-remarks" rows="2">${prefill.remarks || ""}</textarea></label>
    </div>

    <div class="form-actions" style="margin-top:1rem">
      <button class="btn primary" id="aa-submit">Save to Sheet</button>
      <button class="btn" type="button" id="cancel-add-acct">Cancel</button>
    </div>`, true);

  function addJiraRow(val = "") {
    const list = document.getElementById("jira-list");
    if (list.children.length >= 3) return;
    const labels = ["Creation (NM)", "Switchover", "Extra"];
    const idx = list.children.length;
    const row = document.createElement("div");
    row.className = "jira-row";
    row.innerHTML = `
      <span class="jira-label">${labels[idx]}</span>
      <input type="text" class="jira-key" placeholder="e.g. AN3OB-1234" value="${val}">
      <button type="button" class="btn-remove" title="Remove">×</button>`;
    row.querySelector(".btn-remove").onclick = () => {
      row.remove();
      reindexJiraLabels();
      updateJiraBtn();
    };
    list.appendChild(row);
    updateJiraBtn();
  }

  function reindexJiraLabels() {
    const labels = ["Creation (NM)", "Switchover", "Extra"];
    document.querySelectorAll("#jira-list .jira-label").forEach((el, i) => {
      el.textContent = labels[i] || `Jira ${i + 1}`;
    });
  }

  function updateJiraBtn() {
    const btn = document.getElementById("add-jira-btn");
    if (btn) btn.disabled = document.getElementById("jira-list").children.length >= 3;
  }

  function addDistRow() {
    const list = document.getElementById("dist-list");
    const row = document.createElement("div");
    row.className = "dist-row";
    row.innerHTML = `
      <input type="text" class="dist-channel" placeholder="e.g. amg02713c10">
      <input type="text" class="dist-id" placeholder="e.g. amg02713d69">
      <select class="dist-status">${statusOpts}</select>
      <button type="button" class="btn-remove" title="Remove">×</button>`;
    row.querySelector(".btn-remove").onclick = () => {
      if (document.querySelectorAll("#dist-list .dist-row").length > 1) row.remove();
    };
    list.appendChild(row);
  }

  // Wire buttons
  document.getElementById("add-jira-btn").onclick = () => addJiraRow();
  document.getElementById("add-dist-btn").onclick = addDistRow;
  document.getElementById("cancel-add-acct").onclick = closeModal;

  // Seed initial rows
  addJiraRow();
  addDistRow();

  document.getElementById("aa-submit").onclick = async () => {
    const amg_id = document.getElementById("aa-amg").value.trim();
    const account_name = document.getElementById("aa-name").value.trim();
    if (!amg_id || !account_name) { toast("AMG ID and Account Name are required"); return; }

    const jira_keys = [...document.querySelectorAll("#jira-list .jira-key")]
      .map((i) => i.value.trim()).filter(Boolean);

    const distributions = [...document.querySelectorAll("#dist-list .dist-row")].map((row) => ({
      channel_id: row.querySelector(".dist-channel").value.trim(),
      distribution_id: row.querySelector(".dist-id").value.trim(),
      status: row.querySelector(".dist-status").value,
    }));

    if (!distributions.some((d) => d.channel_id || d.distribution_id)) {
      toast("Add at least one distribution row with a Channel or Distribution ID");
      return;
    }

    const btn = document.getElementById("aa-submit");
    btn.disabled = true;
    btn.textContent = "Saving…";

    try {
      const r = await api("/api/accounts/add", {
        method: "POST",
        body: JSON.stringify({
          amg_id, account_name, jira_keys, distributions,
          initial_start_date: document.getElementById("aa-start").value,
          eta_provided_date: document.getElementById("aa-eta").value,
          remarks: document.getElementById("aa-remarks").value.trim(),
        }),
      });
      closeModal();
      const msg = r.sheet_appended
        ? `Added ${r.count} row(s) for ${account_name} — appended to Sheet ✓`
        : `Added ${r.count} row(s) for ${account_name} — push to Sheet when ready`;
      toast(msg);
      await refreshAll();
      if (selected) openAccount(selected.name, selected.amg);
    } catch (err) {
      toast("Error: " + err.message);
      btn.disabled = false;
      btn.textContent = "Save to Sheet";
    }
  };
}

function switchTab(tab) {
  document.querySelectorAll(".tab").forEach(t => t.classList.toggle("active", t.dataset.tab === tab));
  const isMigration = tab === "migration";
  $("#overview").classList.toggle("hidden", !isMigration);
  $("#detail").classList.add("hidden");
  $("#deletion-overview").classList.toggle("hidden", isMigration);
  $("#deletion-detail").classList.add("hidden");
  if (!isMigration && !delAccountsCache.length) refreshDeletion().catch(e => toast(e.message));
}
document.querySelectorAll(".tab").forEach(t => t.addEventListener("click", () => switchTab(t.dataset.tab)));

async function refreshDeletion() {
  const { accounts } = await api("/api/deletion/accounts");
  delAccountsCache = accounts;
  const total = accounts.reduce((s, a) => s + (a.total || 0), 0);
  const pending = accounts.reduce((s, a) => s + (a.pending || 0), 0);
  const deleted = accounts.reduce((s, a) => s + (a.deleted_count || 0), 0);
  $("#del-kpis").innerHTML = `
    <div class="kpi"><div class="label">Accounts</div><div class="value">${accounts.length}</div></div>
    <div class="kpi"><div class="label">Total Channels</div><div class="value">${total}</div></div>
    <div class="kpi blocked"><div class="label">Pending</div><div class="value">${pending}</div></div>
    <div class="kpi done"><div class="label">Deleted</div><div class="value">${deleted}</div></div>
  `;
  renderDelAccounts($("#del-search").value || "");
}

function renderDelAccounts(filter = "") {
  const q = filter.trim().toLowerCase();
  const list = delAccountsCache.filter(a =>
    !q || (a.account_name || "").toLowerCase().includes(q) || (a.amg_id || "").toLowerCase().includes(q)
  );
  const grid = $("#del-accounts-grid");
  if (!list.length) {
    grid.innerHTML = `<p class="muted">No data. Click <strong>Refresh from Sheet</strong>.</p>`;
    return;
  }
  grid.innerHTML = list.map(a => {
    const t = a.total || 1;
    const w = (n) => `${Math.round(100 * (n || 0) / t)}%`;
    const allDone = a.total > 0 && a.deleted_count === a.total;
    return `
    <div class="account ${allDone ? "migrated" : ""}" data-amg="${a.amg_id || ""}" data-acct="${encodeURIComponent(a.account_name || "")}">
      ${allDone ? `<span class="badge-done">All Deleted</span>` : ""}
      <h3>${a.account_name || a.amg_id || "—"}</h3>
      <div class="meta">${a.amg_id || ""} · ${a.total} channels</div>
      <div class="mini-bars">
        <span style="width:${w(a.deleted_count)};background:#1a7f37"></span>
        <span style="width:${w(a.pending)};background:#cf222e"></span>
      </div>
      <div class="pills">
        <span class="pill done">deleted ${a.deleted_count || 0}</span>
        <span class="pill blocked">pending ${a.pending || 0}</span>
      </div>
    </div>`;
  }).join("");
  grid.querySelectorAll(".account").forEach(el => {
    el.addEventListener("click", () => openDelAccount(decodeURIComponent(el.dataset.acct), el.dataset.amg));
  });
}

async function openDelAccount(name, amg) {
  delSelected = { name, amg };
  $("#deletion-overview").classList.add("hidden");
  $("#deletion-detail").classList.remove("hidden");
  $("#del-detail-title").textContent = name || amg;
  const { channels } = await api(`/api/deletion/channels?amg_id=${encodeURIComponent(amg)}`);
  delDetailRows = channels;
  const deleted = channels.filter(c => (c.deletion_status || "").toLowerCase() === "deleted").length;
  $("#del-detail-meta").textContent = `${channels.length} channels · ${deleted} deleted · ${amg}`;
  renderDelDetailTable();
}

function renderDelDetailTable() {
  const status = $("#del-filter-status").value;
  const q = ($("#del-filter-q").value || "").toLowerCase();
  const rows = delDetailRows.filter(d => {
    if (status && d.deletion_status !== status) return false;
    if (!q) return true;
    return [d.channel_id, d.channel_name, d.jira].join(" ").toLowerCase().includes(q);
  });
  $("#del-detail-table").innerHTML = `
    <table>
      <thead>
        <tr><th>Channel ID</th><th>Channel Name</th><th>Status</th><th>Jira</th></tr>
      </thead>
      <tbody>
        ${rows.map(d => {
          const opts = DEL_STATUSES.map(s => `<option value="${s}" ${d.deletion_status === s ? "selected" : ""}>${s}</option>`).join("");
          const jiraRaw = (d.jira || "").trim();
          let jiraLink = "—";
          if (jiraRaw) {
            const isUrl = /^https?:\/\//.test(jiraRaw);
            const jiraUrl = isUrl ? jiraRaw : `https://amagiengg.atlassian.net/browse/${jiraRaw}`;
            const ticketMatch = jiraRaw.match(/([A-Z][A-Z0-9]+-\d+)/);
            const jiraLabel = ticketMatch ? ticketMatch[1] : (isUrl ? jiraRaw.split("/").pop() : jiraRaw);
            jiraLink = `<a href="${jiraUrl}" target="_blank">${jiraLabel}</a>`;
          }
          const isDel = (d.deletion_status || "").toLowerCase() === "deleted";
          return `<tr class="${isDel ? "row-done" : ""}">
            <td>${d.channel_id || "—"}</td>
            <td class="muted">${d.channel_name || "—"}</td>
            <td><select class="status-select del-status-select" data-id="${d.id}">${opts}</select></td>
            <td>${jiraLink}</td>
          </tr>`;
        }).join("") || `<tr><td colspan="4" class="muted">No rows match.</td></tr>`}
      </tbody>
    </table>`;
  $("#del-detail-table").querySelectorAll(".del-status-select").forEach(sel => {
    sel.addEventListener("change", async () => {
      await api(`/api/deletion/channels/${sel.dataset.id}`, {
        method: "PATCH",
        body: JSON.stringify({ deletion_status: sel.value }),
      });
      toast("Saved — click Push to Sheet when ready");
      openDelAccount(delSelected.name, delSelected.amg);
    });
  });
}

function showAddDelModal(prefill = {}) {
  const accountOpts = (delAccountsCache.length ? delAccountsCache : accountsCache).map(a =>
    `<option value="${a.amg_id}" data-name="${a.account_name || ""}" ${delSelected?.amg === a.amg_id ? "selected" : ""}>${a.account_name || a.amg_id}</option>`
  ).join("");
  showModal(`
    <h3>Add Channel Deletion</h3>
    <form id="del-add-form" class="form-grid">
      <label>Account
        <select id="del-acc-pick"><option value="">— select —</option>${accountOpts}</select>
      </label>
      <label>AMG ID<input id="del-amg" name="amg_id" value="${prefill.amg_id || delSelected?.amg || ""}" required></label>
      <label>Account Name<input id="del-acct-name" name="account_name" value="${prefill.account_name || delSelected?.name || ""}"></label>
      <label>Channel ID<input name="channel_id" value="${prefill.channel_id || ""}"></label>
      <label>Channel Name<input name="channel_name" value="${prefill.channel_name || ""}"></label>
      <label>Jira<input name="jira" placeholder="AN3OB-…" value="${prefill.jira || ""}"></label>
      <label>Deletion Status
        <select name="deletion_status">${DEL_STATUSES.map(s => `<option ${s === "Pending" ? "selected" : ""}>${s}</option>`).join("")}</select>
      </label>
      <div class="form-actions full">
        <button class="btn primary" type="submit">Save</button>
        <button class="btn" type="button" id="del-cancel">Cancel</button>
      </div>
    </form>`);
  document.getElementById("del-acc-pick").onchange = (e) => {
    const opt = e.target.selectedOptions[0];
    if (!opt?.value) return;
    document.getElementById("del-amg").value = opt.value;
    document.getElementById("del-acct-name").value = opt.dataset.name || "";
  };
  document.getElementById("del-cancel").onclick = closeModal;
  document.getElementById("del-add-form").onsubmit = async (e) => {
    e.preventDefault();
    const body = Object.fromEntries(new FormData(e.target).entries());
    await api("/api/deletion/channels", { method: "POST", body: JSON.stringify(body) });
    closeModal();
    toast("Saved — Push to Sheet when ready");
    await refreshDeletion();
    if (delSelected) openDelAccount(delSelected.name, delSelected.amg);
  };
}

$("#del-btn-refresh").onclick = async () => {
  const btn = $("#del-btn-refresh");
  btn.disabled = true; btn.textContent = "Refreshing…";
  try {
    const r = await api("/api/deletion/sheet/refresh", { method: "POST" });
    toast(`Loaded ${r.rows ?? "?"} channel deletion rows`);
    await refreshDeletion();
  } catch (e) { toast(e.message); }
  finally { btn.disabled = false; btn.textContent = "Refresh from Sheet"; }
};
$("#del-btn-push").onclick = async () => {
  const btn = $("#del-btn-push");
  btn.disabled = true; btn.textContent = "Pushing…";
  try {
    const r = await api("/api/deletion/sheet/push", { method: "POST" });
    toast(`Pushed ${r.pushed ?? "?"} rows to Sheet`);
  } catch (e) { toast(e.message); }
  finally { btn.disabled = false; btn.textContent = "Push to Sheet"; }
};
$("#del-btn-add").onclick = () => showAddDelModal();
$("#del-btn-back").onclick = () => {
  delSelected = null;
  $("#deletion-detail").classList.add("hidden");
  $("#deletion-overview").classList.remove("hidden");
  refreshDeletion().catch(e => toast(e.message));
};
$("#del-btn-add-here").onclick = () => showAddDelModal();
$("#del-search").oninput = (e) => renderDelAccounts(e.target.value);
$("#del-filter-status").onchange = renderDelDetailTable;
$("#del-filter-q").oninput = renderDelDetailTable;

$("#btn-refresh").onclick = async () => {
  const btn = $("#btn-refresh");
  btn.disabled = true;
  const prev = btn.textContent;
  btn.textContent = "Refreshing…";
  toast("Pulling Sheet automatically…");
  try {
    const r = await apiLong("/api/sheet/refresh");
    const n = r.imported?.rows ?? r.imported?.ok ?? 0;
    toast(`Loaded ${typeof n === "number" ? n : "?"} rows (${r.via || "auto"})`);
    await refreshAll();
    $("#overview").classList.remove("hidden");
    $("#detail").classList.add("hidden");
  } catch (e) {
    toast(e.message);
  } finally {
    btn.disabled = false;
    btn.textContent = prev;
  }
};

$("#btn-push").onclick = async () => {
  const btn = $("#btn-push");
  btn.disabled = true;
  const prev = btn.textContent;
  btn.textContent = "Pushing…";
  toast("Pushing dashboard changes to Sheet…");
  try {
    const r = await apiLong("/api/sheet/push");
    toast(`Pushed to Sheet (cleared ${r.cleared?.cleared ?? 0})`);
    await refreshAll();
  } catch (e) {
    toast(e.message);
  } finally {
    btn.disabled = false;
    btn.textContent = prev;
  }
};

$("#btn-back").onclick = () => {
  selected = null;
  $("#detail").classList.add("hidden");
  $("#overview").classList.remove("hidden");
  refreshAll();
};

$("#account-search").oninput = (e) => renderAccounts(e.target.value);
$("#filter-status").onchange = renderDetailTable;
$("#filter-q").oninput = renderDetailTable;
$("#btn-add").onclick = () => showAddAccountModal();
$("#btn-add-here").onclick = showAddModal;
$("#btn-enrich").onclick = async () => {
  if (!selected) return;
  try {
    const r = await api(`/api/accounts/${encodeURIComponent(selected.name)}/enrich`, { method: "POST" });
    toast(`Enriched ${r.enriched}`);
    openAccount(selected.name, selected.amg);
  } catch (e) { toast(e.message); }
};

refreshAll().catch((e) => toast(e.message));
