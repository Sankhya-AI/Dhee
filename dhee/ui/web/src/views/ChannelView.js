import { jsxs as _jsxs, jsx as _jsx } from "react/jsx-runtime";
import { useEffect, useMemo, useState } from "react";
import { AssetDrawer } from "../components/AssetDrawer";
import { ConnectedAgents, LineComposer, LineMessageCard, useWorkspaceLine, } from "../components/LinePanel";
import { StatPill } from "../components/ui/StatPill";
// ---------------------------------------------------------------------------
// ChannelView — the landing page and the *entire product surface* per the
// pitch deck: a single shared information line that every agent in the
// workspace broadcasts into and reads from.
//
// Layout (three columns on wide viewports, stack on narrow):
//   - Left: workspace + project tree, connected-agents block
//   - Center: live SSE stream of line messages, newest first
//   - Right: composer with target-project picker, suggested-task list
// ---------------------------------------------------------------------------
function countSuggestedTasks(workspace, tasks) {
    if (!workspace)
        return [];
    const workspaceProjectIds = new Set(workspace.projects.map((project) => project.id));
    return tasks.filter((task) => {
        const source = String(task.source || "").toLowerCase();
        if (source !== "broadcast" && !source.includes("suggested"))
            return false;
        const projectId = task.project_id;
        return !projectId || workspaceProjectIds.has(String(projectId));
    });
}
export function ChannelView({ projectIndex, workspaceGraph, tasks, selectedWorkspaceId, selectedProjectId, onSelectWorkspace, onSelectProject, onSelectTask, onTasksRefresh, onOpenCanvas, onLaunchSession, onOpenManager, }) {
    const workspaces = projectIndex?.workspaces || [];
    // Local state for kind filters so operators can scope the feed.
    const [kindFilter, setKindFilter] = useState("all");
    const currentWorkspace = useMemo(() => {
        return (workspaces.find((workspace) => workspace.id === selectedWorkspaceId) ||
            workspaces.find((workspace) => workspace.id === projectIndex?.currentWorkspaceId) ||
            workspaces[0] ||
            workspaceGraph?.workspace ||
            null);
    }, [workspaces, selectedWorkspaceId, projectIndex?.currentWorkspaceId, workspaceGraph]);
    const currentProject = useMemo(() => {
        if (!currentWorkspace)
            return null;
        return (currentWorkspace.projects.find((project) => project.id === selectedProjectId) ||
            currentWorkspace.projects.find((project) => project.id === projectIndex?.currentProjectId) ||
            null);
    }, [currentWorkspace, selectedProjectId, projectIndex?.currentProjectId]);
    const workspaceSessions = currentWorkspace?.sessions || [];
    const activeSession = useMemo(() => {
        const currentId = projectIndex?.currentSessionId;
        if (!currentWorkspace)
            return null;
        const inProject = currentProject?.sessions?.find((session) => session.id === currentId) ||
            currentProject?.sessions?.[0];
        if (inProject)
            return inProject;
        return (workspaceSessions.find((session) => session.id === currentId) || workspaceSessions[0] || null);
    }, [currentWorkspace, currentProject, projectIndex?.currentSessionId, workspaceSessions]);
    const { messages, live, error, refresh } = useWorkspaceLine(currentWorkspace?.id, currentProject?.id);
    // Apply the kind filter after merge so user-facing counts are honest.
    const filtered = useMemo(() => {
        if (kindFilter === "all")
            return messages;
        return messages.filter((message) => {
            const kind = String(message.message_kind || "").toLowerCase();
            if (kindFilter === "broadcast")
                return kind === "broadcast";
            if (kindFilter === "tool")
                return kind.startsWith("tool.");
            if (kindFilter === "note")
                return kind === "note" || kind === "update";
            return true;
        });
    }, [messages, kindFilter]);
    const suggestedTasks = useMemo(() => countSuggestedTasks(currentWorkspace, tasks), [currentWorkspace, tasks]);
    // Auto-scroll the feed to top when a new message arrives (new messages
    // are at the head of the sorted list).
    const [latestId, setLatestId] = useState(null);
    useEffect(() => {
        const head = messages[0]?.id;
        if (head && head !== latestId)
            setLatestId(head);
    }, [messages, latestId]);
    const chipButton = (active) => ({
        padding: "5px 10px",
        border: `1px solid ${active ? "var(--ink)" : "var(--border)"}`,
        background: active ? "var(--ink)" : "white",
        color: active ? "white" : "var(--ink2)",
        fontFamily: "var(--mono)",
        fontSize: 9,
        letterSpacing: 0.5,
        textTransform: "uppercase",
        cursor: "pointer",
    });
    const navChipStyle = (active) => ({
        width: "100%",
        textAlign: "left",
        padding: "9px 10px",
        border: `1px solid ${active ? "var(--accent)" : "var(--border)"}`,
        background: active ? "var(--surface)" : "white",
        fontFamily: active ? "var(--sans)" : "var(--mono)",
        fontSize: active ? 12 : 11,
        color: "var(--ink)",
        cursor: "pointer",
        display: "flex",
        justifyContent: "space-between",
        alignItems: "center",
        gap: 8,
    });
    return (_jsxs("div", { style: { height: "100%", display: "flex", flexDirection: "column", overflow: "hidden" }, children: [_jsxs("div", { style: {
                    height: 48,
                    borderBottom: "1px solid var(--border)",
                    padding: "0 20px",
                    display: "flex",
                    alignItems: "center",
                    justifyContent: "space-between",
                    flexShrink: 0,
                }, children: [_jsxs("div", { style: { display: "flex", alignItems: "center", gap: 10, minWidth: 0 }, children: [_jsxs("span", { style: { fontFamily: "var(--mono)", fontSize: 10, color: "var(--ink3)" }, children: [currentWorkspace?.label || currentWorkspace?.name || "channel", currentProject ? ` / ${currentProject.name}` : ""] }), _jsx(StatPill, { label: live ? "live" : "offline", tone: live ? "var(--green)" : "var(--ink3)" }), _jsxs("span", { style: { fontFamily: "var(--mono)", fontSize: 10, color: "var(--ink3)" }, children: [filtered.length, " events \u00B7 ", suggestedTasks.length, " suggested tasks"] })] }), _jsxs("div", { style: { display: "flex", gap: 8, alignItems: "center" }, children: [_jsx("button", { onClick: onOpenCanvas, style: {
                                    padding: "6px 12px",
                                    border: "1px solid var(--border)",
                                    fontFamily: "var(--mono)",
                                    fontSize: 9,
                                    color: "var(--ink2)",
                                    background: "white",
                                    cursor: "pointer",
                                    letterSpacing: 0.4,
                                }, children: "open canvas" }), _jsx("button", { onClick: () => void refresh(), style: {
                                    padding: "6px 12px",
                                    border: "1px solid var(--border)",
                                    fontFamily: "var(--mono)",
                                    fontSize: 9,
                                    color: "var(--ink2)",
                                    background: "white",
                                    cursor: "pointer",
                                    letterSpacing: 0.4,
                                }, children: "refresh" })] })] }), _jsxs("div", { style: {
                    flex: 1,
                    display: "grid",
                    gridTemplateColumns: "260px minmax(0, 1fr) 360px",
                    overflow: "hidden",
                }, children: [_jsxs("div", { style: {
                            borderRight: "1px solid var(--border)",
                            padding: 16,
                            overflowY: "auto",
                            display: "flex",
                            flexDirection: "column",
                            gap: 14,
                        }, children: [_jsxs("div", { children: [_jsxs("div", { style: {
                                            display: "flex",
                                            justifyContent: "space-between",
                                            alignItems: "center",
                                            marginBottom: 8,
                                            gap: 8,
                                        }, children: [_jsx("span", { style: {
                                                    fontFamily: "var(--mono)",
                                                    fontSize: 9,
                                                    letterSpacing: 0.6,
                                                    color: "var(--ink3)",
                                                    textTransform: "uppercase",
                                                }, children: "Workspace" }), onOpenManager ? (_jsx("button", { onClick: () => onOpenManager("workspaces"), title: "Manage workspaces", style: {
                                                    padding: "3px 7px",
                                                    border: "1px solid var(--border)",
                                                    background: "white",
                                                    fontFamily: "var(--mono)",
                                                    fontSize: 9,
                                                    letterSpacing: 0.4,
                                                    color: "var(--ink3)",
                                                    cursor: "pointer",
                                                }, children: "+ new / manage" })) : null] }), workspaces.length === 0 ? (_jsx("button", { onClick: () => onOpenManager?.("workspaces"), style: {
                                            width: "100%",
                                            padding: "10px 12px",
                                            border: "1px dashed var(--border)",
                                            background: "white",
                                            fontFamily: "var(--mono)",
                                            fontSize: 10,
                                            color: "var(--ink2)",
                                            textAlign: "left",
                                            cursor: "pointer",
                                            lineHeight: 1.5,
                                        }, children: "Create your first workspace \u2192 e.g. Office, Personal, Sankhya AI Labs." })) : (_jsx("select", { value: currentWorkspace?.id || "", onChange: (e) => onSelectWorkspace(e.target.value), style: {
                                            width: "100%",
                                            padding: "9px 10px",
                                            border: "1px solid var(--border)",
                                            background: "white",
                                            fontSize: 12,
                                        }, children: workspaces.map((workspace) => (_jsx("option", { value: workspace.id, children: workspace.label || workspace.name }, workspace.id))) }))] }), _jsxs("div", { children: [_jsxs("div", { style: {
                                            display: "flex",
                                            justifyContent: "space-between",
                                            alignItems: "center",
                                            marginBottom: 8,
                                            gap: 8,
                                        }, children: [_jsxs("span", { style: {
                                                    fontFamily: "var(--mono)",
                                                    fontSize: 9,
                                                    letterSpacing: 0.6,
                                                    color: "var(--ink3)",
                                                    textTransform: "uppercase",
                                                }, children: ["Projects \u00B7 ", currentWorkspace?.projects?.length || 0] }), onOpenManager && currentWorkspace ? (_jsx("button", { onClick: () => onOpenManager("projects"), title: "Add or edit projects", style: {
                                                    padding: "3px 7px",
                                                    border: "1px solid var(--border)",
                                                    background: "white",
                                                    fontFamily: "var(--mono)",
                                                    fontSize: 9,
                                                    letterSpacing: 0.4,
                                                    color: "var(--ink3)",
                                                    cursor: "pointer",
                                                }, children: "+ project" })) : null] }), _jsxs("div", { style: { display: "grid", gap: 6 }, children: [_jsxs("button", { onClick: () => onSelectProject("", currentWorkspace?.id), style: navChipStyle(!currentProject), children: [_jsx("span", { children: "All projects (workspace line)" }), _jsx("span", { style: { fontFamily: "var(--mono)", fontSize: 9, color: "var(--ink3)" }, children: workspaceSessions.length })] }), (currentWorkspace?.projects || []).map((project) => {
                                                const active = project.id === currentProject?.id;
                                                const sessionCount = project.sessions?.length || 0;
                                                return (_jsxs("button", { onClick: () => onSelectProject(project.id, currentWorkspace?.id), style: navChipStyle(active), children: [_jsx("span", { children: project.name }), _jsx("span", { style: { fontFamily: "var(--mono)", fontSize: 9, color: "var(--ink3)" }, children: sessionCount })] }, project.id));
                                            })] })] }), _jsx(ConnectedAgents, { workspace: currentWorkspace, projects: currentWorkspace?.projects || [], workspaceSessions: workspaceSessions }), currentWorkspace && (_jsxs("div", { style: {
                                    display: "flex",
                                    flexDirection: "column",
                                    gap: 6,
                                }, children: [_jsx("span", { style: {
                                            fontFamily: "var(--mono)",
                                            fontSize: 9,
                                            letterSpacing: 0.6,
                                            color: "var(--ink3)",
                                            textTransform: "uppercase",
                                        }, children: "Launch" }), _jsxs("div", { style: { display: "flex", gap: 6, flexWrap: "wrap" }, children: [_jsx("button", { onClick: () => void onLaunchSession("channel session", "codex", currentWorkspace.id, undefined, currentProject?.id), style: chipButton(false), children: "+ codex" }), _jsx("button", { onClick: () => void onLaunchSession("channel session", "claude-code", currentWorkspace.id, "standard", currentProject?.id), style: chipButton(false), children: "+ claude" })] })] }))] }), _jsxs("div", { style: {
                            display: "flex",
                            flexDirection: "column",
                            overflow: "hidden",
                            background: "var(--bg)",
                        }, children: [_jsxs("div", { style: {
                                    padding: "12px 20px",
                                    borderBottom: "1px solid var(--border)",
                                    display: "flex",
                                    alignItems: "center",
                                    gap: 8,
                                    flexWrap: "wrap",
                                }, children: [_jsx("span", { style: {
                                            fontFamily: "var(--mono)",
                                            fontSize: 9,
                                            letterSpacing: 0.6,
                                            color: "var(--ink3)",
                                            textTransform: "uppercase",
                                        }, children: "Shared line" }), _jsx("button", { onClick: () => setKindFilter("all"), style: chipButton(kindFilter === "all"), children: "all" }), _jsx("button", { onClick: () => setKindFilter("broadcast"), style: chipButton(kindFilter === "broadcast"), children: "broadcasts" }), _jsx("button", { onClick: () => setKindFilter("tool"), style: chipButton(kindFilter === "tool"), children: "tool events" }), _jsx("button", { onClick: () => setKindFilter("note"), style: chipButton(kindFilter === "note"), children: "notes" }), _jsx("span", { style: { flex: 1 } }), error ? (_jsx("span", { style: { fontFamily: "var(--mono)", fontSize: 9, color: "var(--rose)" }, children: error })) : null] }), _jsx("div", { style: {
                                    flex: 1,
                                    overflowY: "auto",
                                    padding: 20,
                                    display: "flex",
                                    flexDirection: "column",
                                    gap: 10,
                                }, children: filtered.length === 0 ? (_jsxs("div", { style: {
                                        padding: 24,
                                        border: "1px dashed var(--border)",
                                        background: "white",
                                        textAlign: "center",
                                    }, children: [_jsx("div", { style: { fontSize: 13, fontWeight: 600, marginBottom: 6 }, children: "The line is quiet." }), _jsx("div", { style: {
                                                fontFamily: "var(--mono)",
                                                fontSize: 10,
                                                color: "var(--ink3)",
                                                lineHeight: 1.55,
                                            }, children: "Every agent tool-call in this workspace will appear here. Launch a session from the left rail, or broadcast a note from the composer to get started." })] })) : (filtered.map((message) => (_jsx(LineMessageCard, { message: message, workspace: currentWorkspace, onOpenTask: onSelectTask }, message.id)))) })] }), _jsxs("div", { style: {
                            borderLeft: "1px solid var(--border)",
                            padding: 16,
                            overflowY: "auto",
                            display: "flex",
                            flexDirection: "column",
                            gap: 14,
                        }, children: [_jsx("div", { style: {
                                    fontFamily: "var(--mono)",
                                    fontSize: 9,
                                    letterSpacing: 0.6,
                                    color: "var(--ink3)",
                                    textTransform: "uppercase",
                                }, children: "Broadcast" }), _jsx(LineComposer, { workspace: currentWorkspace, activeProjectId: currentProject?.id, sessionId: activeSession?.id, onPublished: async () => {
                                    await onTasksRefresh();
                                    void refresh();
                                } }), _jsx(AssetDrawer, { workspace: currentWorkspace, project: currentProject, onActivity: () => void refresh() }), _jsxs("div", { style: {
                                    fontFamily: "var(--mono)",
                                    fontSize: 9,
                                    letterSpacing: 0.6,
                                    color: "var(--ink3)",
                                    textTransform: "uppercase",
                                    marginTop: 2,
                                }, children: ["Suggested tasks \u00B7 ", suggestedTasks.length] }), _jsx("div", { style: { display: "grid", gap: 8 }, children: suggestedTasks.length === 0 ? (_jsx("div", { style: {
                                        padding: 12,
                                        border: "1px dashed var(--border)",
                                        fontFamily: "var(--mono)",
                                        fontSize: 10,
                                        color: "var(--ink3)",
                                        lineHeight: 1.55,
                                        background: "white",
                                    }, children: "When an agent broadcasts to another project, a task is auto-created there. It will show up here." })) : (suggestedTasks.slice(0, 10).map((task) => (_jsxs("button", { onClick: () => onSelectTask(task.id), style: {
                                        textAlign: "left",
                                        padding: "10px 12px",
                                        border: "1px solid var(--border)",
                                        background: "white",
                                        cursor: "pointer",
                                        display: "flex",
                                        flexDirection: "column",
                                        gap: 6,
                                    }, children: [_jsx("div", { style: { fontSize: 12, fontWeight: 600, lineHeight: 1.35 }, children: task.title }), _jsxs("div", { style: { display: "flex", gap: 6, flexWrap: "wrap" }, children: [_jsx(StatPill, { label: task.status || "active", tone: "var(--accent)" }), task.harness ? _jsx(StatPill, { label: String(task.harness) }) : null] })] }, task.id)))) })] })] })] }));
}
