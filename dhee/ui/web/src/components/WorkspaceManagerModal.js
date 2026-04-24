import { jsx as _jsx, jsxs as _jsxs, Fragment as _Fragment } from "react/jsx-runtime";
import { useEffect, useState } from "react";
import { api } from "../api";
const overlayStyle = {
    position: "fixed",
    inset: 0,
    background: "rgba(20,16,10,0.28)",
    backdropFilter: "blur(4px)",
    display: "flex",
    alignItems: "center",
    justifyContent: "center",
    zIndex: 60,
};
const cardStyle = {
    width: 640,
    maxWidth: "calc(100vw - 32px)",
    maxHeight: "calc(100vh - 60px)",
    background: "white",
    border: "1px solid var(--border)",
    borderRadius: 10,
    boxShadow: "0 20px 60px rgba(20,16,10,0.20)",
    display: "flex",
    flexDirection: "column",
    overflow: "hidden",
};
const inputStyle = {
    width: "100%",
    border: "1px solid var(--border)",
    padding: "9px 11px",
    background: "var(--bg)",
    fontSize: 13,
    lineHeight: 1.4,
};
const labelStyle = {
    fontFamily: "var(--mono)",
    fontSize: 9,
    color: "var(--ink3)",
    letterSpacing: 0.5,
    textTransform: "uppercase",
    marginBottom: 4,
    display: "block",
};
const buttonPrimary = {
    padding: "8px 14px",
    border: "1px solid var(--ink)",
    background: "var(--ink)",
    color: "white",
    fontFamily: "var(--mono)",
    fontSize: 10,
    letterSpacing: 0.4,
    cursor: "pointer",
};
const buttonGhost = {
    padding: "8px 14px",
    border: "1px solid var(--border)",
    background: "white",
    color: "var(--ink2)",
    fontFamily: "var(--mono)",
    fontSize: 10,
    letterSpacing: 0.4,
    cursor: "pointer",
};
const buttonDanger = {
    padding: "8px 14px",
    border: "1px solid var(--rose)",
    background: "white",
    color: "var(--rose)",
    fontFamily: "var(--mono)",
    fontSize: 10,
    letterSpacing: 0.4,
    cursor: "pointer",
};
const runtimeOptions = ["codex", "claude-code"];
export function WorkspaceManagerModal({ open, onClose, projectIndex, initialWorkspaceId, initialTab = "workspaces", onChanged, }) {
    const workspaces = projectIndex?.workspaces || [];
    const [tab, setTab] = useState(initialTab);
    const [selectedWorkspaceId, setSelectedWorkspaceId] = useState(initialWorkspaceId || workspaces[0]?.id || "");
    const currentWorkspace = workspaces.find((workspace) => workspace.id === selectedWorkspaceId) || null;
    // Create-workspace state
    const [newWsName, setNewWsName] = useState("");
    const [newWsRoot, setNewWsRoot] = useState("");
    const [newWsDesc, setNewWsDesc] = useState("");
    // Edit-workspace state
    const [editWsName, setEditWsName] = useState("");
    const [editWsDesc, setEditWsDesc] = useState("");
    const [editWsRoot, setEditWsRoot] = useState("");
    const [deleteConfirm, setDeleteConfirm] = useState("");
    // Create-project state
    const [newProjectName, setNewProjectName] = useState("");
    const [newProjectDesc, setNewProjectDesc] = useState("");
    const [newProjectRuntime, setNewProjectRuntime] = useState("codex");
    // Edit-project state (keyed by project id so edits don't leak across selections)
    const [projectEdits, setProjectEdits] = useState({});
    const [busy, setBusy] = useState(false);
    const [error, setError] = useState(null);
    const [notice, setNotice] = useState(null);
    useEffect(() => {
        if (!open)
            return;
        setTab(initialTab);
        setSelectedWorkspaceId(initialWorkspaceId || workspaces[0]?.id || "");
        setError(null);
        setNotice(null);
        setNewWsName("");
        setNewWsRoot("");
        setNewWsDesc("");
        setNewProjectName("");
        setNewProjectDesc("");
        setNewProjectRuntime("codex");
        setDeleteConfirm("");
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [open]);
    useEffect(() => {
        if (!currentWorkspace) {
            setEditWsName("");
            setEditWsDesc("");
            setEditWsRoot("");
            return;
        }
        setEditWsName(String(currentWorkspace.label || currentWorkspace.name || ""));
        setEditWsDesc(String(currentWorkspace.description || ""));
        const mounts = currentWorkspace.mounts || currentWorkspace.folders || [];
        const primary = mounts.find((m) => m.primary) || mounts[0];
        setEditWsRoot(String(primary?.path || currentWorkspace.workspacePath || currentWorkspace.rootPath || ""));
        setDeleteConfirm("");
    }, [currentWorkspace?.id, currentWorkspace?.label, currentWorkspace?.description]);
    // Keep project edit drafts in sync with the live snapshot.
    useEffect(() => {
        if (!currentWorkspace)
            return;
        setProjectEdits((prev) => {
            const next = {};
            for (const project of currentWorkspace.projects || []) {
                next[project.id] = prev[project.id] || {
                    name: project.name,
                    description: project.description || "",
                    defaultRuntime: project.defaultRuntime || "codex",
                };
            }
            return next;
        });
    }, [currentWorkspace?.id, currentWorkspace?.projects?.length]);
    if (!open)
        return null;
    const pickFolder = async (onPicked) => {
        setError(null);
        try {
            const res = await api.pickFolder("Choose a workspace root");
            if (res.ok && res.path)
                onPicked(res.path);
        }
        catch (e) {
            setError(String(e));
        }
    };
    const runGuarded = async (label, fn) => {
        setBusy(true);
        setError(null);
        setNotice(null);
        try {
            await fn();
            setNotice(label);
            await onChanged();
        }
        catch (e) {
            setError(String(e));
        }
        finally {
            setBusy(false);
        }
    };
    const onCreateWorkspace = () => runGuarded("Workspace created.", async () => {
        const name = newWsName.trim();
        const root = newWsRoot.trim();
        if (!name || !root) {
            throw new Error("Name and root folder are required.");
        }
        await api.createWorkspaceRoot(name, root, newWsDesc.trim() || undefined);
        setNewWsName("");
        setNewWsRoot("");
        setNewWsDesc("");
    });
    const onUpdateWorkspace = () => runGuarded("Workspace updated.", async () => {
        if (!currentWorkspace)
            return;
        const payload = {};
        if (editWsName.trim() && editWsName !== (currentWorkspace.label || currentWorkspace.name)) {
            payload.label = editWsName.trim();
        }
        if (editWsDesc !== (currentWorkspace.description || "")) {
            payload.description = editWsDesc;
        }
        const currentRoot = String(currentWorkspace.workspacePath || currentWorkspace.rootPath || "");
        if (editWsRoot.trim() && editWsRoot.trim() !== currentRoot) {
            payload.root_path = editWsRoot.trim();
        }
        if (!Object.keys(payload).length) {
            throw new Error("Nothing to save.");
        }
        await api.updateWorkspace(currentWorkspace.id, payload);
    });
    const onDeleteWorkspace = () => runGuarded("Workspace deleted.", async () => {
        if (!currentWorkspace)
            return;
        if (deleteConfirm.trim() !== (currentWorkspace.label || currentWorkspace.name)) {
            throw new Error("Type the workspace name exactly to confirm.");
        }
        await api.deleteWorkspace(currentWorkspace.id);
        setSelectedWorkspaceId("");
        setDeleteConfirm("");
    });
    const onCreateProject = () => runGuarded("Project created.", async () => {
        if (!currentWorkspace)
            throw new Error("Pick a workspace first.");
        const name = newProjectName.trim();
        if (!name)
            throw new Error("Project name is required.");
        await api.createProject(currentWorkspace.id, {
            name,
            description: newProjectDesc.trim() || undefined,
            default_runtime: newProjectRuntime,
        });
        setNewProjectName("");
        setNewProjectDesc("");
        setNewProjectRuntime("codex");
    });
    const onUpdateProject = (project) => () => runGuarded("Project updated.", async () => {
        const draft = projectEdits[project.id];
        if (!draft)
            return;
        const payload = {};
        if (draft.name.trim() && draft.name.trim() !== project.name)
            payload.name = draft.name.trim();
        if (draft.description !== (project.description || ""))
            payload.description = draft.description;
        if (draft.defaultRuntime && draft.defaultRuntime !== (project.defaultRuntime || "codex")) {
            payload.default_runtime = draft.defaultRuntime;
        }
        if (!Object.keys(payload).length)
            throw new Error("Nothing to save.");
        await api.updateProject(project.id, payload);
    });
    const onDeleteProject = (project) => () => runGuarded("Project deleted.", async () => {
        const ok = window.confirm(`Delete project "${project.name}"? This removes its assets. The workspace stays.`);
        if (!ok)
            throw new Error("cancelled");
        await api.deleteProject(project.id);
    });
    return (_jsx("div", { style: overlayStyle, onClick: onClose, children: _jsxs("div", { style: cardStyle, onClick: (e) => e.stopPropagation(), children: [_jsxs("div", { style: {
                        padding: "14px 20px",
                        borderBottom: "1px solid var(--border)",
                        display: "flex",
                        alignItems: "center",
                        justifyContent: "space-between",
                    }, children: [_jsxs("div", { children: [_jsx("div", { style: { fontSize: 15, fontWeight: 700 }, children: "Workspaces & projects" }), _jsx("div", { style: { fontFamily: "var(--mono)", fontSize: 9, color: "var(--ink3)", marginTop: 2 }, children: "Organise every agent under a shared brain." })] }), _jsx("button", { onClick: onClose, style: {
                                width: 28,
                                height: 28,
                                border: "1px solid var(--border)",
                                borderRadius: 4,
                                background: "white",
                                cursor: "pointer",
                                display: "flex",
                                alignItems: "center",
                                justifyContent: "center",
                                color: "var(--ink3)",
                            }, children: _jsx("svg", { width: 12, height: 12, viewBox: "0 0 24 24", fill: "none", children: _jsx("path", { d: "M6 6l12 12M18 6L6 18", stroke: "currentColor", strokeWidth: 2, strokeLinecap: "round" }) }) })] }), _jsx("div", { style: {
                        padding: "10px 20px",
                        borderBottom: "1px solid var(--border)",
                        display: "flex",
                        gap: 6,
                    }, children: ["workspaces", "projects"].map((key) => (_jsx("button", { onClick: () => setTab(key), style: {
                            padding: "6px 12px",
                            border: `1px solid ${tab === key ? "var(--ink)" : "var(--border)"}`,
                            background: tab === key ? "var(--ink)" : "white",
                            color: tab === key ? "white" : "var(--ink2)",
                            fontFamily: "var(--mono)",
                            fontSize: 10,
                            letterSpacing: 0.4,
                            textTransform: "uppercase",
                            cursor: "pointer",
                        }, children: key }, key))) }), _jsxs("div", { style: { padding: 20, overflowY: "auto", display: "flex", flexDirection: "column", gap: 16 }, children: [tab === "workspaces" && (_jsxs(_Fragment, { children: [_jsxs("section", { style: { display: "flex", flexDirection: "column", gap: 8 }, children: [_jsx("div", { style: labelStyle, children: "New workspace" }), _jsx("input", { placeholder: "Name (e.g. Office, Personal, Sankhya AI Labs)", value: newWsName, onChange: (e) => setNewWsName(e.target.value), style: inputStyle }), _jsxs("div", { style: { display: "flex", gap: 8 }, children: [_jsx("input", { placeholder: "Root folder (absolute path)", value: newWsRoot, onChange: (e) => setNewWsRoot(e.target.value), style: { ...inputStyle, flex: 1 } }), _jsx("button", { onClick: () => void pickFolder((path) => setNewWsRoot(path)), style: buttonGhost, disabled: busy, children: "browse\u2026" })] }), _jsx("textarea", { placeholder: "Description (optional)", value: newWsDesc, onChange: (e) => setNewWsDesc(e.target.value), rows: 2, style: { ...inputStyle, resize: "vertical" } }), _jsx("div", { style: { display: "flex", justifyContent: "flex-end" }, children: _jsx("button", { onClick: () => void onCreateWorkspace(), disabled: busy || !newWsName.trim() || !newWsRoot.trim(), style: {
                                                    ...buttonPrimary,
                                                    opacity: busy || !newWsName.trim() || !newWsRoot.trim() ? 0.5 : 1,
                                                }, children: "create workspace" }) })] }), _jsxs("section", { style: { display: "flex", flexDirection: "column", gap: 8 }, children: [_jsxs("div", { style: labelStyle, children: ["Existing \u00B7 ", workspaces.length] }), workspaces.length === 0 ? (_jsx("div", { style: {
                                                padding: 14,
                                                border: "1px dashed var(--border)",
                                                fontFamily: "var(--mono)",
                                                fontSize: 10,
                                                color: "var(--ink3)",
                                                lineHeight: 1.55,
                                            }, children: "No workspaces yet. Create one above." })) : (_jsx("div", { style: { display: "grid", gap: 6 }, children: workspaces.map((workspace) => {
                                                const active = workspace.id === selectedWorkspaceId;
                                                const projectCount = workspace.projects?.length || 0;
                                                return (_jsxs("button", { onClick: () => setSelectedWorkspaceId(workspace.id), style: {
                                                        textAlign: "left",
                                                        padding: "10px 12px",
                                                        border: `1px solid ${active ? "var(--accent)" : "var(--border)"}`,
                                                        background: active ? "var(--surface)" : "white",
                                                        display: "flex",
                                                        justifyContent: "space-between",
                                                        gap: 12,
                                                        cursor: "pointer",
                                                    }, children: [_jsxs("div", { style: { minWidth: 0 }, children: [_jsx("div", { style: { fontSize: 13, fontWeight: 600, lineHeight: 1.3 }, children: workspace.label || workspace.name }), _jsx("div", { style: { fontFamily: "var(--mono)", fontSize: 9, color: "var(--ink3)", marginTop: 2 }, children: workspace.workspacePath || workspace.rootPath || "—" })] }), _jsxs("span", { style: { fontFamily: "var(--mono)", fontSize: 9, color: "var(--ink3)" }, children: [projectCount, " project", projectCount === 1 ? "" : "s"] })] }, workspace.id));
                                            }) }))] }), currentWorkspace && (_jsxs("section", { style: {
                                        display: "flex",
                                        flexDirection: "column",
                                        gap: 8,
                                        borderTop: "1px solid var(--border)",
                                        paddingTop: 14,
                                    }, children: [_jsxs("div", { style: labelStyle, children: ["Edit \u00B7 ", currentWorkspace.label || currentWorkspace.name] }), _jsx("input", { value: editWsName, onChange: (e) => setEditWsName(e.target.value), placeholder: "Name", style: inputStyle }), _jsxs("div", { style: { display: "flex", gap: 8 }, children: [_jsx("input", { value: editWsRoot, onChange: (e) => setEditWsRoot(e.target.value), placeholder: "Root folder", style: { ...inputStyle, flex: 1 } }), _jsx("button", { onClick: () => void pickFolder((path) => setEditWsRoot(path)), style: buttonGhost, disabled: busy, children: "browse\u2026" })] }), _jsx("textarea", { value: editWsDesc, onChange: (e) => setEditWsDesc(e.target.value), placeholder: "Description", rows: 2, style: { ...inputStyle, resize: "vertical" } }), _jsx("div", { style: { display: "flex", justifyContent: "flex-end", gap: 8 }, children: _jsx("button", { onClick: () => void onUpdateWorkspace(), disabled: busy, style: { ...buttonPrimary, opacity: busy ? 0.5 : 1 }, children: "save changes" }) }), _jsxs("div", { style: {
                                                marginTop: 8,
                                                padding: 12,
                                                border: "1px solid rgba(203,63,78,0.3)",
                                                borderRadius: 4,
                                                background: "rgba(203,63,78,0.04)",
                                            }, children: [_jsx("div", { style: {
                                                        fontFamily: "var(--mono)",
                                                        fontSize: 9,
                                                        color: "var(--rose)",
                                                        letterSpacing: 0.5,
                                                        textTransform: "uppercase",
                                                        marginBottom: 6,
                                                    }, children: "Danger zone" }), _jsxs("div", { style: { fontSize: 11, color: "var(--ink2)", lineHeight: 1.5, marginBottom: 8 }, children: ["Deleting ", _jsx("strong", { children: currentWorkspace.label || currentWorkspace.name }), " removes every project, asset, and line message. Sessions remain but detach. Type the workspace name to confirm."] }), _jsxs("div", { style: { display: "flex", gap: 8 }, children: [_jsx("input", { value: deleteConfirm, onChange: (e) => setDeleteConfirm(e.target.value), placeholder: currentWorkspace.label || currentWorkspace.name, style: { ...inputStyle, flex: 1 } }), _jsx("button", { onClick: () => void onDeleteWorkspace(), disabled: busy ||
                                                                deleteConfirm.trim() !== (currentWorkspace.label || currentWorkspace.name), style: {
                                                                ...buttonDanger,
                                                                opacity: busy ||
                                                                    deleteConfirm.trim() !== (currentWorkspace.label || currentWorkspace.name)
                                                                    ? 0.5
                                                                    : 1,
                                                            }, children: "delete workspace" })] })] })] }))] })), tab === "projects" && (_jsxs(_Fragment, { children: [_jsxs("section", { style: { display: "flex", flexDirection: "column", gap: 8 }, children: [_jsx("div", { style: labelStyle, children: "Workspace" }), _jsxs("select", { value: selectedWorkspaceId, onChange: (e) => setSelectedWorkspaceId(e.target.value), style: inputStyle, children: [_jsx("option", { value: "", children: "\u2014 pick a workspace \u2014" }), workspaces.map((workspace) => (_jsx("option", { value: workspace.id, children: workspace.label || workspace.name }, workspace.id)))] })] }), currentWorkspace && (_jsxs(_Fragment, { children: [_jsxs("section", { style: { display: "flex", flexDirection: "column", gap: 8 }, children: [_jsxs("div", { style: labelStyle, children: ["Add project to ", currentWorkspace.label || currentWorkspace.name] }), _jsx("input", { placeholder: "Project name (e.g. frontend, backend, design)", value: newProjectName, onChange: (e) => setNewProjectName(e.target.value), style: inputStyle }), _jsx("textarea", { placeholder: "Description (optional)", value: newProjectDesc, onChange: (e) => setNewProjectDesc(e.target.value), rows: 2, style: { ...inputStyle, resize: "vertical" } }), _jsxs("div", { style: { display: "flex", gap: 8, alignItems: "center" }, children: [_jsx("span", { style: { fontFamily: "var(--mono)", fontSize: 10, color: "var(--ink3)" }, children: "default runtime" }), _jsx("select", { value: newProjectRuntime, onChange: (e) => setNewProjectRuntime(e.target.value), style: { ...inputStyle, flex: 1 }, children: runtimeOptions.map((option) => (_jsx("option", { value: option, children: option }, option))) })] }), _jsx("div", { style: { display: "flex", justifyContent: "flex-end" }, children: _jsx("button", { onClick: () => void onCreateProject(), disabled: busy || !newProjectName.trim(), style: {
                                                            ...buttonPrimary,
                                                            opacity: busy || !newProjectName.trim() ? 0.5 : 1,
                                                        }, children: "add project" }) })] }), _jsxs("section", { style: { display: "flex", flexDirection: "column", gap: 8 }, children: [_jsxs("div", { style: labelStyle, children: ["Projects \u00B7 ", (currentWorkspace.projects || []).length] }), (currentWorkspace.projects || []).length === 0 ? (_jsx("div", { style: {
                                                        padding: 14,
                                                        border: "1px dashed var(--border)",
                                                        fontFamily: "var(--mono)",
                                                        fontSize: 10,
                                                        color: "var(--ink3)",
                                                        lineHeight: 1.55,
                                                    }, children: "No projects yet \u2014 add the first one above." })) : (_jsx("div", { style: { display: "grid", gap: 10 }, children: (currentWorkspace.projects || []).map((project) => {
                                                        const draft = projectEdits[project.id] || {
                                                            name: project.name,
                                                            description: project.description || "",
                                                            defaultRuntime: project.defaultRuntime || "codex",
                                                        };
                                                        const setDraft = (patch) => setProjectEdits((prev) => ({
                                                            ...prev,
                                                            [project.id]: { ...draft, ...patch },
                                                        }));
                                                        return (_jsxs("div", { style: {
                                                                border: "1px solid var(--border)",
                                                                borderRadius: 6,
                                                                padding: 10,
                                                                display: "flex",
                                                                flexDirection: "column",
                                                                gap: 8,
                                                            }, children: [_jsx("input", { value: draft.name, onChange: (e) => setDraft({ name: e.target.value }), style: inputStyle }), _jsx("textarea", { value: draft.description, onChange: (e) => setDraft({ description: e.target.value }), rows: 2, style: { ...inputStyle, resize: "vertical" } }), _jsxs("div", { style: { display: "flex", gap: 8, alignItems: "center" }, children: [_jsx("span", { style: {
                                                                                fontFamily: "var(--mono)",
                                                                                fontSize: 10,
                                                                                color: "var(--ink3)",
                                                                            }, children: "runtime" }), _jsx("select", { value: draft.defaultRuntime, onChange: (e) => setDraft({ defaultRuntime: e.target.value }), style: { ...inputStyle, flex: 1 }, children: runtimeOptions.map((option) => (_jsx("option", { value: option, children: option }, option))) })] }), _jsxs("div", { style: { display: "flex", justifyContent: "space-between", gap: 8 }, children: [_jsx("button", { onClick: () => void onDeleteProject(project)(), disabled: busy, style: { ...buttonDanger, opacity: busy ? 0.5 : 1 }, children: "delete" }), _jsx("button", { onClick: () => void onUpdateProject(project)(), disabled: busy, style: { ...buttonPrimary, opacity: busy ? 0.5 : 1 }, children: "save" })] })] }, project.id));
                                                    }) }))] })] }))] }))] }), _jsxs("div", { style: {
                        padding: "10px 20px",
                        borderTop: "1px solid var(--border)",
                        background: "var(--bg)",
                        display: "flex",
                        alignItems: "center",
                        justifyContent: "space-between",
                        minHeight: 44,
                    }, children: [_jsx("div", { style: { fontFamily: "var(--mono)", fontSize: 10, lineHeight: 1.4 }, children: error ? (_jsx("span", { style: { color: "var(--rose)" }, children: error })) : notice ? (_jsx("span", { style: { color: "var(--green)" }, children: notice })) : (_jsx("span", { style: { color: "var(--ink3)" }, children: "Changes save immediately." })) }), _jsx("button", { onClick: onClose, style: buttonGhost, children: "close" })] })] }) }));
}
