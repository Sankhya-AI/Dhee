const state = {
  dashboard: null,
  activeView: "overview",
};

const $ = (selector) => document.querySelector(selector);

function text(value, fallback = "0") {
  if (value === undefined || value === null || value === "") return fallback;
  return String(value);
}

function number(value) {
  const parsed = Number(value || 0);
  return Number.isFinite(parsed) ? parsed.toLocaleString() : "0";
}

function emptyNode() {
  return document.querySelector("#empty-template").content.cloneNode(true);
}

function setMetric(id, value) {
  const node = document.getElementById(id);
  if (node) node.textContent = text(value);
}

async function requestJson(url, options = {}) {
  const res = await fetch(url, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

async function loadDashboard() {
  const dashboard = await requestJson("/api/dashboard");
  state.dashboard = dashboard;
  renderDashboard(dashboard);
}

async function reloadDemo() {
  await requestJson("/api/demo", { method: "POST" });
  await loadDashboard();
}

function copySnapshot() {
  const raw = JSON.stringify(state.dashboard || {}, null, 2);
  if (navigator.clipboard) {
    navigator.clipboard.writeText(raw).catch(() => {});
  }
}

function renderDashboard(dashboard) {
  const totals = dashboard.totals || {};
  const workspace = dashboard.workspace || {};
  const firewall = dashboard.context_firewall || {};
  const aggregate = firewall.aggregate || {};
  $("#workspace-title").textContent = workspace.name || "Developer Brain";
  $("#branch-pill").textContent = workspace.branch || "local";
  $("#workspace-branch-pill").textContent = workspace.branch || "branch";
  setMetric("metric-firewall", `${text(totals.router_saved_pct, "0")}%`);
  setMetric("metric-raw", number(totals.raw_tokens));
  setMetric("metric-digest", number(totals.digest_tokens));
  setMetric("metric-state", totals.state_level || "unknown");
  setMetric("metric-runtime", totals.runtime ? "On" : "Off");
  setMetric("metric-repo-context", number(totals.repo_context));
  setMetric("metric-integrations", number(totals.integrations));
  setMetric("metric-portable", totals.portable ? "Yes" : "No");
  renderIntegrations(dashboard.integrations || []);
  renderWhyList(aggregate);
  renderWorkspace(workspace, dashboard.runtime || {});
  renderContextFirewall(firewall);
  renderState(dashboard.context_state || {}, dashboard.handoff || {});
  renderRepoContext(dashboard.repo_context || {});
  renderPortability(dashboard.portability || {});
}

function renderIntegrations(items) {
  const root = $("#integration-list");
  root.replaceChildren();
  if (!items.length) {
    root.appendChild(emptyNode());
    return;
  }
  items.forEach((item) => {
    const node = document.createElement("section");
    node.className = "org-node";
    node.innerHTML = `
      <h3>${text(item.name)}</h3>
      <div class="meta">${text(item.status)} / ${text(item.level)}</div>
      <p class="small-copy">${text(item.detail, "")}</p>
    `;
    root.appendChild(node);
  });
}

function renderWhyList(aggregate) {
  const root = $("#why-list");
  root.replaceChildren();
  const items = [
    {
      title: "Tool output stays bounded",
      body: `Demo routing saves ${text(aggregate.saved_pct, "0")}% while preserving raw evidence behind pointers.`,
      pill: "token control",
    },
    {
      title: "Current truth beats transcript replay",
      body: "Dhee keeps task state, decisions, files, tests, and evidence separate from noisy chat history.",
      pill: "state",
    },
    {
      title: "Local data remains inspectable",
      body: "The UI is a view over local CLI/MCP primitives, not a hosted memory silo.",
      pill: "local-first",
    },
  ];
  items.forEach((item) => root.appendChild(summaryCard(item)));
}

function renderWorkspace(workspace, runtime) {
  const root = $("#workspace-card");
  root.replaceChildren();
  const daemon = runtime.daemon || {};
  [
    { title: "Root", body: text(workspace.root_path, "unknown"), pill: text(workspace.branch, "branch") },
    { title: "Remote", body: text(workspace.remote, "no origin remote"), pill: "git" },
    { title: "Runtime", body: daemon.running ? text(daemon.endpoint, "daemon running") : "daemon stopped; CLI/MCP fall back in-process", pill: daemon.running ? "running" : "stopped" },
  ].forEach((item) => root.appendChild(summaryCard(item)));
}

function renderContextFirewall(report) {
  const root = $("#firewall-list");
  root.replaceChildren();
  const aggregate = report.aggregate || {};
  $("#firewall-pill").textContent = `${text(aggregate.saved_pct, "0")}% saved`;
  const cases = report.cases || [];
  if (!cases.length) {
    root.appendChild(emptyNode());
    return;
  }
  cases.forEach((item) => {
    const card = document.createElement("article");
    card.className = "firewall-card";

    const heading = document.createElement("div");
    heading.className = "panel-heading";
    heading.innerHTML = `
      <h3>${text(item.name)}</h3>
      <span class="pill">${text(item.surface)}</span>
    `;
    card.appendChild(heading);

    const decision = document.createElement("div");
    decision.className = "firewall-decision";
    decision.textContent = text(item.decision, "");
    card.appendChild(decision);

    const stats = document.createElement("div");
    stats.className = "firewall-stats";
    stats.innerHTML = `
      <span><strong>${number(item.raw_tokens)}</strong> raw</span>
      <span><strong>${number(item.digest_tokens)}</strong> digest</span>
      <span><strong>${text(item.saved_pct)}%</strong> saved</span>
      <span>${text(item.expand)}</span>
    `;
    card.appendChild(stats);

    const pre = document.createElement("pre");
    pre.className = "digest-preview";
    pre.textContent = text(item.digest, "");
    card.appendChild(pre);

    root.appendChild(card);
  });
}

function renderState(contextState, handoff) {
  const root = $("#state-list");
  root.replaceChildren();
  const status = contextState.status || {};
  $("#state-pill").textContent = text(status.level, "unknown");
  const items = [
    {
      title: "Compiled State",
      body: `epoch ${text(status.task_epoch, "1")} / revision ${text(status.state_revision, "0")} / ${text(status.state_card_tokens, "0")} card tokens`,
      pill: text(status.level, "unknown"),
    },
    {
      title: "Expansion Health",
      body: `expansion rate ${text(status.expansion_rate, "0")} / projected cache read ${text(status.projected_cache_read_tokens, "0")} tokens`,
      pill: text(status.expansion_level, "unknown"),
    },
    {
      title: "Latest Handoff",
      body: handoff.available ? text(handoff.task_summary, "handoff available") : "No handoff saved yet.",
      pill: handoff.available ? text(handoff.status, "active") : "empty",
    },
  ];
  items.forEach((item) => root.appendChild(summaryCard(item)));

  if (contextState.card) {
    const pre = document.createElement("pre");
    pre.className = "digest-preview";
    pre.textContent = contextState.card;
    root.appendChild(pre);
  }
}

function renderRepoContext(repoContext) {
  const root = $("#repo-context-list");
  root.replaceChildren();
  const entries = repoContext.entries || [];
  $("#repo-context-pill").textContent = `${text(repoContext.count, "0")} entries`;
  if (!entries.length) {
    root.appendChild(summaryCard({
      title: "No repo context yet",
      body: "Run `dhee link /path/to/repo` and promote decisions or conventions into .dhee/context.",
      pill: repoContext.exists ? "empty" : "not linked",
    }));
    return;
  }
  entries.forEach((item) => {
    root.appendChild(summaryCard({
      title: text(item.title || item.summary || item.id, "repo context"),
      body: text(item.content || item.body || item.summary || item.reason, ""),
      pill: text(item.kind || item.type || "note"),
    }));
  });
}

function renderPortability(portability) {
  const root = $("#portability-list");
  root.replaceChildren();
  [
    { title: "Export", body: text(portability.export), pill: ".dheemem" },
    { title: "Dry-run Import", body: text(portability.dry_run_import), pill: "inspect first" },
    { title: "Clean Uninstall", body: text(portability.uninstall), pill: "no lock-in" },
  ].forEach((item) => root.appendChild(summaryCard(item)));
}

function summaryCard(item) {
  const node = document.createElement("article");
  node.className = "context-item";
  node.innerHTML = `
    <div class="panel-heading">
      <h3>${text(item.title)}</h3>
      <span class="pill">${text(item.pill, "")}</span>
    </div>
    <div class="context-summary">${text(item.body, "")}</div>
  `;
  return node;
}

document.querySelectorAll(".tab").forEach((tab) => {
  tab.addEventListener("click", () => {
    const view = tab.dataset.view;
    document.querySelectorAll(".tab").forEach((node) => node.classList.toggle("active", node === tab));
    document.querySelectorAll(".view").forEach((node) => node.classList.toggle("active", node.id === `view-${view}`));
    state.activeView = view;
  });
});

$("#refresh-button").addEventListener("click", loadDashboard);
$("#seed-button").addEventListener("click", reloadDemo);
$("#snapshot-button").addEventListener("click", copySnapshot);

loadDashboard().catch((error) => {
  document.body.innerHTML = `<main class="app-shell"><div class="empty-state"><strong>Dashboard error</strong><span>${error.message}</span></div></main>`;
});
