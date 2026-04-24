import { jsx as _jsx, jsxs as _jsxs } from "react/jsx-runtime";
import { useEffect, useMemo, useRef, useState } from "react";
import { api } from "../api";
import { ChatMessage } from "../components/ChatMessage";
import { LineComposer, LineMessageCard, useWorkspaceLine } from "../components/LinePanel";
import { StatPill } from "../components/ui/StatPill";
function fmtTime(value) {
    if (!value)
        return "—";
    const date = new Date(value);
    if (Number.isNaN(date.getTime()))
        return "—";
    return date.toLocaleString();
}
function runtimeSummary(runtime) {
    if (!runtime)
        return "No runtime selected";
    if (runtime.currentSession?.state === "active")
        return "session live";
    if (runtime.installed)
        return "installed";
    return "not attached";
}
function workspaceRootPath(workspace) {
    const mounts = workspace?.mounts || workspace?.folders || [];
    const primary = mounts.find((mount) => mount.primary) || mounts[0];
    return primary?.path || workspace?.rootPath || workspace?.workspacePath || "";
}
export function WorkspaceView({ tasks, activeTaskId, selectedProjectId, selectedWorkspaceId, selectedSessionId, projectIndex, workspaceGraph, onSelectTask, onSelectSession, onSelectProject, onCanvasOpen, onNotepadOpen, onAddTaskNote, onUpdateWorkspace, onAddWorkspaceFolder, onRemoveWorkspaceFolder, onCreateProject, onUpdateProject, onTasksRefresh, tweaks, }) {
    const [detail, setDetail] = useState(null);
    const [workspaceRuntime, setWorkspaceRuntime] = useState([]);
    const [input, setInput] = useState("");
    const [saving, setSaving] = useState(false);
    const [uploading, setUploading] = useState(false);
    const [selectedFile, setSelectedFile] = useState(null);
    const [selectedAsset, setSelectedAsset] = useState(null);
    const [assetQuestion, setAssetQuestion] = useState("");
    const [showWorkspaceModal, setShowWorkspaceModal] = useState(false);
    const [managingWorkspaceId, setManagingWorkspaceId] = useState("");
    const [managingProjectId, setManagingProjectId] = useState("");
    const [workspaceLabelDraft, setWorkspaceLabelDraft] = useState("");
    const [projectNameDraft, setProjectNameDraft] = useState("");
    const [projectRuntimeDraft, setProjectRuntimeDraft] = useState("codex");
    const [projectScopeDraft, setProjectScopeDraft] = useState("");
    const [newProjectNameDraft, setNewProjectNameDraft] = useState("");
    const [newProjectRuntimeDraft, setNewProjectRuntimeDraft] = useState("codex");
    const [workspaceModalBusy, setWorkspaceModalBusy] = useState(false);
    // Line panel state is owned by the shared LinePanel hook — one
    // SSE subscription for Workspace and Channel views, same drop-oldest
    // fanout from workspace_line_bus.
    const [error, setError] = useState(null);
    const inputRef = useRef(null);
    const fileInputRef = useRef(null);
    const currentWorkspace = projectIndex?.workspaces?.find((workspace) => workspace.id === selectedWorkspaceId) ||
        projectIndex?.workspaces?.find((workspace) => workspace.id === workspaceGraph?.currentWorkspaceId) ||
        projectIndex?.workspaces?.[0] ||
        workspaceGraph?.workspace ||
        null;
    const currentProject = currentWorkspace?.projects?.find((project) => project.id === selectedProjectId) ||
        currentWorkspace?.projects?.find((project) => project.id === workspaceGraph?.currentProjectId) ||
        currentWorkspace?.projects?.[0] ||
        null;
    const currentSession = currentProject?.sessions?.find((session) => session.id === selectedSessionId) ||
        currentProject?.sessions?.find((session) => session.id === workspaceGraph?.currentSessionId) ||
        currentProject?.sessions?.[0] ||
        currentWorkspace?.sessions?.find((session) => session.id === selectedSessionId) ||
        currentWorkspace?.sessions?.find((session) => session.id === workspaceGraph?.currentSessionId) ||
        currentWorkspace?.sessions?.[0] ||
        null;
    const managedWorkspace = projectIndex?.workspaces?.find((workspace) => workspace.id === (managingWorkspaceId || currentWorkspace?.id)) ||
        currentWorkspace ||
        null;
    const managedProject = managedWorkspace?.projects?.find((project) => project.id === (managingProjectId || currentProject?.id)) ||
        managedWorkspace?.projects?.[0] ||
        null;
    useEffect(() => {
        if (currentWorkspace && !managingWorkspaceId) {
            setManagingWorkspaceId(currentWorkspace.id);
        }
    }, [currentWorkspace, managingWorkspaceId]);
    useEffect(() => {
        if (managedWorkspace && !managedWorkspace.projects.some((project) => project.id === managingProjectId)) {
            setManagingProjectId(managedWorkspace.projects[0]?.id || "");
        }
    }, [managedWorkspace, managingProjectId]);
    useEffect(() => {
        if (managedWorkspace) {
            setWorkspaceLabelDraft(managedWorkspace.label || managedWorkspace.name);
        }
    }, [managedWorkspace?.id]);
    useEffect(() => {
        if (currentProject) {
            setProjectNameDraft(currentProject.name);
        }
    }, [currentProject?.id]);
    useEffect(() => {
        if (managedProject) {
            setProjectNameDraft(managedProject.name);
            setProjectRuntimeDraft(managedProject.defaultRuntime === "claude-code" ? "claude-code" : "codex");
            setProjectScopeDraft((managedProject.scopeRules || [])
                .map((rule) => rule.pathPrefix)
                .filter(Boolean)
                .join("\n"));
        }
    }, [managedProject?.id]);
    const loadDetail = async (sessionId) => {
        const nextSessionId = sessionId || currentSession?.id;
        if (!nextSessionId)
            return;
        try {
            const snapshot = await api.sessionDetail(nextSessionId);
            setDetail(snapshot);
            setWorkspaceRuntime(snapshot.runtime?.runtimes || []);
            setSelectedFile(snapshot.files?.[0] || null);
            setSelectedAsset(null);
            setError(null);
        }
        catch (e) {
            setError(String(e));
        }
    };
    const { messages: lineMessages } = useWorkspaceLine(currentWorkspace?.id, currentProject?.id);
    useEffect(() => {
        void loadDetail(selectedSessionId || currentSession?.id);
    }, [selectedSessionId, currentWorkspace?.id]);
    useEffect(() => {
        const timer = window.setInterval(() => {
            void loadDetail(selectedSessionId || currentSession?.id);
        }, 5000);
        return () => window.clearInterval(timer);
    }, [selectedSessionId, currentSession?.id]);
    useEffect(() => {
        inputRef.current?.focus();
    }, [detail?.session?.id]);
    const fallbackTask = tasks.find((task) => task.id === activeTaskId) || null;
    const activeTask = detail?.task || fallbackTask || null;
    const messages = useMemo(() => {
        if (detail?.session?.messages?.length)
            return detail.session.messages;
        if (activeTask?.messages?.length)
            return activeTask.messages;
        return [];
    }, [detail?.session?.messages, activeTask]);
    const relevantRuntimes = useMemo(() => {
        if (!detail?.session?.runtime)
            return workspaceRuntime;
        return workspaceRuntime.filter((runtime) => runtime.id === detail.session.runtime);
    }, [detail?.session?.runtime, workspaceRuntime]);
    const saveNote = async () => {
        const content = input.trim();
        if (!content || !activeTask || saving)
            return;
        setSaving(true);
        try {
            await onAddTaskNote(activeTask.id, content);
            setInput("");
            await onTasksRefresh();
            await loadDetail(detail?.session?.id);
        }
        catch (e) {
            setError(String(e));
        }
        finally {
            setSaving(false);
        }
    };
    const handleAssetUpload = async (file) => {
        if (!file || !detail?.session?.id)
            return;
        setUploading(true);
        try {
            await api.uploadSessionAsset(detail.session.id, file);
            await loadDetail(detail.session.id);
        }
        catch (e) {
            setError(String(e));
        }
        finally {
            setUploading(false);
            if (fileInputRef.current)
                fileInputRef.current.value = "";
        }
    };
    const handleAssetOpen = async (assetId) => {
        try {
            const snapshot = await api.assetContext(assetId);
            setSelectedAsset(snapshot);
        }
        catch (e) {
            setError(String(e));
        }
    };
    const openWorkspaceModal = () => {
        if (currentWorkspace) {
            setManagingWorkspaceId(currentWorkspace.id);
            setWorkspaceLabelDraft(currentWorkspace.label || currentWorkspace.name);
            setManagingProjectId(currentProject?.id || currentWorkspace.projects?.[0]?.id || "");
        }
        setShowWorkspaceModal(true);
        setError(null);
    };
    const saveWorkspaceRename = async () => {
        if (!managedWorkspace || !workspaceLabelDraft.trim() || workspaceModalBusy)
            return;
        setWorkspaceModalBusy(true);
        try {
            await onUpdateWorkspace(managedWorkspace.id, workspaceLabelDraft.trim());
            setError(null);
        }
        catch (e) {
            setError(String(e));
        }
        finally {
            setWorkspaceModalBusy(false);
        }
    };
    const addMountedFolder = async () => {
        if (!managedWorkspace || workspaceModalBusy)
            return;
        setWorkspaceModalBusy(true);
        setError(null);
        try {
            const picked = await api.pickFolder("Select a folder to mount in this workspace");
            if (picked.ok && picked.path) {
                await onAddWorkspaceFolder(managedWorkspace.id, picked.path);
            }
        }
        catch (e) {
            setError(String(e));
        }
        finally {
            setWorkspaceModalBusy(false);
        }
    };
    const removeMountedFolder = async (path) => {
        if (!managedWorkspace || workspaceModalBusy)
            return;
        setWorkspaceModalBusy(true);
        setError(null);
        try {
            await onRemoveWorkspaceFolder(managedWorkspace.id, path);
        }
        catch (e) {
            setError(String(e));
        }
        finally {
            setWorkspaceModalBusy(false);
        }
    };
    const saveProjectSettings = async () => {
        if (!managedProject || workspaceModalBusy)
            return;
        const scopeRules = projectScopeDraft
            .split("\n")
            .map((line) => line.trim())
            .filter(Boolean)
            .map((pathPrefix, index) => ({
            path_prefix: pathPrefix,
            label: index === 0 ? "primary" : `scope-${index + 1}`,
        }));
        setWorkspaceModalBusy(true);
        setError(null);
        try {
            await onUpdateProject(managedProject.id, {
                name: projectNameDraft.trim() || managedProject.name,
                default_runtime: projectRuntimeDraft,
                scope_rules: scopeRules,
            });
        }
        catch (e) {
            setError(String(e));
        }
        finally {
            setWorkspaceModalBusy(false);
        }
    };
    if (!currentProject || !currentWorkspace || !currentSession) {
        return (_jsxs("div", { style: {
                height: "100%",
                display: "flex",
                alignItems: "center",
                justifyContent: "center",
                flexDirection: "column",
                gap: 12,
                fontFamily: "var(--mono)",
                fontSize: 11,
                color: "var(--ink3)",
            }, children: [_jsx("div", { style: { fontSize: 13, color: "var(--ink)" }, children: "No mirrored workspace session yet" }), _jsx("div", { children: "Launch or attach a Codex / Claude Code session to populate this workspace." }), _jsx("button", { onClick: onNotepadOpen, style: {
                        padding: "6px 14px",
                        border: "1px solid var(--border)",
                        fontFamily: "var(--mono)",
                        fontSize: 10,
                        color: "var(--ink2)",
                    }, children: "\u2190 back to notepad" })] }));
    }
    return (_jsxs("div", { style: { display: "flex", flexDirection: "column", height: "100%" }, children: [_jsxs("div", { style: {
                    borderBottom: "1px solid var(--border)",
                    padding: "0 14px",
                    height: 48,
                    display: "flex",
                    alignItems: "center",
                    gap: 10,
                    flexShrink: 0,
                }, children: [_jsxs("div", { style: { display: "flex", alignItems: "center", gap: 10, minWidth: 0, flex: 1 }, children: [_jsxs("span", { style: { fontFamily: "var(--mono)", fontSize: 10, color: "var(--ink3)" }, children: [currentWorkspace.label || currentWorkspace.name, " / ", currentProject.name] }), _jsx("span", { style: {
                                    fontSize: 14,
                                    fontWeight: 600,
                                    overflow: "hidden",
                                    textOverflow: "ellipsis",
                                    whiteSpace: "nowrap",
                                }, children: detail?.session?.title || currentSession.title }), _jsx(StatPill, { label: `${detail?.session?.runtime || currentSession.runtime}`.replace("-", " "), tone: "var(--green)" }), _jsx(StatPill, { label: detail?.session?.permissionMode || currentSession.permissionMode || "native" }), _jsx(StatPill, { label: detail?.session?.state || currentSession.state })] }), _jsx("button", { onClick: () => void loadDetail(detail?.session?.id), style: {
                            height: 48,
                            padding: "0 12px",
                            borderLeft: "1px solid var(--border)",
                            fontFamily: "var(--mono)",
                            fontSize: 9,
                            color: "var(--ink2)",
                        }, children: "REFRESH" }), _jsx("button", { onClick: onCanvasOpen, style: {
                            height: 48,
                            padding: "0 14px",
                            borderLeft: "1px solid var(--border)",
                            fontFamily: "var(--mono)",
                            fontSize: 9,
                            color: "var(--ink2)",
                        }, children: "\u229E CANVAS" }), _jsx("button", { onClick: openWorkspaceModal, style: {
                            height: 48,
                            padding: "0 14px",
                            borderLeft: "1px solid var(--border)",
                            fontFamily: "var(--mono)",
                            fontSize: 9,
                            color: "var(--ink2)",
                        }, children: "MANAGE" })] }), _jsxs("div", { style: { flex: 1, display: "flex", overflow: "hidden" }, children: [_jsxs("div", { style: {
                            width: tweaks.compactNav ? 74 : 260,
                            borderRight: "1px solid var(--border)",
                            display: "flex",
                            flexDirection: "column",
                            overflowY: "auto",
                        }, children: [_jsxs("div", { style: { padding: "12px 12px 10px", borderBottom: "1px solid var(--border)" }, children: [_jsx("div", { style: { fontFamily: "var(--mono)", fontSize: 9, color: "var(--ink3)", marginBottom: 8 }, children: "WORKSPACE" }), _jsxs("div", { style: { border: "1px solid var(--border)", background: "white", padding: 10, marginBottom: 12 }, children: [_jsx("div", { style: { fontSize: 12, fontWeight: 600, marginBottom: 4 }, children: currentWorkspace.label || currentWorkspace.name }), _jsxs("div", { style: { fontFamily: "var(--mono)", fontSize: 9, color: "var(--ink3)", lineHeight: 1.5 }, children: [(currentWorkspace.folders || currentWorkspace.mounts || []).length, " mounted folders \u00B7 ", currentWorkspace.sessionCount || currentWorkspace.sessions.length, " sessions"] }), _jsx("button", { onClick: openWorkspaceModal, style: {
                                                    marginTop: 8,
                                                    padding: "6px 10px",
                                                    border: "1px solid var(--border)",
                                                    fontFamily: "var(--mono)",
                                                    fontSize: 9,
                                                    color: "var(--accent)",
                                                }, children: "manage workspace" })] }), _jsx("div", { style: { fontFamily: "var(--mono)", fontSize: 9, color: "var(--ink3)", marginBottom: 8 }, children: "PROJECTS" }), currentWorkspace.projects.map((project) => (_jsx("div", { style: { marginBottom: 10 }, children: _jsxs("button", { onClick: () => onSelectProject(project.id, currentWorkspace.id), style: {
                                                width: "100%",
                                                textAlign: "left",
                                                padding: "8px 9px",
                                                border: `1px solid ${project.id === currentProject.id ? "var(--border2)" : "var(--border)"}`,
                                                background: project.id === currentProject.id ? "var(--surface)" : "white",
                                                fontSize: 12,
                                                fontWeight: project.id === currentProject.id ? 700 : 500,
                                                color: project.id === currentProject.id ? "var(--ink)" : "var(--ink2)",
                                            }, children: [project.name, _jsxs("div", { style: { marginTop: 4, fontFamily: "var(--mono)", fontSize: 9, color: "var(--ink3)" }, children: [project.sessions.length, " sessions \u00B7 default ", project.defaultRuntime || "codex"] })] }) }, project.id)))] }), _jsxs("div", { style: { padding: "12px" }, children: [_jsx("div", { style: { fontFamily: "var(--mono)", fontSize: 9, color: "var(--ink3)", marginBottom: 8 }, children: "SESSIONS" }), (currentProject.sessions || []).map((session) => (_jsxs("button", { onClick: () => onSelectSession(session.id, session.taskId), style: {
                                            width: "100%",
                                            textAlign: "left",
                                            padding: "9px 10px",
                                            marginBottom: 8,
                                            border: `1px solid ${detail?.session?.id === session.id ? "var(--green)" : "var(--border)"}`,
                                            background: detail?.session?.id === session.id ? "oklch(0.98 0.02 145)" : "white",
                                        }, children: [_jsx("div", { style: { fontSize: 12, fontWeight: 600, marginBottom: 4 }, children: session.title }), _jsxs("div", { style: { fontFamily: "var(--mono)", fontSize: 9, color: "var(--ink3)" }, children: [session.runtime, " \u00B7 ", session.model || "unknown model"] }), _jsx("div", { style: {
                                                    fontSize: 10.5,
                                                    color: "var(--ink3)",
                                                    marginTop: 6,
                                                    lineHeight: 1.4,
                                                }, children: session.preview || "No preview yet." })] }, session.id)))] })] }), _jsxs("div", { style: { flex: 1, display: "flex", flexDirection: "column", minWidth: 0 }, children: [_jsxs("div", { style: {
                                    padding: "12px 16px",
                                    borderBottom: "1px solid var(--border)",
                                    display: "flex",
                                    gap: 8,
                                    flexWrap: "wrap",
                                }, children: [_jsx(StatPill, { label: detail?.session?.runtime || currentSession.runtime, tone: "var(--green)" }), _jsx(StatPill, { label: detail?.session?.taskStatus || activeTask?.status || "mirrored" }), _jsx(StatPill, { label: `${detail?.session?.touchedFiles?.length || 0} files` }), _jsx(StatPill, { label: `${detail?.assets?.length || 0} assets` }), _jsx(StatPill, { label: `${detail?.results?.length || 0} shared results` })] }), _jsxs("div", { style: { flex: 1, overflowY: "auto", padding: "20px 18px 8px" }, children: [messages.length === 0 && (_jsx("div", { style: { fontFamily: "var(--mono)", fontSize: 11, color: "var(--ink3)" }, children: "No mirrored messages yet." })), messages.map((msg) => (_jsx(ChatMessage, { msg: msg, tasks: tasks, onSelectTask: onSelectTask }, msg.id))), detail?.results?.slice(0, 8).map((result) => (_jsx(ChatMessage, { msg: {
                                            id: `result:${result.id}`,
                                            role: "agent",
                                            content: `${result.tool_name}: ${result.digest || "No digest recorded."}`,
                                        }, tasks: tasks, onSelectTask: onSelectTask }, result.id))), _jsx("div", { style: { height: 1 } })] }), _jsxs("div", { style: { borderTop: "1px solid var(--border)", padding: "12px 16px" }, children: [_jsx("textarea", { ref: inputRef, value: input, onChange: (e) => setInput(e.target.value), placeholder: activeTask
                                            ? "Add a Dhee note to this session task…"
                                            : "This mirrored session has no linked Dhee task yet.", rows: 3, disabled: !activeTask, style: {
                                            width: "100%",
                                            fontFamily: "var(--font)",
                                            fontSize: 14,
                                            lineHeight: 1.5,
                                            border: "1px solid var(--border)",
                                            padding: "12px 14px",
                                            background: activeTask ? "white" : "var(--surface)",
                                        } }), _jsxs("div", { style: { marginTop: 10, display: "flex", gap: 10, alignItems: "center" }, children: [_jsx("button", { onClick: () => void saveNote(), disabled: !activeTask || saving, style: {
                                                    padding: "7px 14px",
                                                    border: "1px solid var(--ink)",
                                                    background: activeTask ? "var(--ink)" : "transparent",
                                                    color: activeTask ? "white" : "var(--ink3)",
                                                    fontFamily: "var(--mono)",
                                                    fontSize: 10,
                                                }, children: saving ? "saving…" : "save note" }), error && _jsx("span", { style: { fontSize: 11, color: "var(--rose)" }, children: error })] })] })] }), _jsxs("div", { style: {
                            width: 320,
                            borderLeft: "1px solid var(--border)",
                            overflowY: "auto",
                            padding: "14px 14px 18px",
                        }, children: [_jsxs("div", { style: { marginBottom: 18 }, children: [_jsx("div", { style: { fontFamily: "var(--mono)", fontSize: 9, color: "var(--ink3)", marginBottom: 10 }, children: "RUNTIME" }), relevantRuntimes.map((runtime) => (_jsxs("div", { style: {
                                            border: "1px solid var(--border)",
                                            background: "white",
                                            padding: 12,
                                            marginBottom: 10,
                                        }, children: [_jsxs("div", { style: {
                                                    display: "flex",
                                                    justifyContent: "space-between",
                                                    alignItems: "center",
                                                    gap: 8,
                                                    marginBottom: 6,
                                                }, children: [_jsx("div", { style: { fontSize: 12.5, fontWeight: 600 }, children: runtime.label }), _jsx(StatPill, { label: runtimeSummary(runtime), tone: runtime.installed ? "var(--green)" : "var(--rose)" })] }), _jsx("div", { style: { fontSize: 11, color: "var(--ink2)" }, children: runtime.currentSession?.title || runtime.currentSession?.cwd || "No active session" }), _jsxs("div", { style: { fontFamily: "var(--mono)", fontSize: 9, color: "var(--ink3)", marginTop: 6 }, children: ["limit: ", runtime.limits.state, runtime.limits.resetAt ? ` · reset ${fmtTime(runtime.limits.resetAt)}` : ""] })] }, runtime.id)))] }), _jsxs("div", { style: { marginBottom: 18 }, children: [_jsx("div", { style: { fontFamily: "var(--mono)", fontSize: 9, color: "var(--ink3)", marginBottom: 10 }, children: "COLLABORATION LINE" }), _jsx(LineComposer, { workspace: currentWorkspace, activeProjectId: currentProject?.id, sessionId: detail?.session?.id || currentSession?.id, taskId: activeTask?.id, onPublished: async () => {
                                            await onTasksRefresh();
                                        } }), _jsx("div", { style: { display: "grid", gap: 8, marginTop: 10 }, children: (lineMessages || []).slice(0, 8).map((message) => (_jsx(LineMessageCard, { message: message, workspace: currentWorkspace }, message.id))) })] }), _jsxs("div", { style: { marginBottom: 18 }, children: [_jsx("div", { style: { fontFamily: "var(--mono)", fontSize: 9, color: "var(--ink3)", marginBottom: 10 }, children: "FILE CONTEXT" }), detail?.files?.map((file) => (_jsxs("button", { onClick: () => setSelectedFile(file), style: {
                                            width: "100%",
                                            textAlign: "left",
                                            padding: "10px 11px",
                                            border: `1px solid ${selectedFile?.path === file.path ? "var(--indigo)" : "var(--border)"}`,
                                            background: "white",
                                            marginBottom: 8,
                                        }, children: [_jsx("div", { style: { fontSize: 12, fontWeight: 600, marginBottom: 4 }, children: file.path.split("/").pop() }), _jsxs("div", { style: { fontFamily: "var(--mono)", fontSize: 9, color: "var(--ink3)" }, children: [file.results.length, " results \u00B7 ", file.memories.length, " memories"] })] }, file.path))), selectedFile && (_jsxs("div", { style: { border: "1px solid var(--border)", background: "white", padding: 12 }, children: [_jsx("div", { style: { fontSize: 12, fontWeight: 600, marginBottom: 6 }, children: selectedFile.path }), _jsx("div", { style: { fontSize: 11, color: "var(--ink2)", lineHeight: 1.5, marginBottom: 8 }, children: selectedFile.summary || "No stored summary yet." }), selectedFile.results.slice(0, 3).map((result) => (_jsxs("div", { style: { fontFamily: "var(--mono)", fontSize: 9, color: "var(--ink3)", marginBottom: 6 }, children: [result.tool_name, ": ", String(result.digest || "").slice(0, 120)] }, result.id)))] }))] }), _jsxs("div", { children: [_jsxs("div", { style: {
                                            display: "flex",
                                            justifyContent: "space-between",
                                            alignItems: "center",
                                            marginBottom: 10,
                                        }, children: [_jsx("div", { style: { fontFamily: "var(--mono)", fontSize: 9, color: "var(--ink3)" }, children: "SESSION ASSETS" }), _jsx("button", { onClick: () => fileInputRef.current?.click(), style: {
                                                    padding: "4px 10px",
                                                    border: "1px solid var(--border)",
                                                    fontFamily: "var(--mono)",
                                                    fontSize: 9,
                                                    color: "var(--accent)",
                                                }, children: uploading ? "uploading…" : "upload" })] }), _jsx("input", { ref: fileInputRef, type: "file", hidden: true, onChange: (e) => void handleAssetUpload(e.target.files?.[0]) }), detail?.assets?.map((asset) => (_jsxs("button", { onClick: () => void handleAssetOpen(asset.id), style: {
                                            width: "100%",
                                            textAlign: "left",
                                            border: "1px solid var(--border)",
                                            background: "white",
                                            padding: 12,
                                            marginBottom: 8,
                                        }, children: [_jsx("div", { style: { fontSize: 12, fontWeight: 600, marginBottom: 4 }, children: asset.name }), _jsxs("div", { style: { fontFamily: "var(--mono)", fontSize: 9, color: "var(--ink3)" }, children: [asset.mime_type || "file", " \u00B7 ", (asset.size_bytes || 0).toLocaleString(), " bytes"] })] }, asset.id))), selectedAsset && (_jsxs("div", { style: { border: "1px solid var(--border)", background: "white", padding: 12, marginTop: 10 }, children: [_jsx("div", { style: { fontSize: 12, fontWeight: 600, marginBottom: 6 }, children: selectedAsset.asset.name }), _jsx("div", { style: { fontSize: 11, color: "var(--ink2)", lineHeight: 1.5, marginBottom: 8 }, children: selectedAsset.summary || "No extracted summary yet. Re-upload or parse this asset to make it queryable." }), (selectedAsset.chunks || []).slice(0, 3).map((chunk) => (_jsxs("div", { style: { fontFamily: "var(--mono)", fontSize: 9, color: "var(--ink3)", marginBottom: 6 }, children: ["chunk ", chunk.chunk_index, ": ", chunk.content.slice(0, 180)] }, `${selectedAsset.asset.id}:${chunk.chunk_index}`))), _jsx("textarea", { value: assetQuestion, onChange: (e) => setAssetQuestion(e.target.value), placeholder: "Ask Dhee about this asset\u2026", rows: 3, style: { width: "100%", border: "1px solid var(--border)", padding: "10px 12px", marginTop: 8, background: "var(--bg)" } }), _jsx("button", { onClick: async () => {
                                                    if (!assetQuestion.trim())
                                                        return;
                                                    try {
                                                        const result = await api.askAsset(selectedAsset.asset.id, assetQuestion.trim());
                                                        setAssetQuestion("");
                                                        if (result.launch?.session_id)
                                                            onSelectSession(result.launch.session_id, result.launch.task_id || null);
                                                    }
                                                    catch (e) {
                                                        setError(String(e));
                                                    }
                                                }, style: { marginTop: 8, padding: "8px 12px", border: "1px solid var(--ink)", background: "var(--ink)", color: "white", fontFamily: "var(--mono)", fontSize: 10 }, children: "ask with default claude code" })] }))] })] })] }), showWorkspaceModal && currentProject && managedWorkspace && (_jsx("div", { onClick: () => setShowWorkspaceModal(false), style: {
                    position: "fixed",
                    inset: 0,
                    background: "rgba(12, 12, 12, 0.22)",
                    display: "flex",
                    alignItems: "center",
                    justifyContent: "center",
                    padding: 24,
                    zIndex: 80,
                }, children: _jsxs("div", { onClick: (e) => e.stopPropagation(), style: {
                        width: "min(1040px, calc(100vw - 80px))",
                        maxHeight: "calc(100vh - 80px)",
                        background: "var(--bg)",
                        border: "1px solid var(--border2)",
                        display: "grid",
                        gridTemplateColumns: "280px minmax(0, 1fr)",
                        overflow: "hidden",
                        boxShadow: "0 30px 80px rgba(0,0,0,0.12)",
                    }, children: [_jsxs("div", { style: { borderRight: "1px solid var(--border)", background: "white", overflowY: "auto" }, children: [_jsxs("div", { style: { padding: 16, borderBottom: "1px solid var(--border)" }, children: [_jsx("div", { style: { fontFamily: "var(--mono)", fontSize: 10, color: "var(--ink3)" }, children: "WORKSPACES" }), _jsx("div", { style: { marginTop: 6, fontSize: 13, fontWeight: 600 }, children: "switch workspace" })] }), _jsx("div", { style: { padding: 12 }, children: (projectIndex?.workspaces || []).map((workspace) => (_jsxs("button", { onClick: () => {
                                            setManagingWorkspaceId(workspace.id);
                                            setManagingProjectId(workspace.projects?.[0]?.id || "");
                                        }, style: {
                                            width: "100%",
                                            textAlign: "left",
                                            padding: "10px 12px",
                                            marginBottom: 8,
                                            border: `1px solid ${workspace.id === managedWorkspace.id ? "var(--accent)" : "var(--border)"}`,
                                            background: workspace.id === managedWorkspace.id ? "rgba(224, 107, 63, 0.06)" : "white",
                                        }, children: [_jsx("div", { style: { fontSize: 12, fontWeight: 600, marginBottom: 4 }, children: workspace.label || workspace.name }), _jsxs("div", { style: { fontFamily: "var(--mono)", fontSize: 9, color: "var(--ink3)" }, children: [workspace.sessionCount || workspace.sessions.length, " sessions \u00B7 ", (workspace.folders || workspace.mounts || []).length, " folders"] })] }, workspace.id))) })] }), _jsxs("div", { style: { display: "flex", flexDirection: "column", overflow: "hidden" }, children: [_jsxs("div", { style: {
                                        padding: "16px 18px",
                                        borderBottom: "1px solid var(--border)",
                                        background: "white",
                                        display: "flex",
                                        alignItems: "center",
                                        justifyContent: "space-between",
                                        gap: 12,
                                    }, children: [_jsxs("div", { children: [_jsx("div", { style: { fontFamily: "var(--mono)", fontSize: 10, color: "var(--ink3)" }, children: "WORKSPACE SETTINGS" }), _jsx("div", { style: { marginTop: 6, fontSize: 18, fontWeight: 650 }, children: managedWorkspace.label || managedWorkspace.name })] }), _jsx("button", { onClick: () => setShowWorkspaceModal(false), style: { fontFamily: "var(--mono)", fontSize: 10, color: "var(--ink3)" }, children: "close" })] }), _jsxs("div", { style: { flex: 1, overflowY: "auto", padding: 18 }, children: [_jsxs("div", { style: { marginBottom: 24 }, children: [_jsx("div", { style: { fontFamily: "var(--mono)", fontSize: 10, color: "var(--ink3)", marginBottom: 10 }, children: "RENAME" }), _jsxs("div", { style: { display: "flex", gap: 10, alignItems: "center" }, children: [_jsx("input", { value: workspaceLabelDraft, onChange: (e) => setWorkspaceLabelDraft(e.target.value), placeholder: "Workspace name", style: {
                                                                flex: 1,
                                                                border: "1px solid var(--border)",
                                                                padding: "10px 12px",
                                                                background: "white",
                                                                fontSize: 14,
                                                            } }), _jsx("button", { onClick: () => void saveWorkspaceRename(), disabled: !workspaceLabelDraft.trim() || workspaceModalBusy, style: {
                                                                padding: "10px 12px",
                                                                border: "1px solid var(--ink)",
                                                                background: "var(--ink)",
                                                                color: "white",
                                                                fontFamily: "var(--mono)",
                                                                fontSize: 10,
                                                                opacity: !workspaceLabelDraft.trim() || workspaceModalBusy ? 0.6 : 1,
                                                            }, children: workspaceModalBusy ? "saving..." : "save name" })] })] }), _jsxs("div", { children: [_jsxs("div", { style: {
                                                        display: "flex",
                                                        justifyContent: "space-between",
                                                        alignItems: "center",
                                                        gap: 12,
                                                        marginBottom: 10,
                                                    }, children: [_jsx("div", { style: { fontFamily: "var(--mono)", fontSize: 10, color: "var(--ink3)" }, children: "MOUNTED FOLDERS" }), _jsx("button", { onClick: () => void addMountedFolder(), disabled: workspaceModalBusy, style: {
                                                                padding: "8px 10px",
                                                                border: "1px solid var(--border)",
                                                                background: "white",
                                                                fontFamily: "var(--mono)",
                                                                fontSize: 9,
                                                                color: "var(--accent)",
                                                            }, children: workspaceModalBusy ? "working..." : "add folder" })] }), _jsx("div", { style: { display: "grid", gap: 10 }, children: (managedWorkspace.folders || managedWorkspace.mounts || []).map((folder) => (_jsxs("div", { style: {
                                                            border: "1px solid var(--border)",
                                                            background: "white",
                                                            padding: 12,
                                                            display: "flex",
                                                            justifyContent: "space-between",
                                                            gap: 12,
                                                            alignItems: "flex-start",
                                                        }, children: [_jsxs("div", { style: { minWidth: 0 }, children: [_jsx("div", { style: { fontSize: 12, fontWeight: 600, marginBottom: 4 }, children: folder.primary ? "root folder" : folder.label }), _jsx("div", { style: {
                                                                            fontFamily: "var(--mono)",
                                                                            fontSize: 9,
                                                                            color: "var(--ink3)",
                                                                            lineHeight: 1.5,
                                                                            wordBreak: "break-all",
                                                                        }, children: folder.path })] }), _jsx("button", { onClick: () => void removeMountedFolder(folder.path), disabled: workspaceModalBusy || (managedWorkspace.folders || managedWorkspace.mounts || []).length <= 1, style: {
                                                                    fontFamily: "var(--mono)",
                                                                    fontSize: 9,
                                                                    color: "var(--rose)",
                                                                    opacity: workspaceModalBusy || (managedWorkspace.folders || managedWorkspace.mounts || []).length <= 1 ? 0.5 : 1,
                                                                }, children: "remove" })] }, folder.path))) }), _jsx("div", { style: { marginTop: 10, fontSize: 11, color: "var(--ink3)", lineHeight: 1.5 }, children: "Sessions are included in this workspace by matching their working directory against these mounted folders. Removing a folder removes those sessions from this workspace view without deleting the mirrored session record." })] }), _jsxs("div", { style: { marginTop: 24 }, children: [_jsx("div", { style: { fontFamily: "var(--mono)", fontSize: 10, color: "var(--ink3)", marginBottom: 10 }, children: "PROJECTS" }), _jsxs("div", { style: { display: "grid", gridTemplateColumns: "240px minmax(0, 1fr)", gap: 14 }, children: [_jsx("div", { style: { display: "grid", gap: 10, alignContent: "start" }, children: (managedWorkspace.projects || []).map((project) => (_jsxs("button", { onClick: () => setManagingProjectId(project.id), style: {
                                                                    width: "100%",
                                                                    textAlign: "left",
                                                                    border: `1px solid ${project.id === managedProject?.id ? "var(--accent)" : "var(--border)"}`,
                                                                    background: project.id === managedProject?.id ? "rgba(224, 107, 63, 0.06)" : "white",
                                                                    padding: 12,
                                                                }, children: [_jsx("div", { style: { fontSize: 12, fontWeight: 600 }, children: project.name }), _jsxs("div", { style: { fontFamily: "var(--mono)", fontSize: 9, color: "var(--ink3)", marginTop: 4 }, children: [project.sessions.length, " sessions \u00B7 default ", project.defaultRuntime || "codex"] })] }, project.id))) }), _jsxs("div", { style: { display: "grid", gap: 12, alignContent: "start" }, children: [managedProject ? (_jsxs("div", { style: { border: "1px solid var(--border)", background: "white", padding: 14 }, children: [_jsx("div", { style: { fontFamily: "var(--mono)", fontSize: 10, color: "var(--ink3)", marginBottom: 10 }, children: "EDIT PROJECT" }), _jsxs("div", { style: { display: "grid", gap: 10 }, children: [_jsx("input", { value: projectNameDraft, onChange: (e) => setProjectNameDraft(e.target.value), placeholder: "Project name", style: {
                                                                                        border: "1px solid var(--border)",
                                                                                        padding: "10px 12px",
                                                                                        background: "white",
                                                                                        fontSize: 14,
                                                                                    } }), _jsxs("select", { value: projectRuntimeDraft, onChange: (e) => setProjectRuntimeDraft(e.target.value), style: {
                                                                                        border: "1px solid var(--border)",
                                                                                        padding: "10px 12px",
                                                                                        background: "white",
                                                                                        fontSize: 14,
                                                                                    }, children: [_jsx("option", { value: "codex", children: "Codex default runtime" }), _jsx("option", { value: "claude-code", children: "Claude Code default runtime" })] }), _jsx("textarea", { value: projectScopeDraft, onChange: (e) => setProjectScopeDraft(e.target.value), rows: 5, placeholder: "One path scope rule per line", style: {
                                                                                        border: "1px solid var(--border)",
                                                                                        padding: "10px 12px",
                                                                                        background: "white",
                                                                                        fontSize: 13,
                                                                                        lineHeight: 1.5,
                                                                                    } }), _jsx("div", { style: { fontSize: 11, color: "var(--ink3)", lineHeight: 1.5 }, children: "Sessions are assigned to this project by the longest matching scope rule path." }), _jsx("button", { onClick: () => void saveProjectSettings(), disabled: workspaceModalBusy || !projectNameDraft.trim(), style: {
                                                                                        justifySelf: "start",
                                                                                        padding: "10px 12px",
                                                                                        border: "1px solid var(--ink)",
                                                                                        background: "var(--ink)",
                                                                                        color: "white",
                                                                                        fontFamily: "var(--mono)",
                                                                                        fontSize: 10,
                                                                                        opacity: workspaceModalBusy || !projectNameDraft.trim() ? 0.6 : 1,
                                                                                    }, children: workspaceModalBusy ? "saving..." : "save project" })] })] })) : null, _jsxs("div", { style: { border: "1px solid var(--border)", background: "white", padding: 14 }, children: [_jsx("div", { style: { fontFamily: "var(--mono)", fontSize: 10, color: "var(--ink3)", marginBottom: 10 }, children: "ADD PROJECT" }), _jsxs("div", { style: { display: "grid", gap: 10 }, children: [_jsx("input", { value: newProjectNameDraft, onChange: (e) => setNewProjectNameDraft(e.target.value), placeholder: "New project name", style: {
                                                                                        border: "1px solid var(--border)",
                                                                                        padding: "10px 12px",
                                                                                        background: "white",
                                                                                        fontSize: 14,
                                                                                    } }), _jsxs("select", { value: newProjectRuntimeDraft, onChange: (e) => setNewProjectRuntimeDraft(e.target.value), style: {
                                                                                        border: "1px solid var(--border)",
                                                                                        padding: "10px 12px",
                                                                                        background: "white",
                                                                                        fontSize: 14,
                                                                                    }, children: [_jsx("option", { value: "codex", children: "Codex default runtime" }), _jsx("option", { value: "claude-code", children: "Claude Code default runtime" })] }), _jsx("button", { onClick: async () => {
                                                                                        if (!managedWorkspace || !newProjectNameDraft.trim())
                                                                                            return;
                                                                                        setWorkspaceModalBusy(true);
                                                                                        try {
                                                                                            await onCreateProject(managedWorkspace.id, {
                                                                                                name: newProjectNameDraft.trim(),
                                                                                                default_runtime: newProjectRuntimeDraft,
                                                                                                scope_rules: [
                                                                                                    { path_prefix: workspaceRootPath(managedWorkspace), label: "root" },
                                                                                                ],
                                                                                            });
                                                                                            setNewProjectNameDraft("");
                                                                                        }
                                                                                        catch (e) {
                                                                                            setError(String(e));
                                                                                        }
                                                                                        finally {
                                                                                            setWorkspaceModalBusy(false);
                                                                                        }
                                                                                    }, style: {
                                                                                        justifySelf: "start",
                                                                                        padding: "10px 12px",
                                                                                        border: "1px solid var(--ink)",
                                                                                        background: "var(--ink)",
                                                                                        color: "white",
                                                                                        fontFamily: "var(--mono)",
                                                                                        fontSize: 10,
                                                                                    }, children: "add project" })] })] })] })] })] }), error && (_jsx("div", { style: { marginTop: 18, fontSize: 11, color: "var(--rose)" }, children: error }))] })] })] }) }))] }));
}
