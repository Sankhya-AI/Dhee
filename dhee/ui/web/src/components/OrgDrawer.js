import { jsx as _jsx, jsxs as _jsxs, Fragment as _Fragment } from "react/jsx-runtime";
import { useEffect, useState } from "react";
import { api } from "../api";
import { SectionHeader } from "./ui/SectionHeader";
function metaRecord(node) {
    return (node?.meta || {});
}
function sessionTaskId(node) {
    const meta = metaRecord(node);
    const taskId = String(meta.task_id || meta.taskId || "");
    return taskId || null;
}
function repoMappingsFromNode(node) {
    const rows = metaRecord(node).repo_mappings;
    return Array.isArray(rows) ? rows : [];
}
function repoMappingLabel(mapping) {
    const meta = (mapping.metadata || {});
    const label = typeof meta.label === "string" ? meta.label.trim() : "";
    const raw = label || mapping.local_path || mapping.repo_url || "folder";
    return String(raw).split("/").filter(Boolean).pop() || String(raw);
}
function runtimeColor(runtime) {
    const value = String(runtime || "").toLowerCase();
    if (value === "codex")
        return "var(--indigo)";
    if (value === "claude-code" || value === "claude")
        return "var(--accent)";
    return "var(--ink3)";
}
function uniqueMappings(rows) {
    const seen = new Set();
    const out = [];
    for (const row of rows) {
        const key = String(row.mapping_id || row.local_path || row.repo_url || "");
        if (!key || seen.has(key))
            continue;
        seen.add(key);
        out.push(row);
    }
    return out;
}
export function OrgDrawer({ node, graph, viewer, isManager, onClose, onOpenVault, onOpenSession, onChanged, }) {
    const [busy, setBusy] = useState(null);
    const [folderPath, setFolderPath] = useState("");
    const [folderLabel, setFolderLabel] = useState("");
    const [projectName, setProjectName] = useState("");
    const [teamName, setTeamName] = useState("");
    const [gitUrl, setGitUrl] = useState("");
    const [collabTeamId, setCollabTeamId] = useState("");
    const [confirmReset, setConfirmReset] = useState(false);
    const [confirmDeleteProject, setConfirmDeleteProject] = useState(false);
    useEffect(() => {
        setFolderPath("");
        setFolderLabel("");
        setProjectName("");
        setTeamName("");
        setGitUrl("");
        setCollabTeamId("");
        setConfirmReset(false);
        setConfirmDeleteProject(false);
    }, [node?.id]);
    if (!node)
        return null;
    const isWorkspace = node.type === "workspace";
    const isProject = node.type === "project";
    const isTeam = node.type === "team" || node.type === "global_team";
    const isRepo = node.type === "repo";
    const isFolder = node.type === "folder";
    const isSession = node.type === "session";
    // ─── Workspace ─────────────────────────────────────────────────────────
    const projects = isWorkspace && graph
        ? graph.edges
            .filter((e) => e.source === node.id && e.kind === "contains")
            .map((e) => graph.nodes.find((n) => n.id === e.target))
            .filter((n) => Boolean(n) && n.type === "project")
        : [];
    // ─── Project ───────────────────────────────────────────────────────────
    const projectId = isProject
        ? String(node.meta?.project_id || "")
        : "";
    const projectTeams = isProject && graph
        ? graph.edges
            .filter((e) => e.kind === "contains" && e.source === node.id)
            .map((e) => graph.nodes.find((n) => n.id === e.target))
            .filter((n) => Boolean(n) && (n.type === "team" || n.type === "global_team"))
        : [];
    // ─── Team / repo body data ─────────────────────────────────────────────
    const repoMappings = isTeam ? repoMappingsFromNode(node) : [];
    const teamMeta = metaRecord(node);
    const teamId = isTeam ? String(teamMeta.team_id || "") : "";
    const developerCount = typeof teamMeta.developer_count === "number" ? teamMeta.developer_count : 0;
    const developerJoinEvents = Array.isArray(teamMeta.developer_join_events)
        ? teamMeta.developer_join_events
        : [];
    const collaboratingTeams = Array.isArray(teamMeta.collaborating_teams)
        ? teamMeta.collaborating_teams
        : [];
    const allTeamNodes = graph
        ? graph.nodes.filter((n) => (n.type === "team" || n.type === "global_team") &&
            String(n.meta?.team_id || "") !== teamId)
        : [];
    const folderMeta = metaRecord(node);
    const selectedFolderPath = isFolder ? String(folderMeta.path || "") : "";
    const folderShared = isFolder ? Boolean(folderMeta.shared) : false;
    const folderSessions = isFolder && graph
        ? graph.edges
            .filter((e) => e.source === node.id && e.kind === "contains")
            .map((e) => graph.nodes.find((n) => n.id === e.target))
            .filter((n) => Boolean(n) && n.type === "session")
        : [];
    // ─── Actions ───────────────────────────────────────────────────────────
    const handleResetWorkspace = async () => {
        setBusy("reset");
        try {
            await api.enterpriseResetWorkspace();
            onChanged();
        }
        finally {
            setBusy(null);
        }
    };
    const handleCreateProject = async () => {
        if (!projectName.trim())
            return;
        setBusy("create-project");
        try {
            await api.enterpriseCreateProject({ name: projectName.trim() });
            setProjectName("");
            onChanged();
        }
        finally {
            setBusy(null);
        }
    };
    const handleCreateProjectTeam = async () => {
        if (!projectId || !teamName.trim())
            return;
        setBusy("create-team");
        try {
            await api.enterpriseCreateProjectTeam(projectId, { name: teamName.trim() });
            setTeamName("");
            onChanged();
        }
        finally {
            setBusy(null);
        }
    };
    const handleAddFolder = async () => {
        if (!teamId || !folderPath.trim())
            return;
        setBusy("add-folder");
        try {
            await api.enterpriseAddTeamFolder(teamId, {
                local_path: folderPath.trim(),
                label: folderLabel.trim() || undefined,
                kind: "folder",
            });
            setFolderPath("");
            setFolderLabel("");
            onChanged();
        }
        finally {
            setBusy(null);
        }
    };
    const handleAddGitRepo = async () => {
        if (!teamId || !gitUrl.trim())
            return;
        setBusy("add-git");
        try {
            await api.enterpriseAddTeamFolder(teamId, {
                repo_url: gitUrl.trim(),
                kind: "git",
            });
            setGitUrl("");
            onChanged();
        }
        finally {
            setBusy(null);
        }
    };
    const handlePickFolder = async () => {
        setBusy("pick-folder");
        try {
            const r = await api.pickFolderPath("Pick a folder for this team");
            if (r.ok && r.path)
                setFolderPath(r.path);
        }
        finally {
            setBusy(null);
        }
    };
    const handleDeleteProject = async () => {
        if (!projectId)
            return;
        setBusy("delete-project");
        try {
            await api.enterpriseDeleteProject(projectId);
            onChanged();
        }
        finally {
            setBusy(null);
        }
    };
    const handleRemoveFolder = async (mappingId) => {
        if (!mappingId)
            return;
        setBusy("remove-folder");
        try {
            await api.enterpriseRemoveFolder(mappingId);
            onChanged();
        }
        finally {
            setBusy(null);
        }
    };
    const handleAddCollaborator = async () => {
        if (!teamId || !collabTeamId.trim())
            return;
        setBusy("collaborate");
        try {
            await api.enterpriseAddTeamCollaborator(teamId, collabTeamId.trim());
            setCollabTeamId("");
            onChanged();
        }
        finally {
            setBusy(null);
        }
    };
    const handleExtractProject = async () => {
        if (!projectId)
            return;
        setBusy("extract");
        try {
            const result = await api.enterpriseExtractProject(projectId);
            onChanged();
            const summary = `AST extraction · ${result.folders_seen} folder(s) · ` +
                `${result.files_seen} files (${result.files_extracted} new, ${result.files_cached} cached) · ` +
                `${result.nodes_upserted} nodes · ${result.edges_upserted} edges`;
            // eslint-disable-next-line no-alert
            window.alert(summary);
        }
        catch (err) {
            // eslint-disable-next-line no-alert
            window.alert(`Extraction failed: ${String(err)}`);
        }
        finally {
            setBusy(null);
        }
    };
    const handleExtractTeam = async () => {
        if (!teamId)
            return;
        setBusy("extract");
        try {
            const result = await api.enterpriseExtractTeam(teamId);
            onChanged();
            const summary = `AST extraction · ${result.folders_seen} folder(s) · ` +
                `${result.files_seen} files (${result.files_extracted} new, ${result.files_cached} cached) · ` +
                `${result.nodes_upserted} nodes · ${result.edges_upserted} edges`;
            // eslint-disable-next-line no-alert
            window.alert(summary);
        }
        catch (err) {
            // eslint-disable-next-line no-alert
            window.alert(`Extraction failed: ${String(err)}`);
        }
        finally {
            setBusy(null);
        }
    };
    const handleToggleFolderShare = async () => {
        if (!selectedFolderPath)
            return;
        setBusy("share-folder");
        try {
            await api.localContextShareFolder({
                path: selectedFolderPath,
                shared: !folderShared,
            });
            onChanged();
        }
        finally {
            setBusy(null);
        }
    };
    return (_jsxs("aside", { style: {
            position: "absolute",
            top: 0,
            right: 0,
            bottom: 0,
            width: 440,
            background: "var(--bg)",
            borderLeft: "1px solid var(--border)",
            boxShadow: "-12px 0 30px rgba(20,16,10,0.06)",
            display: "flex",
            flexDirection: "column",
            zIndex: 25,
            animation: "fadein 0.18s ease",
        }, children: [_jsxs("header", { style: {
                    padding: "12px 16px",
                    borderBottom: "1px solid var(--border)",
                    display: "flex",
                    alignItems: "center",
                    justifyContent: "space-between",
                }, children: [_jsxs("div", { children: [_jsx("div", { style: {
                                    fontFamily: "var(--mono)",
                                    fontSize: 9,
                                    letterSpacing: "0.12em",
                                    color: "var(--ink3)",
                                    textTransform: "uppercase",
                                }, children: nodeKindLabel(node.type) }), _jsx("div", { style: { fontSize: 16, fontWeight: 500, color: "var(--ink)" }, children: node.label })] }), _jsx("button", { onClick: onClose, "aria-label": "Close drawer", style: {
                            width: 24,
                            height: 24,
                            borderRadius: 4,
                            background: "var(--surface)",
                            border: "1px solid var(--border)",
                            color: "var(--ink2)",
                        }, children: "\u00D7" })] }), _jsxs("div", { style: { flex: 1, overflowY: "auto", padding: 14 }, children: [isFolder ? (_jsx(FolderBody, { node: node, sessions: folderSessions, shared: folderShared, onToggleShare: handleToggleFolderShare, onOpenVault: () => onOpenVault(), onOpenSession: onOpenSession, busy: busy })) : null, isSession ? (_jsx(SessionBody, { node: node, onOpenSession: () => onOpenSession(node.id, sessionTaskId(node)) })) : null, isWorkspace ? (_jsx(WorkspaceBody, { projects: projects, projectName: projectName, onProjectName: setProjectName, onCreateProject: handleCreateProject, confirmReset: confirmReset, onAskReset: () => setConfirmReset(true), onCancelReset: () => setConfirmReset(false), onConfirmReset: handleResetWorkspace, busy: busy })) : null, isProject ? (_jsx(ProjectBody, { teams: projectTeams, teamName: teamName, onTeamName: setTeamName, onCreateTeam: handleCreateProjectTeam, confirmDelete: confirmDeleteProject, onAskDelete: () => setConfirmDeleteProject(true), onCancelDelete: () => setConfirmDeleteProject(false), onConfirmDelete: handleDeleteProject, busy: busy })) : null, isTeam ? (_jsx(TeamBody, { node: node, repoMappings: repoMappings, developerCount: developerCount, developerJoinEvents: developerJoinEvents, collaboratingTeams: collaboratingTeams, collaboratorOptions: allTeamNodes, collabTeamId: collabTeamId, onCollabTeamId: setCollabTeamId, onAddCollaborator: handleAddCollaborator, folderPath: folderPath, folderLabel: folderLabel, gitUrl: gitUrl, onFolderPath: setFolderPath, onFolderLabel: setFolderLabel, onGitUrl: setGitUrl, onPickFolder: handlePickFolder, onAddFolder: handleAddFolder, onAddGit: handleAddGitRepo, onExtract: handleExtractTeam, onRemoveFolder: handleRemoveFolder, onOpenVault: () => onOpenVault(String(node.meta?.team_id || "")), isManager: isManager, viewer: viewer, busy: busy })) : null, isRepo ? (_jsx(RepoBody, { node: node, onRemove: () => handleRemoveFolder(String(node.meta?.mapping_id || "")), busy: busy })) : null] })] }));
}
function nodeKindLabel(t) {
    if (t === "global_team")
        return "GLOBAL TEAM";
    if (t === "folder")
        return "LOCAL FOLDER";
    if (t === "session")
        return "AGENT SESSION";
    return t.toUpperCase();
}
function FolderBody({ node, sessions, shared, onToggleShare, onOpenVault, onOpenSession, busy, }) {
    const meta = metaRecord(node);
    const path = String(meta.path || "");
    const activeSessions = Number(meta.active_session_count || 0);
    const manager = typeof meta.context_manager === "object" && meta.context_manager
        ? meta.context_manager
        : null;
    return (_jsxs("div", { style: { display: "flex", flexDirection: "column", gap: 14 }, children: [_jsx("button", { onClick: onOpenVault, style: primaryBtnFilled(false), children: "OPEN CONTEXT \u2192" }), _jsxs("div", { children: [_jsx(SectionHeader, { children: "Folder" }), _jsxs("div", { style: {
                            marginTop: 6,
                            padding: "8px 10px",
                            border: "1px solid var(--border)",
                            borderRadius: 4,
                            background: "var(--surface)",
                            display: "grid",
                            gap: 4,
                        }, children: [_jsx(KeyValue, { label: "path", value: path || node.label, mono: true }), _jsx(KeyValue, { label: "sessions", value: `${sessions.length}` }), _jsx(KeyValue, { label: "active", value: `${activeSessions}` })] })] }), _jsxs("div", { children: [_jsx(SectionHeader, { children: "Context manager" }), _jsxs("div", { style: {
                            marginTop: 6,
                            padding: "8px 10px",
                            border: "1px solid var(--border)",
                            borderRadius: 4,
                            background: "var(--surface)",
                            display: "grid",
                            gap: 4,
                        }, children: [_jsx(KeyValue, { label: "owner", value: String(manager?.display_name || `${node.label} Context Manager`) }), _jsx(KeyValue, { label: "scope", value: String(manager?.folder_path || path || node.label), mono: true })] })] }), _jsxs("div", { children: [_jsx(SectionHeader, { children: "Context sharing" }), _jsx("button", { onClick: onToggleShare, disabled: busy === "share-folder", style: shared ? primaryBtnFilled(busy === "share-folder") : primaryBtn(busy === "share-folder"), children: busy === "share-folder"
                            ? "UPDATING..."
                            : shared
                                ? "SHARING ENABLED"
                                : "SHARE THIS FOLDER" }), _jsx(Hint, { children: "Shared folders exchange local context with the other folders you enable here." })] }), _jsxs("div", { children: [_jsxs(SectionHeader, { children: ["Agent sessions (", sessions.length, ")"] }), sessions.length === 0 ? (_jsx(Hint, { children: "No Claude Code or Codex sessions detected for this folder yet." })) : (_jsx("div", { style: { display: "grid", gap: 4, marginTop: 6 }, children: sessions.map((session) => {
                            const smeta = metaRecord(session);
                            const color = runtimeColor(smeta.runtime);
                            return (_jsxs("div", { style: {
                                    padding: "7px 10px",
                                    border: "1px solid var(--border)",
                                    borderLeft: `3px solid ${color}`,
                                    borderRadius: 4,
                                    background: "var(--surface)",
                                    display: "grid",
                                    gridTemplateColumns: "minmax(0, 1fr) auto",
                                    gap: 8,
                                    alignItems: "center",
                                }, children: [_jsxs("div", { style: { minWidth: 0 }, children: [_jsx("div", { style: {
                                                    fontSize: 12,
                                                    color: "var(--ink)",
                                                    overflow: "hidden",
                                                    textOverflow: "ellipsis",
                                                    whiteSpace: "nowrap",
                                                }, title: session.label, children: session.label }), _jsxs("div", { style: { fontFamily: "var(--mono)", fontSize: 10, color }, children: [String(smeta.runtime || "agent"), " \u00B7 ", String(smeta.state || "recent")] })] }), _jsx("button", { onClick: () => onOpenSession(session.id, sessionTaskId(session)), style: smallActionBtn(color), children: "OPEN" })] }, session.id));
                        }) }))] })] }));
}
function SessionBody({ node, onOpenSession, }) {
    const meta = metaRecord(node);
    return (_jsxs("div", { style: { display: "flex", flexDirection: "column", gap: 14 }, children: [_jsx("button", { onClick: onOpenSession, style: primaryBtnFilled(false), children: "OPEN SESSION TASK \u2192" }), _jsxs("div", { children: [_jsx(SectionHeader, { children: "Session" }), _jsxs("div", { style: {
                            marginTop: 6,
                            padding: "8px 10px",
                            border: "1px solid var(--border)",
                            borderRadius: 4,
                            background: "var(--surface)",
                            display: "grid",
                            gap: 4,
                        }, children: [_jsx(KeyValue, { label: "runtime", value: String(meta.runtime || "agent") }), _jsx(KeyValue, { label: "state", value: String(meta.state || "recent") }), meta.model ? _jsx(KeyValue, { label: "model", value: String(meta.model) }) : null, meta.cwd ? _jsx(KeyValue, { label: "folder", value: String(meta.cwd), mono: true }) : null, meta.updated_at ? _jsx(KeyValue, { label: "updated", value: String(meta.updated_at) }) : null] })] }), meta.preview ? (_jsxs("div", { children: [_jsx(SectionHeader, { children: "Preview" }), _jsx("div", { style: {
                            marginTop: 6,
                            padding: "8px 10px",
                            border: "1px solid var(--border)",
                            borderRadius: 4,
                            background: "var(--surface)",
                            color: "var(--ink2)",
                            fontSize: 12,
                            lineHeight: 1.5,
                            whiteSpace: "pre-wrap",
                        }, children: String(meta.preview) })] })) : null] }));
}
// ─── Workspace ─────────────────────────────────────────────────────────────
function WorkspaceBody({ projects, projectName, onProjectName, onCreateProject, confirmReset, onAskReset, onCancelReset, onConfirmReset, busy, }) {
    return (_jsxs("div", { style: { display: "flex", flexDirection: "column", gap: 16 }, children: [_jsxs("div", { children: [_jsx(SectionHeader, { children: "Add a project" }), _jsxs("div", { style: { display: "flex", gap: 6, marginTop: 8 }, children: [_jsx("input", { value: projectName, onChange: (e) => onProjectName(e.target.value), placeholder: "e.g. Text_to_Speech", onKeyDown: (e) => {
                                    if (e.key === "Enter")
                                        onCreateProject();
                                }, style: inputStyle }), _jsx("button", { onClick: onCreateProject, disabled: busy === "create-project" || !projectName.trim(), style: primaryBtn(busy === "create-project"), children: "CREATE" })] })] }), _jsxs("div", { children: [_jsxs(SectionHeader, { children: ["Projects (", projects.length, ")"] }), projects.length === 0 ? (_jsx(Hint, { children: "No projects yet. Add one above." })) : (_jsx("div", { style: { display: "flex", flexDirection: "column", gap: 4, marginTop: 8 }, children: projects.map((p) => (_jsxs("div", { style: {
                                padding: "6px 10px",
                                border: "1px solid var(--border)",
                                borderRadius: 4,
                                background: "var(--surface)",
                                display: "flex",
                                alignItems: "center",
                                justifyContent: "space-between",
                                fontSize: 12,
                                color: "var(--ink)",
                            }, children: [_jsx("span", { children: p.label }), _jsx(Pill, { label: "open", tone: "default" })] }, p.id))) }))] }), _jsxs("div", { style: {
                    marginTop: 8,
                    paddingTop: 14,
                    borderTop: "1px solid var(--border)",
                }, children: [_jsx(SectionHeader, { children: "Danger zone" }), !confirmReset ? (_jsx("button", { onClick: onAskReset, style: dangerBtn, children: "RESET WORKSPACE" })) : (_jsxs("div", { style: {
                            marginTop: 8,
                            padding: 10,
                            border: "1px solid var(--rose)",
                            background: "var(--rose-dim)",
                            borderRadius: 4,
                            fontSize: 12,
                            color: "var(--ink)",
                        }, children: [_jsx("div", { style: { marginBottom: 8 }, children: "This deletes projects, teams, folders, context items, proposals, and findings for this org. Memory engrams in the Dhee tier are not affected. Continue?" }), _jsxs("div", { style: { display: "flex", gap: 6 }, children: [_jsx("button", { onClick: onConfirmReset, disabled: busy === "reset", style: dangerBtn, children: busy === "reset" ? "RESETTING…" : "YES, RESET" }), _jsx("button", { onClick: onCancelReset, style: ghostBtn, children: "CANCEL" })] })] }))] })] }));
}
// ─── Project ───────────────────────────────────────────────────────────────
function ProjectBody({ teams, teamName, onTeamName, onCreateTeam, confirmDelete, onAskDelete, onCancelDelete, onConfirmDelete, busy, }) {
    return (_jsxs("div", { style: { display: "flex", flexDirection: "column", gap: 16 }, children: [_jsxs("div", { children: [_jsx(SectionHeader, { children: "Add a team" }), _jsxs("div", { style: { display: "flex", gap: 6, marginTop: 8 }, children: [_jsx("input", { value: teamName, onChange: (e) => onTeamName(e.target.value), placeholder: "Backend, Frontend, Data, Mobile", onKeyDown: (e) => {
                                    if (e.key === "Enter")
                                        onCreateTeam();
                                }, style: inputStyle }), _jsx("button", { onClick: onCreateTeam, disabled: busy === "create-team" || !teamName.trim(), style: primaryBtn(busy === "create-team"), children: "ADD" })] })] }), _jsxs("div", { children: [_jsxs(SectionHeader, { children: ["Teams (", teams.length, ")"] }), teams.length === 0 ? (_jsx(Hint, { children: "No teams yet." })) : (_jsx("div", { style: { display: "flex", flexDirection: "column", gap: 4, marginTop: 8 }, children: teams.map((team) => {
                            const mappings = uniqueMappings(repoMappingsFromNode(team));
                            return (_jsxs("div", { style: {
                                    padding: "6px 10px",
                                    border: "1px solid var(--border)",
                                    borderRadius: 4,
                                    background: "var(--surface)",
                                    display: "flex",
                                    alignItems: "center",
                                    justifyContent: "space-between",
                                    gap: 8,
                                }, children: [_jsx("span", { style: { color: "var(--ink)", fontSize: 12 }, children: team.label }), _jsx(Pill, { label: `${mappings.length} ${mappings.length === 1 ? "repo" : "repos"}`, tone: mappings.length ? "green" : "default" })] }, team.id));
                        }) }))] }), _jsx("div", { style: {
                    marginTop: 8,
                    paddingTop: 14,
                    borderTop: "1px solid var(--border)",
                }, children: !confirmDelete ? (_jsx("button", { onClick: onAskDelete, style: dangerBtn, children: "DELETE PROJECT" })) : (_jsxs("div", { style: {
                        marginTop: 4,
                        padding: 10,
                        border: "1px solid var(--rose)",
                        background: "var(--rose-dim)",
                        borderRadius: 4,
                        fontSize: 12,
                        color: "var(--ink)",
                    }, children: [_jsx("div", { style: { marginBottom: 8 }, children: "Deletes the project and all its teams + context. Continue?" }), _jsxs("div", { style: { display: "flex", gap: 6 }, children: [_jsx("button", { onClick: onConfirmDelete, disabled: busy === "delete-project", style: dangerBtn, children: busy === "delete-project" ? "DELETING…" : "YES, DELETE" }), _jsx("button", { onClick: onCancelDelete, style: ghostBtn, children: "CANCEL" })] })] })) })] }));
}
function TeamBody({ node, repoMappings, developerCount, developerJoinEvents, collaboratingTeams, collaboratorOptions, collabTeamId, onCollabTeamId, onAddCollaborator, folderPath, folderLabel, gitUrl, onFolderPath, onFolderLabel, onGitUrl, onPickFolder, onAddFolder, onAddGit, onExtract, onRemoveFolder, onOpenVault, busy, }) {
    const meta = metaRecord(node);
    const manager = meta.context_manager;
    const teamId = String(meta.team_id || "");
    const projectId = String(meta.project_id || "");
    const mappings = uniqueMappings(repoMappings);
    return (_jsxs("div", { style: { display: "flex", flexDirection: "column", gap: 14 }, children: [_jsx("button", { onClick: onOpenVault, style: primaryBtnFilled(false), children: "OPEN CONTEXT \u2192" }), _jsxs("div", { children: [_jsx(SectionHeader, { children: "Team details" }), _jsxs("div", { style: {
                            marginTop: 6,
                            display: "grid",
                            gap: 4,
                            padding: "8px 10px",
                            border: "1px solid var(--border)",
                            borderRadius: 4,
                            background: "var(--surface)",
                            fontSize: 12,
                        }, children: [_jsx(KeyValue, { label: "team", value: teamId || node.label }), projectId ? _jsx(KeyValue, { label: "project", value: projectId }) : null, _jsx(KeyValue, { label: "git access", value: `${developerCount} dev${developerCount === 1 ? "" : "s"} joined` })] })] }), _jsxs("div", { children: [_jsx(SectionHeader, { children: "Manager" }), _jsx("div", { style: {
                            marginTop: 6,
                            padding: "8px 10px",
                            border: "1px solid var(--border)",
                            borderRadius: 4,
                            background: "var(--surface)",
                            fontSize: 12,
                        }, children: manager?.display_name ? (_jsxs(_Fragment, { children: [_jsx("div", { children: manager.display_name }), _jsx("div", { style: { fontSize: 10, color: "var(--ink3)" }, children: manager.manager_id })] })) : (_jsx("span", { style: { color: "var(--ink3)" }, children: "no manager assigned" })) })] }), _jsxs("div", { children: [_jsx(SectionHeader, { children: "Add a local folder" }), _jsxs("div", { style: { display: "flex", gap: 6, marginTop: 8 }, children: [_jsx("input", { value: folderPath, onChange: (e) => onFolderPath(e.target.value), placeholder: "/Users/me/code/backend", onKeyDown: (e) => {
                                    if (e.key === "Enter")
                                        onAddFolder();
                                }, style: inputStyle }), _jsx("button", { onClick: onPickFolder, disabled: busy === "pick-folder", style: ghostBtn, title: "Browse", children: "BROWSE" })] }), _jsxs("div", { style: { display: "flex", gap: 6, marginTop: 6 }, children: [_jsx("input", { value: folderLabel, onChange: (e) => onFolderLabel(e.target.value), placeholder: "Optional label", style: inputStyle }), _jsx("button", { onClick: onAddFolder, disabled: busy === "add-folder" || !folderPath.trim(), style: primaryBtn(busy === "add-folder"), children: "ADD" })] })] }), _jsxs("div", { children: [_jsx(SectionHeader, { children: "Add a git repo" }), _jsxs("div", { style: { display: "flex", gap: 6, marginTop: 8 }, children: [_jsx("input", { value: gitUrl, onChange: (e) => onGitUrl(e.target.value), placeholder: "git@github.com:org/backend.git", onKeyDown: (e) => {
                                    if (e.key === "Enter")
                                        onAddGit();
                                }, style: inputStyle }), _jsx("button", { onClick: onAddGit, disabled: busy === "add-git" || !gitUrl.trim(), style: primaryBtn(busy === "add-git"), children: "ADD" })] })] }), _jsxs("div", { children: [_jsxs("div", { style: {
                            display: "flex",
                            alignItems: "center",
                            justifyContent: "space-between",
                            gap: 8,
                        }, children: [_jsxs(SectionHeader, { children: ["Git + local folders (", mappings.length, ")"] }), _jsx("button", { onClick: onExtract, disabled: busy === "extract" || mappings.length === 0, title: "Run AST extraction for this team's local folders", style: primaryBtn(busy === "extract"), children: busy === "extract" ? "INDEXING..." : "INDEX TEAM" })] }), mappings.length === 0 ? (_jsx(Hint, { children: "None mapped to this team." })) : (_jsx("div", { style: { display: "flex", flexDirection: "column", gap: 4, marginTop: 6 }, children: mappings.map((mapping) => {
                            const key = String(mapping.mapping_id || mapping.local_path || mapping.repo_url);
                            return (_jsxs("div", { style: {
                                    padding: "8px 10px",
                                    border: "1px solid var(--border)",
                                    borderRadius: 4,
                                    background: "var(--surface)",
                                    display: "grid",
                                    gap: 4,
                                }, children: [_jsxs("div", { style: {
                                            display: "flex",
                                            alignItems: "center",
                                            justifyContent: "space-between",
                                            gap: 8,
                                        }, children: [_jsx("div", { style: { fontSize: 12, color: "var(--ink)" }, children: repoMappingLabel(mapping) }), _jsx("button", { onClick: () => onRemoveFolder(mapping.mapping_id), style: iconBtn, title: "Remove mapping", "aria-label": "Remove mapping", children: "\u00D7" })] }), mapping.repo_url ? (_jsx(KeyValue, { label: "repo", value: String(mapping.repo_url), mono: true })) : null, mapping.local_path ? (_jsx(KeyValue, { label: "folder", value: String(mapping.local_path), mono: true })) : null] }, key));
                        }) }))] }), developerJoinEvents.length ? (_jsxs("div", { children: [_jsx(SectionHeader, { children: "Recent joins" }), _jsx("div", { style: { display: "grid", gap: 4, marginTop: 6 }, children: developerJoinEvents.slice(0, 4).map((event, idx) => (_jsxs("div", { style: {
                                padding: "6px 10px",
                                border: "1px solid var(--border)",
                                borderRadius: 4,
                                background: "var(--surface)",
                                fontSize: 11,
                                color: "var(--ink2)",
                            }, children: [_jsx("div", { style: { fontFamily: "var(--mono)", overflow: "hidden", textOverflow: "ellipsis" }, children: event.repo_root || "workspace" }), _jsxs("div", { style: { color: "var(--ink3)", marginTop: 2 }, children: [event.role || "developer", " - ", event.received_at || "recent"] })] }, `${event.repo_root || "join"}-${idx}`))) })] })) : null, _jsxs("div", { children: [_jsx(SectionHeader, { children: "Collaborate teams" }), collaboratingTeams.length === 0 ? (_jsx(Hint, { children: "No team context shares yet." })) : (_jsx("div", { style: { display: "flex", flexWrap: "wrap", gap: 6, marginTop: 6 }, children: collaboratingTeams.map((team) => (_jsx(Pill, { label: String(team.name || team.team_id), tone: "default" }, String(team.team_id || team.name)))) })), _jsxs("div", { style: { display: "flex", gap: 6, marginTop: 8 }, children: [_jsxs("select", { value: collabTeamId, onChange: (e) => onCollabTeamId(e.target.value), style: inputStyle, children: [_jsx("option", { value: "", children: "Select team" }), collaboratorOptions.map((team) => {
                                        const optionTeamId = String(team.meta?.team_id || "");
                                        return (_jsx("option", { value: optionTeamId, children: team.label }, team.id));
                                    })] }), _jsx("button", { onClick: onAddCollaborator, disabled: busy === "collaborate" || !collabTeamId, style: primaryBtn(busy === "collaborate"), children: "ADD" })] })] })] }));
}
function RepoBody({ node, onRemove, busy, }) {
    const meta = node.meta || {};
    return (_jsxs("div", { style: { display: "flex", flexDirection: "column", gap: 12 }, children: [_jsx(SectionHeader, { children: "Folder / path" }), _jsx("div", { style: {
                    padding: "8px 10px",
                    border: "1px solid var(--border)",
                    borderRadius: 4,
                    background: "var(--surface)",
                    fontFamily: "var(--mono)",
                    fontSize: 11,
                    color: "var(--ink2)",
                    wordBreak: "break-all",
                }, children: meta.local_path || meta.repo_url || node.label }), _jsx("button", { onClick: onRemove, disabled: busy === "remove-folder", style: dangerBtn, children: busy === "remove-folder" ? "REMOVING…" : "REMOVE" })] }));
}
// ─── Atoms ─────────────────────────────────────────────────────────────────
function KeyValue({ label, value, mono = false, }) {
    return (_jsxs("div", { style: {
            display: "grid",
            gridTemplateColumns: "82px minmax(0, 1fr)",
            gap: 8,
            alignItems: "baseline",
        }, children: [_jsx("span", { style: {
                    fontFamily: "var(--mono)",
                    fontSize: 10,
                    color: "var(--ink3)",
                    textTransform: "uppercase",
                }, children: label }), _jsx("span", { title: value, style: {
                    minWidth: 0,
                    overflow: "hidden",
                    textOverflow: "ellipsis",
                    whiteSpace: "nowrap",
                    fontFamily: mono ? "var(--mono)" : undefined,
                    fontSize: mono ? 10 : 12,
                    color: "var(--ink2)",
                }, children: value })] }));
}
function Hint({ children }) {
    return (_jsx("div", { style: { fontSize: 11, color: "var(--ink3)", marginTop: 6 }, children: children }));
}
function Pill({ label, tone = "default", }) {
    const map = {
        default: { bg: "var(--surface)", fg: "var(--ink2)" },
        green: { bg: "var(--green-dim)", fg: "var(--green)" },
        indigo: { bg: "var(--indigo-dim)", fg: "var(--indigo)" },
        rose: { bg: "var(--rose-dim)", fg: "var(--rose)" },
        accent: { bg: "var(--accent-dim)", fg: "var(--accent)" },
    };
    const c = map[tone];
    return (_jsx("span", { style: {
            display: "inline-flex",
            padding: "2px 7px",
            borderRadius: 3,
            background: c.bg,
            color: c.fg,
            fontFamily: "var(--mono)",
            fontSize: 9,
            letterSpacing: "0.04em",
        }, children: label }));
}
const inputStyle = {
    flex: 1,
    fontFamily: "var(--mono)",
    fontSize: 11,
    padding: "6px 8px",
    background: "var(--surface)",
    border: "1px solid var(--border)",
    borderRadius: 3,
    color: "var(--ink)",
};
function primaryBtn(busy) {
    return {
        fontFamily: "var(--mono)",
        fontSize: 10,
        padding: "5px 12px",
        background: busy ? "var(--surface)" : "var(--accent-dim)",
        color: "var(--accent)",
        border: "1px solid var(--accent)",
        borderRadius: 3,
        cursor: busy ? "wait" : "pointer",
    };
}
function primaryBtnFilled(busy) {
    return {
        fontFamily: "var(--mono)",
        fontSize: 11,
        padding: "8px 12px",
        background: busy ? "var(--surface)" : "var(--accent-dim)",
        color: "var(--accent)",
        border: "1px solid var(--accent)",
        borderRadius: 4,
        textAlign: "center",
        cursor: busy ? "wait" : "pointer",
    };
}
function smallActionBtn(color) {
    return {
        fontFamily: "var(--mono)",
        fontSize: 9,
        padding: "5px 8px",
        background: "white",
        color,
        border: `1px solid ${color}`,
        borderRadius: 3,
        cursor: "pointer",
        whiteSpace: "nowrap",
    };
}
const ghostBtn = {
    fontFamily: "var(--mono)",
    fontSize: 10,
    padding: "5px 10px",
    background: "var(--surface)",
    color: "var(--ink2)",
    border: "1px solid var(--border)",
    borderRadius: 3,
};
const dangerBtn = {
    fontFamily: "var(--mono)",
    fontSize: 10,
    padding: "5px 12px",
    background: "var(--rose-dim)",
    color: "var(--rose)",
    border: "1px solid var(--rose)",
    borderRadius: 3,
};
const iconBtn = {
    width: 22,
    height: 22,
    borderRadius: 3,
    background: "var(--surface)",
    color: "var(--ink2)",
    border: "1px solid var(--border)",
    fontSize: 12,
    lineHeight: 1,
};
