import { jsx as _jsx, jsxs as _jsxs } from "react/jsx-runtime";
import { Suspense, lazy, useEffect, useState } from "react";
import { api } from "./api";
import { NavRail } from "./components/NavRail";
import { TweaksPanel } from "./components/TweaksPanel";
import { WorkspaceManagerModal } from "./components/WorkspaceManagerModal";
import { ChannelView } from "./views/ChannelView";
import { ConflictView } from "./views/ConflictView";
import { MemoryView } from "./views/MemoryView";
import { NotepadView } from "./views/NotepadView";
import { RouterView } from "./views/RouterView";
import { TasksView } from "./views/TasksView";
import { WorkspaceView } from "./views/WorkspaceView";
const CanvasView = lazy(() => import("./views/CanvasView").then((module) => ({ default: module.CanvasView })));
const DEFAULT_TWEAKS = {
    accentHue: "36",
    compactNav: false,
    showTimestamps: true,
    canvasStyle: "dots",
};
export default function App() {
    const [view, setView] = useState("channel");
    const [managerOpen, setManagerOpen] = useState(false);
    const [managerTab, setManagerTab] = useState("workspaces");
    const [tasks, setTasks] = useState([]);
    const [activeTaskId, setActiveTaskId] = useState("");
    const [tweaks, setTweaks] = useState(DEFAULT_TWEAKS);
    const [showTweaks, setShowTweaks] = useState(false);
    const [memoryCount, setMemoryCount] = useState(0);
    const [memoriesCache, setMemoriesCache] = useState([]);
    const [tokensSaved, setTokensSaved] = useState(0);
    const [conflictCount, setConflictCount] = useState(0);
    const [transitioning, setTransitioning] = useState(false);
    const [workspaceGraph, setWorkspaceGraph] = useState(null);
    const [projectIndex, setProjectIndex] = useState(null);
    const [selectedProjectId, setSelectedProjectId] = useState("");
    const [selectedWorkspaceId, setSelectedWorkspaceId] = useState("");
    const [selectedSessionId, setSelectedSessionId] = useState("");
    useEffect(() => {
        const params = new URLSearchParams(window.location.search);
        const urlView = params.get("view");
        const urlTask = params.get("task");
        const urlProject = params.get("project");
        const urlWorkspace = params.get("workspace");
        const urlSession = params.get("session");
        if (urlView)
            setView(urlView);
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
            setView(next.get("view") || "channel");
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
            setTasks(t.tasks || []);
            setActiveTaskId((current) => current || t.tasks?.[0]?.id || "");
        }
        catch { }
    };
    const refreshWorkspaceGraph = async () => {
        try {
            const graph = selectedWorkspaceId
                ? await api.workspaceGraph(selectedWorkspaceId, selectedProjectId || undefined)
                : await api.workspaceGraph();
            setWorkspaceGraph(graph);
        }
        catch { }
    };
    const refreshProjects = async () => {
        try {
            const snapshot = await api.workspaces();
            setProjectIndex(snapshot);
            setSelectedWorkspaceId((current) => current || snapshot.currentWorkspaceId || snapshot.workspaces?.[0]?.id || "");
            setSelectedProjectId((current) => current || snapshot.currentProjectId || snapshot.workspaces?.[0]?.projects?.[0]?.id || "");
            setSelectedSessionId((current) => current || snapshot.currentSessionId || "");
        }
        catch { }
    };
    useEffect(() => {
        (async () => {
            await refreshTasks();
            try {
                const m = await api.listMemories();
                setMemoryCount(m.engrams?.length || 0);
                setMemoriesCache(m.engrams || []);
            }
            catch { }
            try {
                const s = await api.routerStats();
                setTokensSaved(s.sessionTokensSaved || 0);
            }
            catch { }
            try {
                const c = await api.conflicts();
                setConflictCount((c.conflicts || []).length);
            }
            catch { }
            await refreshProjects();
            await refreshWorkspaceGraph();
        })();
    }, []);
    useEffect(() => {
        const timer = window.setInterval(() => {
            void refreshTasks();
            void refreshProjects();
            void refreshWorkspaceGraph();
        }, 5000);
        return () => window.clearInterval(timer);
    }, [selectedWorkspaceId, selectedProjectId]);
    const go = (v, taskId) => {
        setTransitioning(true);
        setTimeout(() => {
            if (taskId)
                setActiveTaskId(taskId);
            setView(v);
            const params = new URLSearchParams(window.location.search);
            params.set("view", v);
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
            window.history.pushState({}, "", `${window.location.pathname}${next}`);
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
    const handleCreateWorkspace = async (name, workspacePath) => {
        await api.createWorkspaceRoot(name, workspacePath);
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
        if (view === "channel")
            return (_jsx(ChannelView, { projectIndex: projectIndex, workspaceGraph: workspaceGraph, tasks: tasks, selectedWorkspaceId: selectedWorkspaceId, selectedProjectId: selectedProjectId, onSelectWorkspace: handleSelectWorkspace, onSelectProject: handleSelectProject, onSelectTask: handleSelectTask, onTasksRefresh: refreshTasks, onOpenCanvas: () => go("canvas"), onLaunchSession: handleLaunchFromNotepad, onOpenManager: (tab) => {
                    setManagerTab(tab || "workspaces");
                    setManagerOpen(true);
                }, tweaks: tweaks }));
        if (view === "notepad")
            return (_jsx(NotepadView, { projectIndex: projectIndex, memories: memoryCount, tokensSaved: tokensSaved, onAddTask: handleAddTask, onAddMemory: handleAddMemory, onSelectSession: handleSelectSession, onCreateWorkspace: handleCreateWorkspace, onLaunchSession: handleLaunchFromNotepad, onCreateProject: handleCreateProject, onOpenWorkspace: () => go("workspace"), onOpenTasks: () => go("tasks"), tweaks: tweaks }));
        if (view === "tasks")
            return (_jsx(TasksView, { tasks: tasks, projectIndex: projectIndex, onSelectTask: handleSelectTask, onSelectSession: handleSelectSession, tweaks: tweaks }));
        if (view === "workspace")
            return (_jsx(WorkspaceView, { tasks: tasks, activeTaskId: activeTaskId, selectedProjectId: selectedProjectId, selectedWorkspaceId: selectedWorkspaceId, selectedSessionId: selectedSessionId, projectIndex: projectIndex, workspaceGraph: workspaceGraph, onSelectTask: handleSelectTask, onSelectSession: handleSelectSession, onSelectProject: handleSelectProject, onCanvasOpen: () => go("canvas"), onNotepadOpen: () => go("notepad"), onAddTaskNote: handleAddTaskNote, onUpdateWorkspace: handleUpdateWorkspace, onAddWorkspaceFolder: handleAddWorkspaceFolder, onRemoveWorkspaceFolder: handleRemoveWorkspaceFolder, onCreateProject: handleCreateProject, onUpdateProject: handleUpdateProject, onTasksRefresh: refreshTasks, tweaks: tweaks }));
        if (view === "canvas")
            return (_jsx(Suspense, { fallback: _jsx("div", { style: {
                        height: "100%",
                        display: "flex",
                        alignItems: "center",
                        justifyContent: "center",
                        fontFamily: "var(--mono)",
                        fontSize: 11,
                        color: "var(--ink3)",
                    }, children: "loading collaboration graph\u2026" }), children: _jsx(CanvasView, { tasks: tasks, selectedProjectId: selectedProjectId, workspaceGraph: workspaceGraph, onSelectTask: handleSelectTask, onSelectSession: handleSelectSession, onSelectWorkspace: handleSelectWorkspace, onSelectProject: handleSelectProject, onClose: () => go("workspace"), tweaks: tweaks }) }));
        if (view === "memory")
            return _jsx(MemoryView, { onMemoryCountChange: setMemoryCount });
        if (view === "router")
            return _jsx(RouterView, {});
        if (view === "conflicts")
            return _jsx(ConflictView, {});
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
    return (_jsxs("div", { style: { height: "100vh", display: "flex", overflow: "hidden" }, children: [_jsx(NavRail, { view: view, setView: (v) => go(v), conflictCount: conflictCount }), _jsx("div", { style: viewStyle, children: renderView() }), _jsx(TweaksPanel, { tweaks: tweaks, setTweaks: setTweaks, visible: showTweaks }), _jsx(WorkspaceManagerModal, { open: managerOpen, onClose: () => setManagerOpen(false), projectIndex: projectIndex, initialWorkspaceId: selectedWorkspaceId, initialTab: managerTab, onChanged: async () => {
                    await refreshProjects();
                    await refreshWorkspaceGraph();
                } })] }));
}
