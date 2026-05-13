import { jsx as _jsx, jsxs as _jsxs } from "react/jsx-runtime";
import { Suspense, lazy, useEffect, useRef, useState } from "react";
import { api } from "./api";
import { NavRail } from "./components/NavRail";
import { TopBar } from "./components/TopBar";
import { TweaksPanel } from "./components/TweaksPanel";
import { WorkspaceManagerModal } from "./components/WorkspaceManagerModal";
import { ChannelView } from "./views/ChannelView";
import { ConflictView } from "./views/ConflictView";
import { MemoryView } from "./views/MemoryView";
import { NotepadView } from "./views/NotepadView";
import { CommandCenterView, HandoffHubView, LearningInboxView, PortabilityTrustView, ProofReplayView, } from "./views/ProductViews";
import { RouterView } from "./views/RouterView";
import { TasksView } from "./views/TasksView";
import { WorkspaceView } from "./views/WorkspaceView";
const CanvasView = lazy(() => import("./views/CanvasView").then((module) => ({ default: module.CanvasView })));
const DEFAULT_TWEAKS = {
    accentHue: "36",
    compactNav: false,
    showTimestamps: true,
    canvasStyle: "force",
};
const GLOBAL_REFRESH_MS = 15000;
const PRODUCT_VIEWS = new Set([
    "command",
    "handoff",
    "replay",
    "learnings",
    "portability",
]);
function isProductView(view) {
    return PRODUCT_VIEWS.has(view);
}
function isGraphOnlyView(view) {
    return view === "canvas";
}
function isSelfHydratedView(view) {
    return view === "router" || view === "router/sessionshistory";
}
function normalizeView(view) {
    const raw = String(view || "").toLowerCase();
    if (raw === "home" || raw === "overview")
        return "command";
    if (raw === "memory")
        return "context";
    if (raw === "firewall" || raw === "context-firewall")
        return "router";
    if (raw === "repo" || raw === "brain" || raw === "repo-brain" || raw === "folders")
        return "canvas";
    if (raw === "learn" || raw === "learning")
        return "learnings";
    if (raw === "packs" || raw === "portable" || raw === "trust")
        return "portability";
    if (raw === "inbox" || raw === "review" || raw === "queue")
        return "conflicts";
    if (raw === "router/sessionshistory" ||
        raw === "router/session-history" ||
        raw === "router/history")
        return "router/sessionshistory";
    return raw || "command";
}
function initialParam(name) {
    if (typeof window === "undefined")
        return "";
    return new URLSearchParams(window.location.search).get(name) || "";
}
export default function App() {
    const [view, setView] = useState(() => normalizeView(initialParam("view")));
    const [managerOpen, setManagerOpen] = useState(false);
    const [managerTab, setManagerTab] = useState("workspaces");
    const [tasks, setTasks] = useState([]);
    const [activeTaskId, setActiveTaskId] = useState(() => initialParam("task"));
    const [tweaks, setTweaks] = useState(DEFAULT_TWEAKS);
    const [showTweaks, setShowTweaks] = useState(false);
    const [memoryCount, setMemoryCount] = useState(0);
    const [memoriesCache, setMemoriesCache] = useState([]);
    const [tokensSaved, setTokensSaved] = useState(0);
    const [conflictCount, setConflictCount] = useState(0);
    const [transitioning, setTransitioning] = useState(false);
    const [workspaceGraph, setWorkspaceGraph] = useState(null);
    const [orgGraph, setOrgGraph] = useState(null);
    const [viewer, setViewer] = useState(null);
    const [routerStats, setRouterStats] = useState(null);
    const [inboxCount, setInboxCount] = useState(0);
    const [projectIndex, setProjectIndex] = useState(null);
    const [selectedProjectId, setSelectedProjectId] = useState(() => initialParam("project"));
    const [selectedWorkspaceId, setSelectedWorkspaceId] = useState(() => initialParam("workspace"));
    const [selectedSessionId, setSelectedSessionId] = useState(() => initialParam("session"));
    const snapshotSignatures = useRef({});
    const updateSnapshot = (key, next, apply) => {
        let signature = "";
        try {
            signature = JSON.stringify(next) || "";
        }
        catch {
            signature = String(Date.now());
        }
        if (snapshotSignatures.current[key] === signature)
            return;
        snapshotSignatures.current[key] = signature;
        apply(next);
    };
    useEffect(() => {
        const params = new URLSearchParams(window.location.search);
        const urlView = params.get("view");
        const urlTask = params.get("task");
        const urlProject = params.get("project");
        const urlWorkspace = params.get("workspace");
        const urlSession = params.get("session");
        if (urlView)
            setView(normalizeView(urlView));
        if (urlTask)
            setActiveTaskId(urlTask);
        if (urlProject)
            setSelectedProjectId(urlProject);
        if (urlWorkspace)
            setSelectedWorkspaceId(urlWorkspace);
        if (urlSession)
            setSelectedSessionId(urlSession);
        const onPop = () => {
            const next = new URLSearchParams(window.location.search);
            setView(normalizeView(next.get("view")));
            setActiveTaskId(next.get("task") || "");
            setSelectedProjectId(next.get("project") || "");
            setSelectedWorkspaceId(next.get("workspace") || "");
            setSelectedSessionId(next.get("session") || "");
        };
        window.addEventListener("popstate", onPop);
        return () => window.removeEventListener("popstate", onPop);
    }, []);
    const refreshTasks = async () => {
        try {
            const t = await api.tasks();
            updateSnapshot("tasks", t.tasks || [], setTasks);
            setActiveTaskId((current) => current || t.tasks?.[0]?.id || "");
        }
        catch { }
    };
    const refreshWorkspaceGraph = async () => {
        try {
            const graph = selectedWorkspaceId
                ? await api.workspaceGraph(selectedWorkspaceId, selectedProjectId || undefined)
                : await api.workspaceGraph();
            updateSnapshot("workspaceGraph", graph, setWorkspaceGraph);
        }
        catch { }
    };
    const refreshOrgGraph = async () => {
        try {
            // Active-only on the canvas — no stale or paused sessions clutter the
            // FOLDERS view. Slice-1 server gates on ?active=true.
            const g = await api.orgGraph(undefined, { active: true });
            updateSnapshot("orgGraph", g, setOrgGraph);
        }
        catch { }
    };
    const refreshViewer = async () => {
        try {
            const v = await api.me();
            updateSnapshot("viewer", v, setViewer);
        }
        catch { }
    };
    const refreshRouterStats = async () => {
        try {
            const s = await api.routerStats();
            updateSnapshot("routerStats", s, setRouterStats);
            setTokensSaved(s.sessionTokensSaved || 0);
        }
        catch { }
    };
    const refreshInbox = async () => {
        try {
            const box = await api.inbox(viewer?.team_id ? { team: viewer.team_id, user: viewer.user_id } : { user: viewer?.user_id });
            const total = (box.totals?.proposals || 0) +
                (box.totals?.findings || 0) +
                (box.totals?.conflicts || 0);
            setInboxCount(total);
            setConflictCount(box.totals?.conflicts || 0);
        }
        catch { }
    };
    const refreshProjects = async () => {
        try {
            const snapshot = await api.workspaces();
            updateSnapshot("projectIndex", snapshot, setProjectIndex);
            setSelectedWorkspaceId((current) => current || snapshot.currentWorkspaceId || snapshot.workspaces?.[0]?.id || "");
            setSelectedProjectId((current) => current || snapshot.currentProjectId || snapshot.workspaces?.[0]?.projects?.[0]?.id || "");
            setSelectedSessionId((current) => current || snapshot.currentSessionId || "");
        }
        catch { }
    };
    useEffect(() => {
        void refreshViewer();
        void refreshInbox();
        if (!isProductView(view)) {
            void refreshRouterStats();
        }
        if (isGraphOnlyView(view)) {
            void refreshOrgGraph();
        }
        else if (isSelfHydratedView(view)) {
            return;
        }
        else if (!isProductView(view)) {
            void refreshTasks();
            void refreshOrgGraph();
            void refreshProjects().then(() => refreshWorkspaceGraph());
            void (async () => {
                try {
                    const m = await api.listMemories();
                    setMemoryCount(m.engrams?.length || 0);
                    setMemoriesCache(m.engrams || []);
                }
                catch { }
            })();
        }
    }, []);
    useEffect(() => {
        const timer = window.setInterval(() => {
            if (isProductView(view)) {
                void refreshInbox();
                return;
            }
            if (isSelfHydratedView(view)) {
                void refreshRouterStats();
                void refreshInbox();
                return;
            }
            if (isGraphOnlyView(view)) {
                void refreshOrgGraph();
                void refreshInbox();
                return;
            }
            void refreshTasks();
            void refreshProjects();
            void refreshWorkspaceGraph();
            void refreshOrgGraph();
            void refreshRouterStats();
            void refreshInbox();
        }, GLOBAL_REFRESH_MS);
        return () => window.clearInterval(timer);
    }, [view, selectedWorkspaceId, selectedProjectId, viewer?.team_id]);
    useEffect(() => {
        if (isProductView(view))
            return;
        if (isSelfHydratedView(view)) {
            void refreshRouterStats();
            void refreshInbox();
            return;
        }
        if (isGraphOnlyView(view)) {
            void refreshOrgGraph();
            return;
        }
        void refreshTasks();
        void refreshProjects().then(() => refreshWorkspaceGraph());
        void refreshOrgGraph();
        void (async () => {
            try {
                const m = await api.listMemories();
                setMemoryCount(m.engrams?.length || 0);
                setMemoriesCache(m.engrams || []);
            }
            catch { }
        })();
    }, [view]);
    const go = (v, taskId) => {
        const targetView = normalizeView(v);
        setTransitioning(true);
        setTimeout(() => {
            if (taskId)
                setActiveTaskId(taskId);
            setView(targetView);
            const params = new URLSearchParams(window.location.search);
            params.set("view", targetView);
            if (taskId || activeTaskId)
                params.set("task", taskId || activeTaskId);
            else
                params.delete("task");
            if (selectedProjectId)
                params.set("project", selectedProjectId);
            else
                params.delete("project");
            if (selectedWorkspaceId)
                params.set("workspace", selectedWorkspaceId);
            else
                params.delete("workspace");
            if (selectedSessionId)
                params.set("session", selectedSessionId);
            else
                params.delete("session");
            const qs = params.toString();
            const next = qs ? `?${qs}` : "";
            const hash = targetView === "context" && window.location.hash.startsWith("#vault")
                ? window.location.hash
                : "";
            window.history.pushState({}, "", `${window.location.pathname}${next}${hash}`);
            setTransitioning(false);
        }, 140);
    };
    const handleSelectTask = (id) => go("workspace", id);
    const handleSelectSession = (sessionId, taskId) => {
        if (sessionId)
            setSelectedSessionId(sessionId);
        if (taskId)
            setActiveTaskId(taskId);
        go("workspace", taskId || undefined);
    };
    const handleSelectWorkspace = (workspaceId) => {
        setSelectedWorkspaceId(workspaceId);
        setSelectedProjectId("");
        setSelectedSessionId("");
        go("workspace");
    };
    const handleSelectProject = (projectId, workspaceId) => {
        if (workspaceId)
            setSelectedWorkspaceId(workspaceId);
        setSelectedProjectId(projectId);
        setSelectedSessionId("");
        go("workspace");
    };
    useEffect(() => {
        const params = new URLSearchParams(window.location.search);
        params.set("view", view);
        if (activeTaskId)
            params.set("task", activeTaskId);
        else
            params.delete("task");
        if (selectedProjectId)
            params.set("project", selectedProjectId);
        else
            params.delete("project");
        if (selectedWorkspaceId)
            params.set("workspace", selectedWorkspaceId);
        else
            params.delete("workspace");
        if (selectedSessionId)
            params.set("session", selectedSessionId);
        else
            params.delete("session");
        const qs = params.toString();
        const next = `${window.location.pathname}${qs ? "?" + qs : ""}`;
        if (next !== `${window.location.pathname}${window.location.search}`) {
            window.history.replaceState({}, "", next);
        }
    }, [view, activeTaskId, selectedProjectId, selectedWorkspaceId, selectedSessionId]);
    const handleCreateWorkspace = async (name) => {
        await api.createWorkspaceRoot(name);
        await refreshProjects();
        await refreshWorkspaceGraph();
    };
    const handleCreateProject = async (workspaceId, payload) => {
        await api.createProject(workspaceId, payload);
        await refreshProjects();
        await refreshWorkspaceGraph();
    };
    const handleUpdateProject = async (projectId, payload) => {
        await api.updateProject(projectId, payload);
        await refreshProjects();
        await refreshWorkspaceGraph();
    };
    const handleAddWorkspaceFolder = async (workspaceId, path, label) => {
        await api.addWorkspaceFolder(workspaceId, path, label);
        await refreshProjects();
        await refreshWorkspaceGraph();
    };
    const handleUpdateWorkspace = async (workspaceId, label) => {
        await api.updateWorkspace(workspaceId, { label });
        await refreshProjects();
        await refreshWorkspaceGraph();
    };
    const handleRemoveWorkspaceFolder = async (workspaceId, path) => {
        await api.removeWorkspaceFolder(workspaceId, path);
        await refreshProjects();
        await refreshWorkspaceGraph();
    };
    const handleAddTask = async (title) => {
        try {
            const res = await api.createTask(title);
            setTasks((t) => [...t, res.task]);
            await refreshProjects();
            setTimeout(() => go("workspace", res.task.id), 80);
        }
        catch (e) {
            console.warn("createTask failed", e);
        }
    };
    const handleAddMemory = async (text) => {
        try {
            await api.remember(text, "short-term", []);
            setMemoryCount((c) => c + 1);
            const m = await api.listMemories();
            setMemoriesCache(m.engrams || []);
        }
        catch (e) {
            console.warn("remember failed", e);
        }
    };
    const handleLaunchFromNotepad = async (title, runtime, workspaceId, permissionMode, projectId) => {
        const targetWorkspace = workspaceId || selectedWorkspaceId || projectIndex?.currentWorkspaceId;
        if (!targetWorkspace || !title.trim())
            return;
        const res = await api.launchWorkspaceSession(targetWorkspace, runtime, title.trim(), runtime === "claude-code" ? permissionMode : undefined, undefined, projectId || selectedProjectId || projectIndex?.currentProjectId);
        await refreshTasks();
        await refreshProjects();
        await refreshWorkspaceGraph();
        if (res.session_id)
            setSelectedSessionId(res.session_id);
        if (res.task_id)
            setActiveTaskId(res.task_id);
        go("workspace", res.task_id || undefined);
    };
    const handleAddTaskNote = async (taskId, content) => {
        try {
            await api.addTaskNote(taskId, content);
            await refreshTasks();
            await refreshProjects();
        }
        catch (e) {
            console.warn("addTaskNote failed", e);
        }
    };
    const viewStyle = {
        opacity: transitioning ? 0 : 1,
        transform: transitioning ? "translateY(3px)" : "translateY(0)",
        transition: "opacity 0.14s ease, transform 0.14s ease",
        flex: 1,
        overflow: "hidden",
        display: "flex",
        flexDirection: "column",
    };
    const renderView = () => {
        if (view === "command")
            return _jsx(CommandCenterView, { onNavigate: (target) => go(target) });
        if (view === "channel")
            return (_jsx(ChannelView, { projectIndex: projectIndex, workspaceGraph: workspaceGraph, tasks: tasks, viewer: viewer, orgGraph: orgGraph, selectedWorkspaceId: selectedWorkspaceId, selectedProjectId: selectedProjectId, onSelectWorkspace: handleSelectWorkspace, onSelectProject: handleSelectProject, onSelectTask: handleSelectTask, onTasksRefresh: refreshTasks, onOpenCanvas: () => go("canvas"), onLaunchSession: handleLaunchFromNotepad, onOpenManager: (tab) => {
                    setManagerTab(tab || "workspaces");
                    setManagerOpen(true);
                }, tweaks: tweaks }));
        if (view === "notepad")
            return (_jsx(NotepadView, { projectIndex: projectIndex, memories: memoryCount, tokensSaved: tokensSaved, onAddTask: handleAddTask, onAddMemory: handleAddMemory, onSelectSession: handleSelectSession, onCreateWorkspace: handleCreateWorkspace, onLaunchSession: handleLaunchFromNotepad, onCreateProject: handleCreateProject, onOpenWorkspace: () => go("workspace"), onOpenTasks: () => go("tasks"), tweaks: tweaks }));
        if (view === "tasks")
            return (_jsx(TasksView, { tasks: tasks, projectIndex: projectIndex, onSelectTask: handleSelectTask, onSelectSession: handleSelectSession, tweaks: tweaks }));
        if (view === "workspace")
            return (_jsx(WorkspaceView, { tasks: tasks, activeTaskId: activeTaskId, selectedProjectId: selectedProjectId, selectedWorkspaceId: selectedWorkspaceId, selectedSessionId: selectedSessionId, projectIndex: projectIndex, workspaceGraph: workspaceGraph, onSelectTask: handleSelectTask, onSelectSession: handleSelectSession, onSelectProject: handleSelectProject, onCanvasOpen: () => go("canvas"), onNotepadOpen: () => go("context"), onAddTaskNote: handleAddTaskNote, onUpdateWorkspace: handleUpdateWorkspace, onAddWorkspaceFolder: handleAddWorkspaceFolder, onRemoveWorkspaceFolder: handleRemoveWorkspaceFolder, onCreateProject: handleCreateProject, onUpdateProject: handleUpdateProject, onTasksRefresh: refreshTasks, tweaks: tweaks }));
        if (view === "canvas")
            return (_jsx(Suspense, { fallback: _jsx("div", { style: {
                        height: "100%",
                        display: "grid",
                        alignItems: "start",
                        background: "var(--surface)",
                        padding: 20,
                    }, children: _jsxs("div", { style: {
                            border: "1px solid var(--border)",
                            background: "white",
                            padding: "16px 18px",
                            display: "flex",
                            justifyContent: "space-between",
                            alignItems: "center",
                            gap: 18,
                        }, children: [_jsxs("div", { children: [_jsx("div", { style: {
                                            fontFamily: "var(--mono)",
                                            fontSize: 10,
                                            color: "var(--green)",
                                            letterSpacing: "0.12em",
                                            textTransform: "uppercase",
                                        }, children: "Repo Brain" }), _jsx("div", { style: { marginTop: 6, fontSize: 20, fontWeight: 650 }, children: "Loading folder canvas" })] }), _jsx("div", { style: {
                                    fontFamily: "var(--mono)",
                                    fontSize: 10,
                                    color: "var(--ink3)",
                                    textTransform: "uppercase",
                                }, children: "Context Vault" })] }) }), children: _jsx(CanvasView, { tasks: tasks, selectedProjectId: selectedProjectId, workspaceGraph: workspaceGraph, orgGraph: orgGraph, viewer: viewer, onOpenVault: (teamId) => {
                        if (teamId)
                            window.location.hash = `#vault/${teamId}`;
                        go("context");
                    }, onOrgGraphChanged: () => {
                        void refreshOrgGraph();
                        void refreshViewer();
                        void refreshInbox();
                    }, onSelectTask: handleSelectTask, onSelectSession: handleSelectSession, onSelectWorkspace: handleSelectWorkspace, onSelectProject: handleSelectProject, onClose: () => go("workspace"), tweaks: tweaks }) }));
        if (view === "memory" || view === "context")
            return (_jsx(MemoryView, { onMemoryCountChange: setMemoryCount, viewer: viewer, orgGraph: orgGraph, onInboxChanged: refreshInbox }));
        if (view === "router" || view === "router/sessionshistory")
            return _jsx(RouterView, { onOpenFolders: () => go("canvas"), onOpenSetup: () => go("notepad") });
        if (view === "handoff")
            return _jsx(HandoffHubView, {});
        if (view === "replay")
            return _jsx(ProofReplayView, {});
        if (view === "learnings")
            return _jsx(LearningInboxView, {});
        if (view === "portability")
            return _jsx(PortabilityTrustView, {});
        if (view === "conflicts")
            return _jsx(ConflictView, { viewer: viewer, onChanged: refreshInbox });
        return null;
    };
    useEffect(() => {
        // Keyboard shortcut: Ctrl/Cmd+K shows tweaks panel.
        const onKey = (e) => {
            if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "k") {
                e.preventDefault();
                setShowTweaks((v) => !v);
            }
        };
        window.addEventListener("keydown", onKey);
        return () => window.removeEventListener("keydown", onKey);
    }, []);
    const handleRefreshAll = () => {
        void refreshViewer();
        void refreshTasks();
        void refreshProjects();
        void refreshWorkspaceGraph();
        void refreshOrgGraph();
        void refreshRouterStats();
        void refreshInbox();
    };
    return (_jsxs("div", { style: { height: "100vh", display: "flex", overflow: "hidden" }, children: [_jsx(NavRail, { view: view, setView: (v) => go(v), conflictCount: inboxCount || conflictCount }), _jsxs("div", { style: {
                    flex: 1,
                    display: "flex",
                    flexDirection: "column",
                    overflow: "hidden",
                }, children: [_jsx(TopBar, { viewer: viewer, routerStats: routerStats, onRefresh: handleRefreshAll, onOpenTweaks: () => setShowTweaks((v) => !v), onResetWorkspace: async () => {
                            if (!window.confirm("Reset workspace? Deletes projects, teams, folders, context items, proposals and findings for this org. Memory engrams are unaffected."))
                                return;
                            try {
                                await api.enterpriseResetWorkspace();
                            }
                            finally {
                                handleRefreshAll();
                            }
                        } }), _jsx("div", { style: viewStyle, children: renderView() })] }), _jsx(TweaksPanel, { tweaks: tweaks, setTweaks: setTweaks, visible: showTweaks }), _jsx(WorkspaceManagerModal, { open: managerOpen, onClose: () => setManagerOpen(false), projectIndex: projectIndex, initialWorkspaceId: selectedWorkspaceId, initialTab: managerTab, onChanged: async () => {
                    await refreshProjects();
                    await refreshWorkspaceGraph();
                } })] }));
}
