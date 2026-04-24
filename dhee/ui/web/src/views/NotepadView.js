import { jsxs as _jsxs, jsx as _jsx, Fragment as _Fragment } from "react/jsx-runtime";
import { useEffect, useMemo, useRef, useState } from "react";
import { api } from "../api";
export function NotepadView({ projectIndex, memories, tokensSaved, onAddMemory, onSelectSession, onCreateWorkspace, onLaunchSession, onOpenWorkspace, onOpenTasks, }) {
    const [draft, setDraft] = useState("");
    const [mode, setMode] = useState("task");
    const [runtime, setRuntime] = useState("codex");
    const [permissionMode, setPermissionMode] = useState("standard");
    const [selectedWorkspaceId, setSelectedWorkspaceId] = useState("");
    const [selectedProjectId, setSelectedProjectId] = useState("");
    const [showWorkspaceModal, setShowWorkspaceModal] = useState(false);
    const [workspaceName, setWorkspaceName] = useState("");
    const [workspaceFolder, setWorkspaceFolder] = useState("");
    const [busy, setBusy] = useState(false);
    const [folderBusy, setFolderBusy] = useState(false);
    const [error, setError] = useState(null);
    const inputRef = useRef(null);
    useEffect(() => {
        inputRef.current?.focus();
    }, []);
    const workspaces = projectIndex?.workspaces || [];
    const currentWorkspace = useMemo(() => workspaces.find((workspace) => workspace.id === (selectedWorkspaceId || projectIndex?.currentWorkspaceId)) ||
        workspaces[0] ||
        null, [projectIndex?.currentWorkspaceId, selectedWorkspaceId, workspaces]);
    const currentProject = useMemo(() => currentWorkspace?.projects?.find((project) => project.id === (selectedProjectId || projectIndex?.currentProjectId)) ||
        currentWorkspace?.projects?.[0] ||
        null, [currentWorkspace, projectIndex?.currentProjectId, selectedProjectId]);
    const currentSession = useMemo(() => currentProject?.sessions?.find((session) => session.id === projectIndex?.currentSessionId) ||
        currentProject?.sessions?.[0] ||
        currentWorkspace?.sessions?.find((session) => session.id === projectIndex?.currentSessionId) ||
        currentWorkspace?.sessions?.[0] ||
        null, [currentProject, currentWorkspace, projectIndex?.currentSessionId]);
    useEffect(() => {
        if (!selectedWorkspaceId && projectIndex?.currentWorkspaceId) {
            setSelectedWorkspaceId(projectIndex.currentWorkspaceId);
        }
    }, [projectIndex?.currentWorkspaceId, selectedWorkspaceId]);
    useEffect(() => {
        if (!selectedProjectId && projectIndex?.currentProjectId) {
            setSelectedProjectId(projectIndex.currentProjectId);
        }
    }, [projectIndex?.currentProjectId, selectedProjectId]);
    useEffect(() => {
        if (currentWorkspace && currentProject && !currentWorkspace.projects.some((project) => project.id === selectedProjectId)) {
            setSelectedProjectId(currentProject.id);
        }
    }, [currentWorkspace, currentProject, selectedProjectId]);
    const noteLines = draft
        .split("\n")
        .map((line) => line.trim())
        .filter(Boolean);
    const saveNotes = async () => {
        if (!noteLines.length || busy)
            return;
        setBusy(true);
        setError(null);
        try {
            if (mode === "memory") {
                for (const line of noteLines) {
                    await onAddMemory(line);
                }
            }
            else {
                for (const line of noteLines) {
                    await onLaunchSession(line, runtime, currentWorkspace?.id, runtime === "claude-code" ? permissionMode : undefined, currentProject?.id);
                }
            }
            setDraft("");
        }
        catch (e) {
            setError(String(e));
        }
        finally {
            setBusy(false);
        }
    };
    const pickFolder = async () => {
        setFolderBusy(true);
        setError(null);
        try {
            const res = await api.pickFolder("Select a workspace folder");
            if (res.ok && res.path) {
                setWorkspaceFolder(res.path);
                if (!workspaceName.trim()) {
                    const parts = res.path.replace(/\/$/, "").split("/");
                    setWorkspaceName(parts[parts.length - 1] || "Workspace");
                }
            }
        }
        catch (e) {
            setError(String(e));
        }
        finally {
            setFolderBusy(false);
        }
    };
    const createWorkspace = async () => {
        if (!workspaceFolder.trim() || !workspaceName.trim() || busy)
            return;
        setBusy(true);
        setError(null);
        try {
            await onCreateWorkspace(workspaceName.trim(), workspaceFolder.trim());
            setWorkspaceFolder("");
            setWorkspaceName("");
            setShowWorkspaceModal(false);
        }
        catch (e) {
            setError(String(e));
        }
        finally {
            setBusy(false);
        }
    };
    const chipButton = (active) => ({
        padding: "6px 10px",
        border: "1px solid var(--border)",
        background: active ? "var(--ink)" : "white",
        color: active ? "white" : "var(--ink2)",
        fontFamily: "var(--mono)",
        fontSize: 9,
    });
    return (_jsxs("div", { style: { height: "100%", display: "flex", flexDirection: "column", overflow: "hidden" }, children: [_jsxs("div", { style: {
                    height: 48,
                    borderBottom: "1px solid var(--border)",
                    padding: "0 24px",
                    display: "flex",
                    alignItems: "center",
                    justifyContent: "space-between",
                    flexShrink: 0,
                }, children: [_jsx("div", { style: { display: "flex", gap: 10, alignItems: "center", minWidth: 0 }, children: _jsxs("span", { style: { fontFamily: "var(--mono)", fontSize: 10, color: "var(--ink3)" }, children: [currentWorkspace?.label || currentWorkspace?.name || "workspace", currentProject ? ` / ${currentProject.name}` : ""] }) }), _jsxs("div", { style: { display: "flex", gap: 18, alignItems: "center", fontFamily: "var(--mono)", fontSize: 10 }, children: [_jsx("button", { onClick: onOpenTasks, style: { color: "var(--ink3)" }, children: "tasks" }), _jsxs("span", { style: { color: "var(--ink3)" }, children: [memories, " engrams"] }), _jsxs("span", { style: { color: "var(--accent)", fontWeight: 700 }, children: [tokensSaved.toLocaleString(), " tokens saved"] })] })] }), _jsxs("div", { style: {
                    flex: 1,
                    overflow: "auto",
                    padding: 28,
                    display: "grid",
                    gridTemplateColumns: "minmax(0, 1fr) 280px",
                    gap: 24,
                }, children: [_jsxs("div", { style: {
                            border: "1px solid var(--border)",
                            background: "transparent",
                            padding: 24,
                            display: "flex",
                            flexDirection: "column",
                            minHeight: 0,
                        }, children: [_jsxs("div", { style: {
                                    display: "flex",
                                    justifyContent: "space-between",
                                    gap: 12,
                                    alignItems: "center",
                                    marginBottom: 16,
                                    flexWrap: "wrap",
                                }, children: [_jsxs("div", { style: { display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }, children: [_jsx("button", { onClick: () => setMode("task"), style: chipButton(mode === "task"), children: "create task" }), _jsx("button", { onClick: () => setMode("memory"), style: chipButton(mode === "memory"), children: "save memory" }), mode === "task" && (_jsxs(_Fragment, { children: [_jsx("button", { onClick: () => setRuntime("codex"), style: chipButton(runtime === "codex"), children: "codex" }), _jsx("button", { onClick: () => setRuntime("claude-code"), style: chipButton(runtime === "claude-code"), children: "claude-code" }), runtime === "claude-code" && (_jsxs(_Fragment, { children: [_jsx("button", { onClick: () => setPermissionMode("standard"), style: chipButton(permissionMode === "standard"), children: "standard" }), _jsx("button", { onClick: () => setPermissionMode("full-access"), style: chipButton(permissionMode === "full-access"), children: "full access" })] }))] }))] }), _jsx("button", { onClick: onOpenWorkspace, style: {
                                            padding: "7px 10px",
                                            border: "1px solid var(--border)",
                                            fontFamily: "var(--mono)",
                                            fontSize: 9,
                                            color: "var(--ink3)",
                                        }, children: "open workspace" })] }), mode === "task" && (_jsxs("div", { style: { display: "grid", gap: 10, marginBottom: 14 }, children: [_jsxs("div", { style: { display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }, children: [_jsx("span", { style: { fontFamily: "var(--mono)", fontSize: 9, color: "var(--ink3)" }, children: "workspace" }), _jsx("select", { value: currentWorkspace?.id || "", onChange: (e) => setSelectedWorkspaceId(e.target.value), style: {
                                                    border: "1px solid var(--border)",
                                                    padding: "7px 10px",
                                                    background: "white",
                                                    minWidth: 220,
                                                }, children: workspaces.map((workspace) => (_jsx("option", { value: workspace.id, children: workspace.label || workspace.name }, workspace.id))) })] }), currentWorkspace?.projects?.length ? (_jsxs("div", { style: { display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }, children: [_jsx("span", { style: { fontFamily: "var(--mono)", fontSize: 9, color: "var(--ink3)" }, children: "project" }), currentWorkspace.projects.map((project) => (_jsx("button", { onClick: () => setSelectedProjectId(project.id), style: {
                                                    ...chipButton(project.id === currentProject?.id),
                                                    background: project.id === currentProject?.id ? "var(--accent)" : "white",
                                                }, children: project.name }, project.id)))] })) : null] })), _jsx("textarea", { ref: inputRef, value: draft, onChange: (e) => setDraft(e.target.value), onKeyDown: (e) => {
                                    if ((e.metaKey || e.ctrlKey) && e.key === "Enter") {
                                        e.preventDefault();
                                        void saveNotes();
                                    }
                                }, placeholder: mode === "task"
                                    ? "- broadcast backend contract changes\n- compare project scope rules\n- create follow-up task for frontend stream"
                                    : "- backend project now emits model version updates\n- paper asset is chunked and queryable\n- avoid reprocessing shared results", style: {
                                    width: "100%",
                                    flex: 1,
                                    border: "1px solid var(--border)",
                                    padding: "18px 20px",
                                    fontSize: 22,
                                    lineHeight: 1.55,
                                    background: "white",
                                    resize: "none",
                                    minHeight: 420,
                                } }), _jsxs("div", { style: { display: "flex", justifyContent: "space-between", alignItems: "center", marginTop: 14 }, children: [_jsxs("div", { style: { fontFamily: "var(--mono)", fontSize: 10, color: "var(--ink3)" }, children: [noteLines.length, " point", noteLines.length === 1 ? "" : "s"] }), _jsx("button", { onClick: () => void saveNotes(), style: {
                                            padding: "10px 16px",
                                            border: "1px solid var(--ink)",
                                            background: "var(--ink)",
                                            color: "white",
                                            fontFamily: "var(--mono)",
                                            fontSize: 10,
                                        }, children: busy ? "saving…" : mode === "task" ? "create task" : "save memory" })] }), error && _jsx("div", { style: { marginTop: 12, fontSize: 11, color: "var(--rose)" }, children: error })] }), _jsxs("div", { style: { display: "flex", flexDirection: "column", gap: 12 }, children: [_jsxs("div", { onClick: onOpenWorkspace, style: {
                                    border: "1px solid var(--border)",
                                    background: "white",
                                    padding: 16,
                                    cursor: "pointer",
                                }, children: [_jsx("div", { style: { marginBottom: 8, fontFamily: "var(--mono)", fontSize: 10, color: "var(--ink3)" }, children: "CURRENT WORKSPACE" }), _jsx("div", { style: { fontSize: 16, fontWeight: 600 }, children: currentWorkspace?.label || currentWorkspace?.name || "No workspace" }), _jsx("div", { style: { marginTop: 6, fontFamily: "var(--mono)", fontSize: 9, color: "var(--ink3)" }, children: currentWorkspace?.workspacePath || "Select or create a workspace" }), currentProject && (_jsxs("div", { style: { marginTop: 10, fontFamily: "var(--mono)", fontSize: 9, color: "var(--ink3)" }, children: [currentProject.name, " \u00B7 default ", currentProject.defaultRuntime || "codex"] })), currentSession && (_jsx("button", { onClick: (e) => {
                                            e.stopPropagation();
                                            onSelectSession(currentSession.id, currentSession.taskId || null);
                                        }, style: {
                                            marginTop: 12,
                                            padding: "7px 10px",
                                            border: "1px solid var(--border)",
                                            fontFamily: "var(--mono)",
                                            fontSize: 9,
                                            color: "var(--ink3)",
                                        }, children: "open current session" }))] }), _jsx("button", { onClick: () => setShowWorkspaceModal(true), style: {
                                    padding: "12px 14px",
                                    border: "1px solid var(--border)",
                                    background: "white",
                                    fontFamily: "var(--mono)",
                                    fontSize: 10,
                                    textAlign: "left",
                                }, children: "+ add workspace" })] })] }), showWorkspaceModal && (_jsx("div", { style: {
                    position: "fixed",
                    inset: 0,
                    background: "rgba(0,0,0,0.18)",
                    display: "flex",
                    alignItems: "center",
                    justifyContent: "center",
                    zIndex: 40,
                }, onClick: () => setShowWorkspaceModal(false), children: _jsxs("div", { onClick: (e) => e.stopPropagation(), style: {
                        width: 460,
                        maxWidth: "calc(100vw - 32px)",
                        border: "1px solid var(--border)",
                        background: "white",
                        padding: 20,
                    }, children: [_jsx("div", { style: { marginBottom: 14, fontFamily: "var(--mono)", fontSize: 10, color: "var(--ink3)" }, children: "ADD WORKSPACE" }), _jsxs("div", { style: { display: "grid", gap: 10 }, children: [_jsx("input", { value: workspaceName, onChange: (e) => setWorkspaceName(e.target.value), placeholder: "Workspace name", style: { border: "1px solid var(--border)", padding: "11px 12px", background: "white" } }), _jsx("button", { onClick: () => void pickFolder(), style: {
                                        padding: "11px 12px",
                                        border: "1px solid var(--border)",
                                        background: "white",
                                        fontFamily: "var(--mono)",
                                        fontSize: 10,
                                        textAlign: "left",
                                    }, children: folderBusy ? "opening folder dialog…" : workspaceFolder || "select folder" }), _jsxs("div", { style: { display: "flex", justifyContent: "flex-end", gap: 8, marginTop: 4 }, children: [_jsx("button", { onClick: () => setShowWorkspaceModal(false), style: {
                                                padding: "10px 12px",
                                                border: "1px solid var(--border)",
                                                background: "white",
                                                fontFamily: "var(--mono)",
                                                fontSize: 10,
                                            }, children: "cancel" }), _jsx("button", { onClick: () => void createWorkspace(), style: {
                                                padding: "10px 12px",
                                                border: "1px solid var(--ink)",
                                                background: "var(--ink)",
                                                color: "white",
                                                fontFamily: "var(--mono)",
                                                fontSize: 10,
                                            }, children: busy ? "creating…" : "create workspace" })] })] })] }) }))] }));
}
