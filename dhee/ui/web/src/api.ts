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
};
