const BASE = "/api";
async function j(path, init) {
    const res = await fetch(BASE + path, {
        ...init,
        headers: {
            "Content-Type": "application/json",
            ...(init?.headers || {}),
        },
    });
    if (!res.ok)
        throw new Error(`${res.status} ${res.statusText}`);
    return (await res.json());
}
export const api = {
    listMemories: () => j("/memories"),
    remember: (content, tier, tags) => j("/memories", {
        method: "POST",
        body: JSON.stringify({ content, tier, tags }),
    }),
    archiveMemory: (id) => j(`/memories/${encodeURIComponent(id)}`, {
        method: "DELETE",
    }),
    routerStats: (agentId) => j(`/router/stats${agentId ? `?agent_id=${encodeURIComponent(agentId)}` : ""}`),
    routerPolicy: () => j("/router/policy"),
    routerTune: () => j("/router/tune", { method: "POST" }),
    metaBuddhi: () => j("/meta-buddhi"),
    evolution: () => j("/evolution"),
    conflicts: () => j("/conflicts"),
    resolveConflict: (id, action) => j(`/conflicts/${encodeURIComponent(id)}/resolve`, {
        method: "POST",
        body: JSON.stringify({ action }),
    }),
    resolveConflictDetailed: (id, payload) => j(`/conflicts/${encodeURIComponent(id)}/resolve`, {
        method: "POST",
        body: JSON.stringify(payload),
    }),
    tasks: () => j("/tasks"),
    createTask: (title, harness) => j("/tasks", {
        method: "POST",
        body: JSON.stringify({ title, harness }),
    }),
    taskDetail: (taskId, limit = 24) => j(`/tasks/${encodeURIComponent(taskId)}?limit=${encodeURIComponent(String(limit))}`),
    updateTaskStatus: (taskId, status) => j(`/tasks/${encodeURIComponent(taskId)}/status`, {
        method: "POST",
        body: JSON.stringify({ status }),
    }),
    addTaskNote: (taskId, content) => j(`/tasks/${encodeURIComponent(taskId)}/notes`, {
        method: "POST",
        body: JSON.stringify({ content }),
    }),
    workspaceGraph: (workspaceId, projectId) => j(`/workspace/graph${workspaceId
        ? `?workspace_id=${encodeURIComponent(workspaceId)}${projectId ? `&project_id=${encodeURIComponent(projectId)}` : ""}`
        : ""}`),
    projects: () => j("/workspaces"),
    workspaces: () => j("/workspaces"),
    createWorkspaceRoot: (name, root_path, description) => j("/workspaces", {
        method: "POST",
        body: JSON.stringify({ name, root_path, description }),
    }),
    createProject: (workspaceId, payload) => j(`/workspaces/${encodeURIComponent(workspaceId)}/projects`, {
        method: "POST",
        body: JSON.stringify(payload),
    }),
    updateProject: (projectId, payload) => j(`/projects/${encodeURIComponent(projectId)}`, {
        method: "PATCH",
        body: JSON.stringify(payload),
    }),
    projectSessions: (projectId) => j(`/projects/${encodeURIComponent(projectId)}/sessions`),
    projectCanvas: (projectId) => j(`/projects/${encodeURIComponent(projectId)}/canvas`),
    workspaceCanvas: (workspaceId) => j(`/workspaces/${encodeURIComponent(workspaceId)}/canvas`),
    pickFolder: (prompt) => j("/folders/pick", {
        method: "POST",
        body: JSON.stringify({ prompt }),
    }),
    addWorkspaceFolder: (workspaceId, path, label) => j(`/workspaces/${encodeURIComponent(workspaceId)}/folders`, {
        method: "POST",
        body: JSON.stringify({ path, label }),
    }),
    removeWorkspaceFolder: (workspaceId, path) => j(`/workspaces/${encodeURIComponent(workspaceId)}/mounts?path=${encodeURIComponent(path)}`, { method: "DELETE" }),
    updateWorkspace: (workspaceId, payload) => j(`/workspaces/${encodeURIComponent(workspaceId)}`, {
        method: "PATCH",
        body: JSON.stringify(payload),
    }),
    deleteWorkspace: (workspaceId) => j(`/workspaces/${encodeURIComponent(workspaceId)}`, { method: "DELETE" }),
    deleteProject: (projectId) => j(`/projects/${encodeURIComponent(projectId)}`, { method: "DELETE" }),
    workspaceDetail: (workspaceId) => j(`/workspaces/${encodeURIComponent(workspaceId)}`),
    workspaceSessions: (workspaceId) => j(`/workspaces/${encodeURIComponent(workspaceId)}/sessions`),
    sessionDetail: (sessionId) => j(`/sessions/${encodeURIComponent(sessionId)}`),
    launchWorkspaceSession: (workspaceId, runtime, title, permission_mode, task_id, project_id) => j(`/workspaces/${encodeURIComponent(workspaceId)}/sessions/launch`, {
        method: "POST",
        body: JSON.stringify({ runtime, title, permission_mode, task_id, project_id }),
    }),
    workspaceLineMessages: (workspaceId, opts) => j(`/workspaces/${encodeURIComponent(workspaceId)}/line/messages?${new URLSearchParams(Object.entries({
        project_id: opts?.project_id,
        channel: opts?.channel,
        cursor: opts?.cursor,
        limit: opts?.limit ? String(opts.limit) : undefined,
    }).filter((entry) => Boolean(entry[1]))).toString()}`),
    publishWorkspaceLineMessage: (workspaceId, payload) => j(`/workspaces/${encodeURIComponent(workspaceId)}/line/messages`, {
        method: "POST",
        body: JSON.stringify(payload),
    }),
    uploadSessionAsset: async (sessionId, file, label) => {
        const form = new FormData();
        form.append("file", file);
        if (label)
            form.append("label", label);
        const res = await fetch(`${BASE}/sessions/${encodeURIComponent(sessionId)}/assets`, {
            method: "POST",
            body: form,
        });
        if (!res.ok)
            throw new Error(`${res.status} ${res.statusText}`);
        return (await res.json());
    },
    listProjectAssets: (projectId) => j(`/projects/${encodeURIComponent(projectId)}/assets`),
    listWorkspaceAssets: (workspaceId, includeProjectAssets = true) => j(`/workspaces/${encodeURIComponent(workspaceId)}/assets?include_project_assets=${includeProjectAssets ? "true" : "false"}`),
    uploadProjectAsset: async (projectId, file, opts) => {
        const form = new FormData();
        form.append("file", file);
        if (opts?.label)
            form.append("label", opts.label);
        if (opts?.folder)
            form.append("folder", opts.folder);
        const res = await fetch(`${BASE}/projects/${encodeURIComponent(projectId)}/assets`, {
            method: "POST",
            body: form,
        });
        if (!res.ok)
            throw new Error(`${res.status} ${res.statusText}`);
        return (await res.json());
    },
    uploadWorkspaceAsset: async (workspaceId, file, opts) => {
        const form = new FormData();
        form.append("file", file);
        if (opts?.label)
            form.append("label", opts.label);
        if (opts?.folder)
            form.append("folder", opts.folder);
        if (opts?.project_id)
            form.append("project_id", opts.project_id);
        const res = await fetch(`${BASE}/workspaces/${encodeURIComponent(workspaceId)}/assets`, {
            method: "POST",
            body: form,
        });
        if (!res.ok)
            throw new Error(`${res.status} ${res.statusText}`);
        return (await res.json());
    },
    deleteProjectAsset: (assetId) => j(`/project-assets/${encodeURIComponent(assetId)}`, { method: "DELETE" }),
    fileContext: (path, workspaceId) => j(`/files/${encodeURIComponent(path)}/context${workspaceId ? `?workspace_id=${encodeURIComponent(workspaceId)}` : ""}`),
    assetContext: (assetId) => j(`/assets/${encodeURIComponent(assetId)}/context`),
    askAsset: (assetId, question) => j(`/assets/${encodeURIComponent(assetId)}/ask`, {
        method: "POST",
        body: JSON.stringify({ question }),
    }),
    runtimeStatus: () => j("/runtime-status"),
    status: () => j("/status"),
    memoryNow: () => j("/memory/now"),
    captureTimeline: (limit = 16) => j(`/capture/timeline?limit=${encodeURIComponent(String(limit))}`),
    launch: (runtime, taskId, title) => j("/launch", {
        method: "POST",
        body: JSON.stringify({ runtime, taskId, title }),
    }),
    apiKeys: () => j("/security/api-keys"),
    storeApiKey: (provider, apiKey, label) => j("/security/api-keys", {
        method: "POST",
        body: JSON.stringify({ provider, apiKey, label }),
    }),
    rotateApiKey: (provider, apiKey, label) => j(`/security/api-keys/${encodeURIComponent(provider)}/rotate`, {
        method: "POST",
        body: JSON.stringify({ apiKey, label }),
    }),
};
