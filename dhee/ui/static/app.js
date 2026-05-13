const state = {
  dashboard: null,
  activeView: "overview",
};

const $ = (selector) => document.querySelector(selector);

function text(value, fallback = "0") {
  if (value === undefined || value === null || value === "") return fallback;
  return String(value);
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

async function seedDemo() {
  await requestJson("/api/demo", { method: "POST" });
  await loadDashboard();
}

async function connectRealWorkspace() {
  await requestJson("/api/real?limit=80", { method: "POST" });
  await loadDashboard();
}

async function syncRepos() {
  await requestJson("/api/sync?limit=80", { method: "POST" });
  await loadDashboard();
}

async function reviewTeam(teamId) {
  await requestJson(`/api/review?team=${encodeURIComponent(teamId)}`, { method: "POST" });
  await loadDashboard();
}

function renderDashboard(dashboard) {
  const totals = dashboard.totals || {};
  const workspace = dashboard.workspace || {};
  $("#workspace-title").textContent = workspace.name || "Company Brain";
  $("#org-id-pill").textContent = dashboard.org_id || "default";
  setMetric("metric-projects", totals.projects);
  setMetric("metric-teams", totals.teams);
  setMetric("metric-managers", totals.context_managers);
  setMetric("metric-repos", totals.repo_mappings);
  setMetric("metric-context", totals.context_items);
  setMetric("metric-findings", totals.open_findings);
  setMetric("metric-indexed", totals.indexed_files);
  const firewall = dashboard.context_firewall || {};
  const firewallAggregate = firewall.aggregate || {};
  setMetric("metric-firewall", `${text(firewallAggregate.saved_pct, "0")}%`);
  renderOrgChart(dashboard.org_chart || {});
  renderCoverage(dashboard);
  renderRepos(dashboard.repo_mappings || []);
  renderRepoBrain(dashboard.code_brain || {});
  renderContextFirewall(firewall);
  renderTeams(dashboard.team_rows || []);
  renderContext(dashboard.context_index || []);
  renderFindings(dashboard.findings || []);
}

function renderOrgChart(orgChart) {
  const root = $("#org-chart");
  root.replaceChildren();
  const workspace = orgChart.workspace || {};
  const projects = orgChart.projects || [];
  const globals = orgChart.global_teams || [];
  if (!projects.length && !globals.length) {
    root.appendChild(emptyNode());
    return;
  }

  const workspaceNode = document.createElement("section");
  workspaceNode.className = "org-node";
  workspaceNode.innerHTML = `
    <h3>${text(workspace.name, "Workspace")}</h3>
    <div class="meta">${text(workspace.root_path, "No root path")} / ${text(workspace.default_branch, "main")}</div>
  `;
  root.appendChild(workspaceNode);

  if (globals.length) {
    const globalNode = document.createElement("section");
    globalNode.className = "org-node global";
    globalNode.innerHTML = `<h3>Global Teams</h3>`;
    const stack = document.createElement("div");
    stack.className = "team-stack";
    globals.forEach((team) => stack.appendChild(teamChip(team)));
    globalNode.appendChild(stack);
    root.appendChild(globalNode);
  }

  projects.forEach((project) => {
    const node = document.createElement("section");
    node.className = "org-node project";
    node.innerHTML = `
      <h3>${text(project.name, project.project_id)}</h3>
      <div class="meta">${(project.teams || []).length} teams / ${(project.repo_mappings || []).length} mapped repos</div>
    `;
    const stack = document.createElement("div");
    stack.className = "team-stack";
    (project.teams || []).forEach((team) => stack.appendChild(teamChip(team)));
    node.appendChild(stack);
    root.appendChild(node);
  });
}

function teamChip(team) {
  const node = document.createElement("article");
  node.className = "team-chip";
  const manager = team.context_manager || {};
  const findings = team.open_findings || [];
  node.innerHTML = `
    <h3>${text(team.name, team.team_id)}</h3>
    <div class="meta">
      <span class="pill">${text(manager.manager_id, "no manager")}</span>
      <span class="pill">${(team.repo_mappings || []).length} repos</span>
      <span class="pill">${findings.length} findings</span>
    </div>
  `;
  return node;
}

function renderCoverage(dashboard) {
  const root = $("#coverage-bars");
  root.replaceChildren();
  const counts = dashboard.kind_counts || {};
  const entries = Object.entries(counts).sort((a, b) => b[1] - a[1]);
  if (!entries.length) {
    root.appendChild(emptyNode());
    return;
  }
  const max = Math.max(...entries.map((entry) => entry[1]), 1);
  entries.forEach(([kind, count]) => {
    const row = document.createElement("div");
    row.className = "bar-row";
    row.innerHTML = `
      <span>${kind}</span>
      <span class="bar-track"><span class="bar-fill" style="width:${Math.max(6, (count / max) * 100)}%"></span></span>
      <strong>${count}</strong>
    `;
    root.appendChild(row);
  });
}

function renderRepos(repos) {
  const root = $("#repo-list");
  root.replaceChildren();
  if (!repos.length) {
    root.appendChild(emptyNode());
    return;
  }
  repos.slice(0, 12).forEach((repo) => {
    const item = document.createElement("article");
    item.className = "repo-item";
    item.innerHTML = `
      <h3>${text(repo.team_id)} <span class="muted">${text(repo.project_id, "global")}</span></h3>
      <div class="repo-path">${text(repo.repo_url || repo.local_path, "unmapped")}</div>
      <div class="row-actions">
        <span class="pill">${text(repo.branch, "main")}</span>
        <span class="pill">${text(repo.provider, "git")}</span>
      </div>
    `;
    root.appendChild(item);
  });
}

function renderRepoBrain(codeBrain) {
  const root = $("#repo-brain-list");
  root.replaceChildren();
  const mappings = codeBrain.mapping_status || [];
  $("#repo-brain-pill").textContent = `${codeBrain.indexed_files || 0} indexed`;
  if (!mappings.length) {
    root.appendChild(emptyNode());
    return;
  }
  mappings.forEach((mapping) => {
    const lastSync = mapping.last_sync || {};
    const item = document.createElement("article");
    item.className = "repo-item";
    item.innerHTML = `
      <div class="panel-heading">
        <h3>${text(mapping.team_id)} <span class="muted">${text(mapping.project_id, "global")}</span></h3>
        <span class="status ${mapping.sync_status === "indexed" ? "healthy" : "watch"}">${text(mapping.sync_status)}</span>
      </div>
      <div class="repo-path">${text(mapping.local_path || mapping.repo_url, "unmapped")}</div>
      <div class="row-actions">
        <span class="pill">${mapping.indexed_files || 0} files</span>
        <span class="pill">${Math.round((mapping.indexed_bytes || 0) / 1024)} KB</span>
        <span class="pill">${lastSync.files_warmed || 0} warmed</span>
      </div>
    `;
    root.appendChild(item);
  });
}

function renderContextFirewall(report) {
  const root = $("#firewall-list");
  if (!root) return;
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
      <span><strong>${text(item.raw_tokens)}</strong> raw</span>
      <span><strong>${text(item.digest_tokens)}</strong> digest</span>
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

function renderTeams(teams) {
  const root = $("#team-table");
  root.replaceChildren();
  $("#team-count-pill").textContent = `${teams.length} teams`;
  if (!teams.length) {
    root.appendChild(emptyNode());
    return;
  }
  teams.forEach((team) => {
    const manager = team.manager || {};
    const row = document.createElement("article");
    row.className = "team-row";
    row.innerHTML = `
      <div class="team-title">
        <strong>${text(team.name, team.team_id)}</strong>
        <span class="muted">${text(team.project_id, team.team_type)}</span>
      </div>
      <div class="team-title">
        <strong>${text(manager.display_name, "Context Manager")}</strong>
        <span class="muted">${text(manager.manager_id, "not assigned")}</span>
      </div>
      <span>${team.repo_count}</span>
      <span>${team.context_count}</span>
      <span>${team.open_findings}</span>
      <button class="icon-button" type="button" title="Review ${text(team.team_id)}">
        <span class="icon refresh-icon" aria-hidden="true"></span>
        <span>Review</span>
      </button>
    `;
    row.querySelector("button").addEventListener("click", () => reviewTeam(team.team_id));
    row.querySelector(".team-title").insertAdjacentHTML("beforeend", `<span class="status ${team.health}">${team.health}</span>`);
    root.appendChild(row);
  });
}

function renderContext(items) {
  const root = $("#context-list");
  root.replaceChildren();
  $("#context-count-pill").textContent = `${items.length} items`;
  if (!items.length) {
    root.appendChild(emptyNode());
    return;
  }
  items.forEach((item) => {
    const node = document.createElement("article");
    node.className = "context-item";
    node.innerHTML = `
      <div class="panel-heading">
        <h3>${text(item.title, item.kind)}</h3>
        <span class="pill">${text(item.scope)} / ${text(item.kind)}</span>
      </div>
      <div class="context-summary">${text(item.summary, "")}</div>
      <div class="row-actions">
        <span class="pill">${text(item.project_id, "company")}</span>
        <span class="pill">${text(item.team_id, "shared")}</span>
        <span class="pill">${(item.shares || []).length} shares</span>
      </div>
    `;
    root.appendChild(node);
  });
}

function renderFindings(findings) {
  const root = $("#finding-list");
  root.replaceChildren();
  $("#finding-count-pill").textContent = `${findings.length} open`;
  if (!findings.length) {
    root.appendChild(emptyNode());
    return;
  }
  findings.forEach((finding) => {
    const node = document.createElement("article");
    node.className = `finding-item ${finding.severity || "medium"}`;
    node.innerHTML = `
      <div class="panel-heading">
        <h3>${text(finding.title)}</h3>
        <span class="pill">${text(finding.severity)} / ${text(finding.finding_type)}</span>
      </div>
      <div class="finding-detail">${text(finding.detail, "")}</div>
      <div class="row-actions">
        <span class="pill">${text(finding.team_id)}</span>
        <span class="pill">${text(finding.manager_id)}</span>
      </div>
    `;
    root.appendChild(node);
  });
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
$("#seed-button").addEventListener("click", seedDemo);
$("#real-button").addEventListener("click", connectRealWorkspace);
$("#sync-button").addEventListener("click", syncRepos);

loadDashboard().catch((error) => {
  document.body.innerHTML = `<main class="app-shell"><div class="empty-state"><strong>Dashboard error</strong><span>${error.message}</span></div></main>`;
});
