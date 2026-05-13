import type {
  CaptureTimelineItem,
  ApiKeyProviderStatus,
  ConflictSnapshot,
  Engram,
  EvolutionEvent,
  MemoryNowSnapshot,
  MetaBuddhiSnapshot,
  PolicyRow,
  ProjectAsset,
  RuntimeStatusCard,
  RouterStats,
  ProjectIndexSnapshot,
  ProjectSummary,
  WorkspaceLineMessage,
  SessionDetailSnapshot,
  AssetContextSummary,
  FileContextSummary,
  SessionAsset,
  TaskDetailSnapshot,
  SankhyaTask,
  WorkspaceDetailSnapshot,
  WorkspaceGraphSnapshot,
  Viewer,
  OrgGraphSnapshot,
  ContextItem,
  Proposal,
  Finding,
  InboxSnapshot,
  BacklinksSnapshot,
  ContinuitySnapshot,
  LocalWorkspace,
  RouterSessionsPage,
  ContextUsageSnapshot,
  ContextEntriesSnapshot,
} from "./types";

const BASE = "/api";

async function j<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(BASE + path, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...(init?.headers || {}),
    },
  });
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
  return (await res.json()) as T;
}

export const api = {
  listMemories: () =>
    j<{ live: boolean; engrams: Engram[]; count: number; error?: string }>(
      "/memories"
    ),
  remember: (content: string, tier?: string, tags?: string[]) =>
    j<{ ok: boolean }>("/memories", {
      method: "POST",
      body: JSON.stringify({ content, tier, tags }),
    }),
  archiveMemory: (id: string) =>
    j<{ ok: boolean }>(`/memories/${encodeURIComponent(id)}`, {
      method: "DELETE",
    }),
  routerStats: (agentId?: string) =>
    j<RouterStats>(
      `/router/stats${agentId ? `?agent_id=${encodeURIComponent(agentId)}` : ""}`
    ),
  routerPolicy: () =>
    j<{ live: boolean; policies: PolicyRow[]; error?: string }>(
      "/router/policy"
    ),
  routerTune: () =>
    j<{
      ok: boolean;
      applied: number;
      human: string;
      suggestions: {
        tool: string;
        intent: string;
        from: string;
        to: string;
        reason: string;
      }[];
    }>("/router/tune", { method: "POST" }),
  metaBuddhi: () => j<MetaBuddhiSnapshot>("/meta-buddhi"),
  evolution: () =>
    j<{ live: boolean; events: EvolutionEvent[] }>("/evolution"),
  conflicts: () => j<ConflictSnapshot>("/conflicts"),
  resolveConflict: (id: string, action: string) =>
    j<{ ok: boolean }>(`/conflicts/${encodeURIComponent(id)}/resolve`, {
      method: "POST",
      body: JSON.stringify({ action }),
    }),
  resolveConflictDetailed: (
    id: string,
    payload: { action: string; merged_content?: string; reason?: string }
  ) =>
    j<{ ok: boolean; result?: Record<string, unknown> }>(
      `/conflicts/${encodeURIComponent(id)}/resolve`,
      {
        method: "POST",
        body: JSON.stringify(payload),
      }
    ),
  tasks: () => j<{ live: boolean; tasks: SankhyaTask[] }>("/tasks"),
  createTask: (title: string, harness?: string | null) =>
    j<{ ok: boolean; task: SankhyaTask }>("/tasks", {
      method: "POST",
      body: JSON.stringify({ title, harness }),
    }),
  taskDetail: (taskId: string, limit = 24) =>
    j<TaskDetailSnapshot>(
      `/tasks/${encodeURIComponent(taskId)}?limit=${encodeURIComponent(String(limit))}`
    ),
  updateTaskStatus: (taskId: string, status: string) =>
    j<{ ok: boolean; task: SankhyaTask }>(
      `/tasks/${encodeURIComponent(taskId)}/status`,
      {
        method: "POST",
        body: JSON.stringify({ status }),
      }
    ),
  addTaskNote: (taskId: string, content: string) =>
    j<{ ok: boolean; task: SankhyaTask; result: Record<string, unknown> }>(
      `/tasks/${encodeURIComponent(taskId)}/notes`,
      {
        method: "POST",
        body: JSON.stringify({ content }),
      }
    ),
  workspaceGraph: (workspaceId?: string, projectId?: string) =>
    j<WorkspaceGraphSnapshot>(
      `/workspace/graph${
        workspaceId
          ? `?workspace_id=${encodeURIComponent(workspaceId)}${
              projectId ? `&project_id=${encodeURIComponent(projectId)}` : ""
            }`
          : ""
      }`
    ),
  projects: () => j<ProjectIndexSnapshot>("/workspaces"),
  workspaces: () => j<ProjectIndexSnapshot>("/workspaces"),
  createWorkspaceRoot: (name: string, description?: string) =>
    j<{ ok: boolean; workspace: WorkspaceDetailSnapshot["workspace"] }>("/workspaces", {
      method: "POST",
      body: JSON.stringify({ name, description }),
    }),
  createProject: (workspaceId: string, payload: { name: string; description?: string; default_runtime?: string; color?: string; icon?: string; scope_rules?: { path_prefix: string; label?: string }[] }) =>
    j<{ ok: boolean; project: ProjectSummary }>(
      `/workspaces/${encodeURIComponent(workspaceId)}/projects`,
      {
        method: "POST",
        body: JSON.stringify(payload),
      }
    ),
  updateProject: (projectId: string, payload: { name?: string; description?: string; default_runtime?: string; color?: string; icon?: string; scope_rules?: { path_prefix: string; label?: string }[] }) =>
    j<{ ok: boolean; project: ProjectSummary }>(
      `/projects/${encodeURIComponent(projectId)}`,
      {
        method: "PATCH",
        body: JSON.stringify(payload),
      }
    ),
  projectSessions: (projectId: string) =>
    j<{ live: boolean; sessions: SessionDetailSnapshot["session"][] }>(
      `/projects/${encodeURIComponent(projectId)}/sessions`
    ),
  projectCanvas: (projectId: string) =>
    j<WorkspaceGraphSnapshot>(`/projects/${encodeURIComponent(projectId)}/canvas`),
  workspaceCanvas: (workspaceId: string) =>
    j<WorkspaceGraphSnapshot>(`/workspaces/${encodeURIComponent(workspaceId)}/canvas`),
  pickFolder: (prompt?: string) =>
    j<{ ok: boolean; cancelled?: boolean; path?: string }>("/folders/pick", {
      method: "POST",
      body: JSON.stringify({ prompt }),
    }),
  addWorkspaceFolder: (workspaceId: string, path: string, label?: string) =>
    j<{ ok: boolean; workspace: WorkspaceDetailSnapshot["workspace"] }>(
      `/workspaces/${encodeURIComponent(workspaceId)}/folders`,
      {
        method: "POST",
        body: JSON.stringify({ path, label }),
      }
    ),
  removeWorkspaceFolder: (workspaceId: string, path: string) =>
    j<{ ok: boolean; workspace: WorkspaceDetailSnapshot["workspace"] }>(
      `/workspaces/${encodeURIComponent(workspaceId)}/mounts?path=${encodeURIComponent(path)}`,
      { method: "DELETE" }
    ),
  updateWorkspace: (
    workspaceId: string,
    payload: { label?: string; description?: string },
  ) =>
    j<{ ok: boolean; workspace: WorkspaceDetailSnapshot["workspace"] }>(
      `/workspaces/${encodeURIComponent(workspaceId)}`,
      {
        method: "PATCH",
        body: JSON.stringify(payload),
      }
    ),
  deleteWorkspace: (workspaceId: string) =>
    j<{ ok: boolean; id: string }>(
      `/workspaces/${encodeURIComponent(workspaceId)}`,
      { method: "DELETE" },
    ),
  deleteProject: (projectId: string) =>
    j<{ ok: boolean; id: string; workspace_id?: string }>(
      `/projects/${encodeURIComponent(projectId)}`,
      { method: "DELETE" },
    ),
  workspaceDetail: (workspaceId: string) =>
    j<WorkspaceDetailSnapshot>(`/workspaces/${encodeURIComponent(workspaceId)}`),
  workspaceSessions: (workspaceId: string) =>
    j<{ live: boolean; sessions: WorkspaceDetailSnapshot["sessions"] }>(
      `/workspaces/${encodeURIComponent(workspaceId)}/sessions`
    ),
  sessionDetail: (sessionId: string) =>
    j<SessionDetailSnapshot>(`/sessions/${encodeURIComponent(sessionId)}`),
  launchWorkspaceSession: (
    workspaceId: string,
    runtime: string,
    title?: string,
    permission_mode?: string,
    task_id?: string,
    project_id?: string
  ) =>
    j<{
      ok: boolean;
      project_id?: string;
      workspace_id?: string;
      session_id?: string;
      task_id?: string;
      runtime: string;
      permission_mode?: string;
      launch_command: string;
      control_state: string;
    }>(`/workspaces/${encodeURIComponent(workspaceId)}/sessions/launch`, {
      method: "POST",
      body: JSON.stringify({ runtime, title, permission_mode, task_id, project_id }),
    }),
  workspaceLineMessages: (
    workspaceId: string,
    opts?: { project_id?: string; channel?: string; cursor?: string; limit?: number }
  ) =>
    j<{ live: boolean; messages: WorkspaceLineMessage[]; nextCursor?: string }>(
      `/workspaces/${encodeURIComponent(workspaceId)}/line/messages?${new URLSearchParams(
        Object.entries({
          project_id: opts?.project_id,
          channel: opts?.channel,
          cursor: opts?.cursor,
          limit: opts?.limit ? String(opts.limit) : undefined,
        }).filter((entry): entry is [string, string] => Boolean(entry[1]))
      ).toString()}`
    ),
  publishWorkspaceLineMessage: (
    workspaceId: string,
    payload: {
      project_id?: string;
      target_project_id?: string;
      channel?: string;
      session_id?: string;
      task_id?: string;
      message_kind?: string;
      title?: string;
      body: string;
      metadata?: Record<string, unknown>;
    }
  ) =>
    j<{ ok: boolean; message: WorkspaceLineMessage; suggestedTask?: SankhyaTask | null }>(
      `/workspaces/${encodeURIComponent(workspaceId)}/line/messages`,
      {
        method: "POST",
        body: JSON.stringify(payload),
      }
    ),
  uploadSessionAsset: async (sessionId: string, file: File, label?: string) => {
    const form = new FormData();
    form.append("file", file);
    if (label) form.append("label", label);
    const res = await fetch(`${BASE}/sessions/${encodeURIComponent(sessionId)}/assets`, {
      method: "POST",
      body: form,
    });
    if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
    return (await res.json()) as { ok: boolean; asset: SessionAsset };
  },
  listProjectAssets: (projectId: string) =>
    j<{ live: boolean; project_id: string; workspace_id: string; assets: ProjectAsset[] }>(
      `/projects/${encodeURIComponent(projectId)}/assets`,
    ),
  listWorkspaceAssets: (workspaceId: string, includeProjectAssets = true) =>
    j<{ live: boolean; workspace_id: string; assets: ProjectAsset[] }>(
      `/workspaces/${encodeURIComponent(workspaceId)}/assets?include_project_assets=${
        includeProjectAssets ? "true" : "false"
      }`,
    ),
  uploadProjectAsset: async (projectId: string, file: File, opts?: { label?: string; folder?: string }) => {
    const form = new FormData();
    form.append("file", file);
    if (opts?.label) form.append("label", opts.label);
    if (opts?.folder) form.append("folder", opts.folder);
    const res = await fetch(`${BASE}/projects/${encodeURIComponent(projectId)}/assets`, {
      method: "POST",
      body: form,
    });
    if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
    return (await res.json()) as { ok: boolean; asset: ProjectAsset };
  },
  uploadWorkspaceAsset: async (
    workspaceId: string,
    file: File,
    opts?: { label?: string; folder?: string; project_id?: string },
  ) => {
    const form = new FormData();
    form.append("file", file);
    if (opts?.label) form.append("label", opts.label);
    if (opts?.folder) form.append("folder", opts.folder);
    if (opts?.project_id) form.append("project_id", opts.project_id);
    const res = await fetch(`${BASE}/workspaces/${encodeURIComponent(workspaceId)}/assets`, {
      method: "POST",
      body: form,
    });
    if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
    return (await res.json()) as { ok: boolean; asset: ProjectAsset };
  },
  deleteProjectAsset: (assetId: string) =>
    j<{ ok: boolean }>(`/project-assets/${encodeURIComponent(assetId)}`, { method: "DELETE" }),
  fileContext: (path: string, workspaceId?: string) =>
    j<{ live: boolean } & FileContextSummary>(
      `/files/${encodeURIComponent(path)}/context${workspaceId ? `?workspace_id=${encodeURIComponent(workspaceId)}` : ""}`
    ),
  assetContext: (assetId: string) =>
    j<{ live: boolean } & AssetContextSummary>(`/assets/${encodeURIComponent(assetId)}/context`),
  askAsset: (assetId: string, question: string) =>
    j<{
      ok: boolean;
      launch: {
        task_id?: string;
        session_id?: string;
        launch_command?: string;
      };
      question: string;
    }>(`/assets/${encodeURIComponent(assetId)}/ask`, {
      method: "POST",
      body: JSON.stringify({ question }),
    }),
  runtimeStatus: () =>
    j<{ live: boolean; repo: string; runtimes: RuntimeStatusCard[]; error?: string }>(
      "/runtime-status"
    ),
  status: () =>
    j<{
      ok: boolean;
      router?: { sessions: number; calls: number; tokensSaved: number };
      dhee_data_dir?: string;
      error?: string;
    }>("/status"),
  memoryNow: () => j<MemoryNowSnapshot>("/memory/now"),
  captureTimeline: (limit = 16) =>
    j<{ items: CaptureTimelineItem[] }>(
      `/capture/timeline?limit=${encodeURIComponent(String(limit))}`
    ),
  launch: (runtime: string, taskId?: string, title?: string) =>
    j<{ ok: boolean; command: string; message: string; task?: SankhyaTask; taskId?: string }>(
      "/launch",
      {
      method: "POST",
      body: JSON.stringify({ runtime, taskId, title }),
      }
    ),
  apiKeys: () =>
    j<{ live: boolean; providers: ApiKeyProviderStatus[]; error?: string }>(
      "/security/api-keys"
    ),
  storeApiKey: (provider: string, apiKey: string, label?: string) =>
    j<{ ok: boolean; provider: ApiKeyProviderStatus }>("/security/api-keys", {
      method: "POST",
      body: JSON.stringify({ provider, apiKey, label }),
    }),
  rotateApiKey: (provider: string, apiKey: string, label?: string) =>
    j<{ ok: boolean; provider: ApiKeyProviderStatus }>(
      `/security/api-keys/${encodeURIComponent(provider)}/rotate`,
      {
        method: "POST",
        body: JSON.stringify({ apiKey, label }),
      }
    ),
  me: () => j<Viewer>("/me"),
  continuity: () => j<ContinuitySnapshot>("/continuity"),
  orgGraph: (org?: string, opts?: { active?: boolean }) => {
    const qs = new URLSearchParams();
    if (org) qs.set("org", org);
    if (opts?.active) qs.set("active", "true");
    const q = qs.toString();
    return j<OrgGraphSnapshot>(`/org/graph${q ? `?${q}` : ""}`);
  },
  routerSessions: (opts?: {
    active?: boolean;
    cursor?: string;
    limit?: number;
    agent?: string;
  }) => {
    const qs = new URLSearchParams();
    if (opts?.active != null) qs.set("active", opts.active ? "true" : "false");
    if (opts?.cursor) qs.set("cursor", opts.cursor);
    if (opts?.limit) qs.set("limit", String(opts.limit));
    if (opts?.agent) qs.set("agent", opts.agent);
    const q = qs.toString();
    return j<RouterSessionsPage>(`/router/sessions${q ? `?${q}` : ""}`);
  },
  contextEntries: (repo?: string, limit = 200) => {
    const qs = new URLSearchParams();
    if (repo) qs.set("repo", repo);
    qs.set("limit", String(limit));
    return j<ContextEntriesSnapshot>(`/context/entries?${qs.toString()}`);
  },
  contextPromote: (payload: {
    memory_id: string;
    repo?: string;
    kind?: string;
    title?: string;
  }) =>
    j<{ ok: boolean; entry: Record<string, unknown>; repo_root: string }>(
      "/context/promote",
      { method: "POST", body: JSON.stringify(payload) },
    ),
  contextDemote: (payload: { entry_id: string; repo?: string }) =>
    j<{ ok: boolean; memory_id: string; entry: Record<string, unknown> }>(
      "/context/demote",
      { method: "POST", body: JSON.stringify(payload) },
    ),
  localWorkspaces: () =>
    j<{ workspaces: LocalWorkspace[]; max_workspaces: number }>(
      "/local/workspaces",
    ),
  localWorkspaceCreate: (payload: { id?: string; name?: string }) =>
    j<{ ok: boolean; workspace: LocalWorkspace }>("/local/workspaces", {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  localContextLinkFolder: (path: string) =>
    j<{ ok: boolean; folder: Record<string, unknown>; link: { linked: boolean } }>(
      "/local-context/folders/link",
      { method: "POST", body: JSON.stringify({ path }) },
    ),
  localContextUnlinkFolder: (path: string) =>
    j<{ ok: boolean }>("/local-context/folders/unlink", {
      method: "POST",
      body: JSON.stringify({ path }),
    }),
  contextItems: (filters: { team?: string; project?: string; scope?: string; kind?: string; limit?: number } = {}) => {
    const qs = new URLSearchParams();
    if (filters.team) qs.set("team", filters.team);
    if (filters.project) qs.set("project", filters.project);
    if (filters.scope) qs.set("scope", filters.scope);
    if (filters.kind) qs.set("kind", filters.kind);
    if (filters.limit) qs.set("limit", String(filters.limit));
    const q = qs.toString();
    return j<{ live: boolean; items: ContextItem[]; error?: string }>(
      `/context/items${q ? `?${q}` : ""}`
    );
  },
  contextUsage: (filters: { team?: string; project?: string; scope?: string; kind?: string; limit?: number } = {}) => {
    const qs = new URLSearchParams();
    if (filters.team) qs.set("team", filters.team);
    if (filters.project) qs.set("project", filters.project);
    if (filters.scope) qs.set("scope", filters.scope);
    if (filters.kind) qs.set("kind", filters.kind);
    if (filters.limit) qs.set("limit", String(filters.limit));
    const q = qs.toString();
    return j<ContextUsageSnapshot>(`/context/usage${q ? `?${q}` : ""}`);
  },
  commandCenter: () => j<Record<string, unknown>>("/ui/command-center"),
  proofReplay: (limit = 80) =>
    j<Record<string, unknown>>(`/ui/proof-replay?limit=${encodeURIComponent(String(limit))}`),
  handoffUi: () => j<Record<string, unknown>>("/ui/handoff"),
  learningsUi: (limit = 120) =>
    j<Record<string, unknown>>(`/ui/learnings?limit=${encodeURIComponent(String(limit))}`),
  promoteLearning: (id: string, payload?: { scope?: string; repo?: string; approved_by?: string }) =>
    j<Record<string, unknown>>(`/ui/learnings/${encodeURIComponent(id)}/promote`, {
      method: "POST",
      body: JSON.stringify(payload || { approved_by: "dhee-ui" }),
    }),
  rejectLearning: (id: string, payload?: { reason?: string }) =>
    j<Record<string, unknown>>(`/ui/learnings/${encodeURIComponent(id)}/reject`, {
      method: "POST",
      body: JSON.stringify(payload || { reason: "rejected in Dhee UI" }),
    }),
  portabilityUi: () => j<Record<string, unknown>>("/ui/portability"),
  exportPackUi: (payload?: { output_path?: string; user_id?: string; repo?: string }) =>
    j<Record<string, unknown>>("/ui/portability/export", {
      method: "POST",
      body: JSON.stringify(payload || {}),
    }),
  importPackDryRunUi: (payload: { input_path: string; user_id?: string; repo?: string }) =>
    j<Record<string, unknown>>("/ui/portability/import-dry-run", {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  upsertContext: (payload: {
    context_id?: string;
    title: string;
    content: string;
    scope: string;
    kind?: string;
    project_id?: string;
    team_id?: string;
    user_id?: string;
    tags?: string[];
    summary?: string;
    metadata?: Record<string, unknown>;
  }) =>
    j<{ ok: boolean; item: ContextItem }>("/context", {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  proposeContext: (payload: {
    title: string;
    content: string;
    scope: string;
    kind: string;
    proposed_by_user_id: string;
    project_id?: string;
    team_id?: string;
    supersedes_id?: string;
    tags?: string[];
    metadata?: Record<string, unknown>;
  }) =>
    j<{ ok: boolean; proposal: Proposal }>("/proposals", {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  approveProposal: (contextId: string, reviewerUserId: string) =>
    j<{ ok: boolean; proposal: Proposal }>(
      `/proposals/${encodeURIComponent(contextId)}/approve`,
      {
        method: "POST",
        body: JSON.stringify({ reviewer_user_id: reviewerUserId }),
      }
    ),
  rejectProposal: (contextId: string, reviewerUserId: string) =>
    j<{ ok: boolean; proposal: Proposal }>(
      `/proposals/${encodeURIComponent(contextId)}/reject`,
      {
        method: "POST",
        body: JSON.stringify({ reviewer_user_id: reviewerUserId }),
      }
    ),
  inbox: (filter: { team?: string; user?: string } = {}) => {
    const qs = new URLSearchParams();
    if (filter.team) qs.set("team", filter.team);
    if (filter.user) qs.set("user", filter.user);
    const q = qs.toString();
    return j<InboxSnapshot>(`/inbox${q ? `?${q}` : ""}`);
  },
  resolveFinding: (findingId: string, resolvedBy?: string) =>
    j<{ ok: boolean; finding: Finding }>(
      `/findings/${encodeURIComponent(findingId)}/resolve`,
      {
        method: "POST",
        body: JSON.stringify({ resolved_by: resolvedBy }),
      }
    ),
  backlinks: (contextId: string, limit = 50) =>
    j<BacklinksSnapshot>(
      `/backlinks?context_id=${encodeURIComponent(contextId)}&limit=${encodeURIComponent(String(limit))}`
    ),
  setIntegration: (payload: {
    scope: string;
    target_id: string;
    type: string;
    value: unknown;
    metadata?: Record<string, unknown>;
  }) =>
    j<{ ok: boolean; node: Record<string, unknown> }>("/integrations", {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  teamJoin: (payload: {
    org_id: string;
    project_id?: string;
    team_id?: string;
    role?: string;
    repo_root?: string;
  }) =>
    j<{ ok: boolean; joined: Record<string, unknown> }>("/team-join", {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  localContextAddFolder: (payload: { path: string; shared?: boolean }) =>
    j<{ ok: boolean; folder: Record<string, unknown> }>("/local-context/folders", {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  localContextShareFolder: (payload: { path: string; shared?: boolean }) =>
    j<{ ok: boolean; folder: Record<string, unknown> }>("/local-context/folders/share", {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  enterpriseSetWorkspace: (payload: {
    name: string;
    root_path?: string;
    default_branch?: string;
  }) =>
    j<{ ok: boolean; workspace: Record<string, unknown> }>("/workspace", {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  enterpriseResetWorkspace: () =>
    j<{ ok: boolean; deleted: Record<string, number> }>("/workspace/reset", {
      method: "POST",
      body: "{}",
    }),
  enterpriseCreateProject: (payload: {
    name: string;
    project_id?: string;
    description?: string;
  }) =>
    j<{ ok: boolean; project: Record<string, unknown> }>("/projects", {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  enterpriseDeleteProject: (projectId: string) =>
    j<{ ok: boolean; project_id: string }>(
      `/projects/${encodeURIComponent(projectId)}`,
      { method: "DELETE" }
    ),
  enterpriseCreateProjectTeam: (
    projectId: string,
    payload: {
      name: string;
      team_id?: string;
      description?: string;
    }
  ) =>
    j<{ ok: boolean; team: Record<string, unknown> }>(
      `/projects/${encodeURIComponent(projectId)}/teams`,
      {
        method: "POST",
        body: JSON.stringify(payload),
      }
    ),
  enterpriseAddProjectFolder: (
    projectId: string,
    payload: {
      local_path?: string;
      repo_url?: string;
      label?: string;
      kind?: string;
    }
  ) =>
    j<{ ok: boolean; mapping: Record<string, unknown> }>(
      `/projects/${encodeURIComponent(projectId)}/folders`,
      {
        method: "POST",
        body: JSON.stringify(payload),
      }
    ),
  enterpriseAddTeamFolder: (
    teamId: string,
    payload: {
      local_path?: string;
      repo_url?: string;
      label?: string;
      kind?: string;
    }
  ) =>
    j<{ ok: boolean; mapping: Record<string, unknown> }>(
      `/teams/${encodeURIComponent(teamId)}/folders`,
      {
        method: "POST",
        body: JSON.stringify(payload),
      }
    ),
  enterpriseRemoveFolder: (mappingId: string) =>
    j<{ ok: boolean; mapping_id: string }>(
      `/folders/${encodeURIComponent(mappingId)}`,
      { method: "DELETE" }
    ),
  enterpriseAddTeamCollaborator: (teamId: string, targetTeamId: string) =>
    j<{
      ok: boolean;
      team: Record<string, unknown>;
      target_team: Record<string, unknown>;
      collaborating_team_ids: string[];
    }>(`/teams/${encodeURIComponent(teamId)}/collaborators`, {
      method: "POST",
      body: JSON.stringify({ target_team_id: targetTeamId }),
    }),
  enterpriseExtractProject: (projectId: string) =>
    j<{
      ok: boolean;
      project_id: string;
      folders_seen: number;
      files_seen: number;
      files_extracted: number;
      files_cached: number;
      nodes_upserted: number;
      edges_upserted: number;
      errors: { path: string; error: string }[];
    }>(`/projects/${encodeURIComponent(projectId)}/extract`, {
      method: "POST",
      body: "{}",
    }),
  enterpriseExtractTeam: (teamId: string) =>
    j<{
      ok: boolean;
      project_id: string;
      team_id?: string | null;
      folders_seen: number;
      files_seen: number;
      files_extracted: number;
      files_cached: number;
      nodes_upserted: number;
      edges_upserted: number;
      errors: { path: string; error: string }[];
    }>(`/teams/${encodeURIComponent(teamId)}/extract`, {
      method: "POST",
      body: "{}",
    }),
  pickFolderPath: (prompt?: string) =>
    j<{ ok: boolean; cancelled?: boolean; path?: string }>("/folders/pick", {
      method: "POST",
      body: JSON.stringify({ prompt }),
    }),
};
