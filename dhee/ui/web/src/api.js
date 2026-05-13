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
    createWorkspaceRoot: (name, description) => j("/workspaces", {
        method: "POST",
        body: JSON.stringify({ name, description }),
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
    me: () => j("/me"),
    continuity: () => j("/continuity"),
    orgGraph: (org, opts) => {
        const qs = new URLSearchParams();
        if (org)
            qs.set("org", org);
        if (opts?.active)
            qs.set("active", "true");
        const q = qs.toString();
        return j(`/org/graph${q ? `?${q}` : ""}`);
    },
    routerSessions: (opts) => {
        const qs = new URLSearchParams();
        if (opts?.active != null)
            qs.set("active", opts.active ? "true" : "false");
        if (opts?.cursor)
            qs.set("cursor", opts.cursor);
        if (opts?.limit)
            qs.set("limit", String(opts.limit));
        if (opts?.agent)
            qs.set("agent", opts.agent);
        const q = qs.toString();
        return j(`/router/sessions${q ? `?${q}` : ""}`);
    },
    contextEntries: (repo, limit = 200) => {
        const qs = new URLSearchParams();
        if (repo)
            qs.set("repo", repo);
        qs.set("limit", String(limit));
        return j(`/context/entries?${qs.toString()}`);
    },
    contextPromote: (payload) => j("/context/promote", { method: "POST", body: JSON.stringify(payload) }),
    contextDemote: (payload) => j("/context/demote", { method: "POST", body: JSON.stringify(payload) }),
    localWorkspaces: () => j("/local/workspaces"),
    localWorkspaceCreate: (payload) => j("/local/workspaces", {
        method: "POST",
        body: JSON.stringify(payload),
    }),
    localContextLinkFolder: (path) => j("/local-context/folders/link", { method: "POST", body: JSON.stringify({ path }) }),
    localContextUnlinkFolder: (path) => j("/local-context/folders/unlink", {
        method: "POST",
        body: JSON.stringify({ path }),
    }),
    contextItems: (filters = {}) => {
        const qs = new URLSearchParams();
        if (filters.team)
            qs.set("team", filters.team);
        if (filters.project)
            qs.set("project", filters.project);
        if (filters.scope)
            qs.set("scope", filters.scope);
        if (filters.kind)
            qs.set("kind", filters.kind);
        if (filters.limit)
            qs.set("limit", String(filters.limit));
        const q = qs.toString();
        return j(`/context/items${q ? `?${q}` : ""}`);
    },
    contextUsage: (filters = {}) => {
        const qs = new URLSearchParams();
        if (filters.team)
            qs.set("team", filters.team);
        if (filters.project)
            qs.set("project", filters.project);
        if (filters.scope)
            qs.set("scope", filters.scope);
        if (filters.kind)
            qs.set("kind", filters.kind);
        if (filters.limit)
            qs.set("limit", String(filters.limit));
        const q = qs.toString();
        return j(`/context/usage${q ? `?${q}` : ""}`);
    },
    commandCenter: () => j("/ui/command-center"),
    proofReplay: (limit = 80) => j(`/ui/proof-replay?limit=${encodeURIComponent(String(limit))}`),
    handoffUi: () => j("/ui/handoff"),
    learningsUi: (limit = 120) => j(`/ui/learnings?limit=${encodeURIComponent(String(limit))}`),
    promoteLearning: (id, payload) => j(`/ui/learnings/${encodeURIComponent(id)}/promote`, {
        method: "POST",
        body: JSON.stringify(payload || { approved_by: "dhee-ui" }),
    }),
    rejectLearning: (id, payload) => j(`/ui/learnings/${encodeURIComponent(id)}/reject`, {
        method: "POST",
        body: JSON.stringify(payload || { reason: "rejected in Dhee UI" }),
    }),
    portabilityUi: () => j("/ui/portability"),
    exportPackUi: (payload) => j("/ui/portability/export", {
        method: "POST",
        body: JSON.stringify(payload || {}),
    }),
    importPackDryRunUi: (payload) => j("/ui/portability/import-dry-run", {
        method: "POST",
        body: JSON.stringify(payload),
    }),
    upsertContext: (payload) => j("/context", {
        method: "POST",
        body: JSON.stringify(payload),
    }),
    proposeContext: (payload) => j("/proposals", {
        method: "POST",
        body: JSON.stringify(payload),
    }),
    approveProposal: (contextId, reviewerUserId) => j(`/proposals/${encodeURIComponent(contextId)}/approve`, {
        method: "POST",
        body: JSON.stringify({ reviewer_user_id: reviewerUserId }),
    }),
    rejectProposal: (contextId, reviewerUserId) => j(`/proposals/${encodeURIComponent(contextId)}/reject`, {
        method: "POST",
        body: JSON.stringify({ reviewer_user_id: reviewerUserId }),
    }),
    inbox: (filter = {}) => {
        const qs = new URLSearchParams();
        if (filter.team)
            qs.set("team", filter.team);
        if (filter.user)
            qs.set("user", filter.user);
        const q = qs.toString();
        return j(`/inbox${q ? `?${q}` : ""}`);
    },
    resolveFinding: (findingId, resolvedBy) => j(`/findings/${encodeURIComponent(findingId)}/resolve`, {
        method: "POST",
        body: JSON.stringify({ resolved_by: resolvedBy }),
    }),
    backlinks: (contextId, limit = 50) => j(`/backlinks?context_id=${encodeURIComponent(contextId)}&limit=${encodeURIComponent(String(limit))}`),
    setIntegration: (payload) => j("/integrations", {
        method: "POST",
        body: JSON.stringify(payload),
    }),
    teamJoin: (payload) => j("/team-join", {
        method: "POST",
        body: JSON.stringify(payload),
    }),
    localContextAddFolder: (payload) => j("/local-context/folders", {
        method: "POST",
        body: JSON.stringify(payload),
    }),
    localContextShareFolder: (payload) => j("/local-context/folders/share", {
        method: "POST",
        body: JSON.stringify(payload),
    }),
    enterpriseSetWorkspace: (payload) => j("/workspace", {
        method: "POST",
        body: JSON.stringify(payload),
    }),
    enterpriseResetWorkspace: () => j("/workspace/reset", {
        method: "POST",
        body: "{}",
    }),
    enterpriseCreateProject: (payload) => j("/projects", {
        method: "POST",
        body: JSON.stringify(payload),
    }),
    enterpriseDeleteProject: (projectId) => j(`/projects/${encodeURIComponent(projectId)}`, { method: "DELETE" }),
    enterpriseCreateProjectTeam: (projectId, payload) => j(`/projects/${encodeURIComponent(projectId)}/teams`, {
        method: "POST",
        body: JSON.stringify(payload),
    }),
    enterpriseAddProjectFolder: (projectId, payload) => j(`/projects/${encodeURIComponent(projectId)}/folders`, {
        method: "POST",
        body: JSON.stringify(payload),
    }),
    enterpriseAddTeamFolder: (teamId, payload) => j(`/teams/${encodeURIComponent(teamId)}/folders`, {
        method: "POST",
        body: JSON.stringify(payload),
    }),
    enterpriseRemoveFolder: (mappingId) => j(`/folders/${encodeURIComponent(mappingId)}`, { method: "DELETE" }),
    enterpriseAddTeamCollaborator: (teamId, targetTeamId) => j(`/teams/${encodeURIComponent(teamId)}/collaborators`, {
        method: "POST",
        body: JSON.stringify({ target_team_id: targetTeamId }),
    }),
    enterpriseExtractProject: (projectId) => j(`/projects/${encodeURIComponent(projectId)}/extract`, {
        method: "POST",
        body: "{}",
    }),
    enterpriseExtractTeam: (teamId) => j(`/teams/${encodeURIComponent(teamId)}/extract`, {
        method: "POST",
        body: "{}",
    }),
    pickFolderPath: (prompt) => j("/folders/pick", {
        method: "POST",
        body: JSON.stringify({ prompt }),
    }),
};
