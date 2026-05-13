import { jsxs as _jsxs, jsx as _jsx } from "react/jsx-runtime";
import { useEffect, useMemo, useRef, useState } from "react";
import { api } from "../api";
import { StatPill } from "./ui/StatPill";
const MS_MINUTE = 60000;
const MS_HOUR = 3600000;
const MS_DAY = 86400000;
function fmtRelative(value) {
    if (!value)
        return "";
    const when = Date.parse(value);
    if (Number.isNaN(when))
        return String(value);
    const delta = Date.now() - when;
    if (delta < MS_MINUTE)
        return "just now";
    if (delta < MS_HOUR)
        return `${Math.round(delta / MS_MINUTE)}m ago`;
    if (delta < MS_DAY)
        return `${Math.round(delta / MS_HOUR)}h ago`;
    return `${Math.round(delta / MS_DAY)}d ago`;
}
function fmtClock(value) {
    if (!value)
        return "";
    const when = new Date(value);
    if (Number.isNaN(when.getTime()))
        return String(value);
    return when.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}
// Tool-kind → accent. Matches canvas palette so broadcast linkages feel
// coherent across views.
const KIND_TONE = {
    broadcast: "#e06b3f",
    "tool.routed_read": "#4d6cff",
    "tool.routed_bash": "#0b8b5f",
    "tool.routed_grep": "#1fa971",
    "tool.routed_agent": "#d74b7b",
    "tool.hook_post_tool": "#64748b",
    "tool.artifact_parse": "#d74b7b",
    note: "#1a1a1a",
    update: "#1a1a1a",
};
function kindTone(kind) {
    const key = String(kind || "").toLowerCase();
    if (KIND_TONE[key])
        return KIND_TONE[key];
    if (key.startsWith("tool."))
        return "#4d6cff";
    return "var(--accent)";
}
function formatKindLabel(kind) {
    const raw = String(kind || "");
    if (!raw)
        return "note";
    if (raw.startsWith("tool."))
        return raw.slice(5);
    return raw;
}
function runtimeTone(runtime) {
    const key = String(runtime || "").toLowerCase();
    if (key.includes("claude"))
        return "#e06b3f";
    if (key.includes("codex"))
        return "#1a1a1a";
    if (key.includes("cursor"))
        return "#4d6cff";
    if (key.includes("browser"))
        return "#1fa971";
    return "var(--ink3)";
}
function sessionsForRuntime(projects, workspaceSessions) {
    const buckets = new Map();
    const visit = (session) => {
        const runtime = String(session.runtime || "unknown").toLowerCase();
        const current = buckets.get(runtime) || { count: 0, latestUpdate: null, isLive: false };
        current.count += 1;
        if (session.updatedAt && (!current.latestUpdate || session.updatedAt > current.latestUpdate)) {
            current.latestUpdate = session.updatedAt;
        }
        if (session.isCurrent || session.state === "active" || session.state === "recent") {
            current.isLive = true;
        }
        buckets.set(runtime, current);
    };
    for (const session of workspaceSessions || [])
        visit(session);
    for (const project of projects || []) {
        for (const session of project.sessions || [])
            visit(session);
    }
    return Array.from(buckets.entries())
        .map(([runtime, value]) => ({ runtime, ...value }))
        .sort((a, b) => b.count - a.count);
}
export function ConnectedAgents({ workspace, projects, workspaceSessions, }) {
    const buckets = useMemo(() => sessionsForRuntime(projects, workspaceSessions), [projects, workspaceSessions]);
    const total = buckets.reduce((sum, item) => sum + item.count, 0);
    return (_jsxs("div", { style: {
            border: "1px solid var(--border)",
            background: "white",
            padding: 14,
        }, children: [_jsxs("div", { style: {
                    display: "flex",
                    justifyContent: "space-between",
                    alignItems: "baseline",
                    marginBottom: 10,
                }, children: [_jsxs("span", { style: {
                            fontFamily: "var(--mono)",
                            fontSize: 9,
                            letterSpacing: 0.6,
                            color: "var(--ink3)",
                            textTransform: "uppercase",
                        }, children: ["Connected agents \u00B7 ", total] }), workspace ? (_jsx("span", { style: {
                            fontFamily: "var(--mono)",
                            fontSize: 9,
                            color: "var(--ink3)",
                        }, children: workspace.label || workspace.name })) : null] }), buckets.length === 0 ? (_jsx("div", { style: {
                    fontFamily: "var(--mono)",
                    fontSize: 10,
                    color: "var(--ink3)",
                    lineHeight: 1.55,
                }, children: "No agent sessions yet. Launch claude-code or codex in this workspace \u2014 they will register here and start publishing to the line." })) : (_jsx("div", { style: { display: "grid", gap: 6 }, children: buckets.map((bucket) => (_jsxs("div", { style: {
                        display: "flex",
                        alignItems: "center",
                        justifyContent: "space-between",
                        gap: 10,
                    }, children: [_jsxs("div", { style: { display: "flex", alignItems: "center", gap: 8, minWidth: 0 }, children: [_jsx("span", { style: {
                                        width: 7,
                                        height: 7,
                                        borderRadius: "50%",
                                        background: bucket.isLive ? "var(--green)" : "var(--ink3)",
                                        boxShadow: bucket.isLive ? "0 0 0 3px rgba(31,169,113,0.18)" : "none",
                                        flexShrink: 0,
                                    } }), _jsx("span", { style: {
                                        fontFamily: "var(--mono)",
                                        fontSize: 11,
                                        color: runtimeTone(bucket.runtime),
                                        whiteSpace: "nowrap",
                                        overflow: "hidden",
                                        textOverflow: "ellipsis",
                                    }, children: bucket.runtime }), _jsxs("span", { style: {
                                        fontFamily: "var(--mono)",
                                        fontSize: 9,
                                        color: "var(--ink3)",
                                    }, children: ["\u00B7 ", bucket.count] })] }), bucket.latestUpdate ? (_jsx("span", { style: {
                                fontFamily: "var(--mono)",
                                fontSize: 9,
                                color: "var(--ink3)",
                            }, children: fmtRelative(bucket.latestUpdate) })) : null] }, bucket.runtime))) }))] }));
}
export function LineMessageCard({ message, workspace, onOpenTask, }) {
    const sourceProject = workspace?.projects.find((project) => project.id === message.project_id)?.name || "workspace";
    const targetProject = workspace?.projects.find((project) => project.id === message.target_project_id)?.name || "";
    const meta = (message.metadata || {});
    const runtime = String(meta.harness || meta.runtime || "");
    const tool = String(meta.tool_name || meta.toolName || "");
    const ptr = String(meta.ptr || "");
    const body = message.body || "";
    const accent = kindTone(message.message_kind);
    return (_jsxs("div", { style: {
            border: "1px solid var(--border)",
            borderLeft: `3px solid ${accent}`,
            background: "white",
            padding: "11px 13px",
        }, children: [_jsxs("div", { style: {
                    display: "flex",
                    justifyContent: "space-between",
                    alignItems: "baseline",
                    gap: 10,
                    marginBottom: 6,
                }, children: [_jsxs("div", { style: {
                            fontFamily: "var(--mono)",
                            fontSize: 10,
                            color: runtimeTone(runtime),
                            letterSpacing: 0.4,
                        }, children: [fmtClock(message.created_at), runtime ? ` · ${runtime}` : "", sourceProject && sourceProject !== "workspace" ? ` · ${sourceProject}` : ""] }), _jsx("span", { style: { fontFamily: "var(--mono)", fontSize: 9, color: "var(--ink3)" }, children: fmtRelative(message.created_at) })] }), message.title ? (_jsx("div", { style: { fontSize: 13, fontWeight: 600, marginBottom: 5, lineHeight: 1.35 }, children: message.title })) : null, body ? (_jsx("div", { style: {
                    fontSize: 12,
                    color: "var(--ink2)",
                    lineHeight: 1.55,
                    whiteSpace: "pre-wrap",
                    wordBreak: "break-word",
                }, children: body })) : null, _jsxs("div", { style: { display: "flex", gap: 5, flexWrap: "wrap", marginTop: 8 }, children: [_jsx(StatPill, { label: formatKindLabel(message.message_kind), tone: accent }), tool ? _jsx(StatPill, { label: tool.toLowerCase() }) : null, targetProject ? _jsx(StatPill, { label: `→ ${targetProject}`, tone: "#4d6cff" }) : null, ptr ? _jsx(StatPill, { label: ptr }) : null, message.task_id && onOpenTask ? (_jsx("button", { onClick: () => onOpenTask(String(message.task_id)), style: {
                            padding: "2px 8px",
                            border: "1px solid var(--ink)",
                            background: "var(--ink)",
                            color: "white",
                            fontFamily: "var(--mono)",
                            fontSize: 9,
                            letterSpacing: 0.4,
                            textTransform: "uppercase",
                            cursor: "pointer",
                        }, children: "open task" })) : null] })] }));
}
export function LineComposer({ workspace, activeProjectId, sessionId, taskId, onPublished, }) {
    const [title, setTitle] = useState("");
    const [body, setBody] = useState("");
    const [targetProjectId, setTargetProjectId] = useState("");
    const [busy, setBusy] = useState(false);
    const [notice, setNotice] = useState(null);
    const [error, setError] = useState(null);
    const bodyRef = useRef(null);
    useEffect(() => {
        bodyRef.current?.focus();
    }, [workspace?.id]);
    const targetOptions = useMemo(() => (workspace?.projects || []).filter((project) => !activeProjectId || project.id !== activeProjectId), [workspace?.projects, activeProjectId]);
    const publish = async () => {
        if (!workspace?.id || !body.trim() || busy)
            return;
        setBusy(true);
        setError(null);
        setNotice(null);
        try {
            const response = await api.publishWorkspaceLineMessage(workspace.id, {
                project_id: activeProjectId || undefined,
                target_project_id: targetProjectId || undefined,
                channel: activeProjectId ? "project" : "workspace",
                session_id: sessionId || undefined,
                task_id: taskId || undefined,
                message_kind: targetProjectId ? "broadcast" : "note",
                title: title.trim() || undefined,
                body: body.trim(),
                metadata: {
                    sourceProject: workspace.projects.find((p) => p.id === activeProjectId)?.name,
                },
            });
            if (response.suggestedTask) {
                const target = workspace.projects.find((p) => p.id === targetProjectId);
                setNotice(target
                    ? `Broadcast sent · suggested task created in ${target.name}.`
                    : "Broadcast sent · suggested task created.");
            }
            else {
                setNotice("Published to the workspace line.");
            }
            setTitle("");
            setBody("");
            setTargetProjectId("");
            onPublished?.(response.message, response.suggestedTask);
        }
        catch (e) {
            setError(String(e));
        }
        finally {
            setBusy(false);
        }
    };
    if (!workspace) {
        return (_jsx("div", { style: {
                padding: 16,
                border: "1px solid var(--border)",
                fontFamily: "var(--mono)",
                fontSize: 10,
                color: "var(--ink3)",
                background: "white",
            }, children: "No workspace selected." }));
    }
    const disabled = !body.trim() || busy;
    const targetName = targetOptions.find((p) => p.id === targetProjectId)?.name;
    const ctaLabel = busy
        ? "publishing…"
        : targetProjectId
            ? `broadcast → ${targetName || "project"}`
            : "publish update";
    return (_jsxs("div", { style: {
            border: "1px solid var(--border)",
            background: "white",
            padding: 14,
            display: "flex",
            flexDirection: "column",
            gap: 8,
        }, children: [_jsx("input", { value: title, onChange: (e) => setTitle(e.target.value), placeholder: "headline (optional) \u2014 e.g. user.plan field added", style: {
                    border: "1px solid var(--border)",
                    padding: "9px 11px",
                    background: "var(--bg)",
                    fontSize: 13,
                } }), _jsxs("select", { value: targetProjectId, onChange: (e) => setTargetProjectId(e.target.value), style: {
                    border: "1px solid var(--border)",
                    padding: "9px 11px",
                    background: "var(--bg)",
                    fontFamily: "var(--mono)",
                    fontSize: 10,
                }, children: [_jsxs("option", { value: "", children: ["Publish to current ", activeProjectId ? "project" : "workspace", " only"] }), targetOptions.map((project) => (_jsxs("option", { value: project.id, children: ["Broadcast into ", project.name, " (creates task)"] }, project.id)))] }), _jsx("textarea", { ref: bodyRef, value: body, onChange: (e) => setBody(e.target.value), placeholder: targetProjectId
                    ? "What should the target project's agent know? It will spawn a task with this context."
                    : "Broadcast a dependency change, a tool result, or a follow-up signal to the workspace line…", rows: 4, onKeyDown: (e) => {
                    if ((e.metaKey || e.ctrlKey) && e.key === "Enter") {
                        e.preventDefault();
                        void publish();
                    }
                }, style: {
                    border: "1px solid var(--border)",
                    padding: "10px 12px",
                    background: "var(--bg)",
                    fontSize: 13,
                    lineHeight: 1.55,
                    resize: "vertical",
                } }), _jsxs("div", { style: {
                    display: "flex",
                    justifyContent: "space-between",
                    alignItems: "center",
                    gap: 10,
                }, children: [_jsx("span", { style: {
                            fontFamily: "var(--mono)",
                            fontSize: 9,
                            color: "var(--ink3)",
                            letterSpacing: 0.3,
                        }, children: "\u2318/Ctrl + Enter to publish" }), _jsx("button", { onClick: () => void publish(), disabled: disabled, style: {
                            padding: "8px 14px",
                            border: "1px solid var(--ink)",
                            background: disabled ? "var(--ink3)" : "var(--ink)",
                            color: "white",
                            fontFamily: "var(--mono)",
                            fontSize: 10,
                            letterSpacing: 0.4,
                            opacity: disabled ? 0.7 : 1,
                            cursor: disabled ? "not-allowed" : "pointer",
                        }, children: ctaLabel })] }), notice ? (_jsx("div", { style: { fontSize: 11, color: "var(--green)", lineHeight: 1.5 }, children: notice })) : null, error ? (_jsx("div", { style: { fontSize: 11, color: "var(--rose)", lineHeight: 1.5 }, children: error })) : null] }));
}
// ---------------------------------------------------------------------------
// Live stream hook — owns the EventSource + initial fetch + dedup merge.
// Exposed so both ChannelView (full page) and WorkspaceView (right rail)
// share one subscription.
// ---------------------------------------------------------------------------
export function useWorkspaceLine(workspaceId, projectId) {
    const [messages, setMessages] = useState([]);
    const [live, setLive] = useState(false);
    const [error, setError] = useState(null);
    const merge = (incoming) => {
        setMessages((current) => {
            const seen = new Map();
            [...incoming, ...current].forEach((message) => {
                if (message?.id)
                    seen.set(message.id, message);
            });
            return Array.from(seen.values()).sort((a, b) => String(b.created_at || "").localeCompare(String(a.created_at || "")));
        });
    };
    const refresh = async () => {
        if (!workspaceId) {
            setMessages([]);
            return;
        }
        try {
            const snapshot = await api.workspaceLineMessages(workspaceId, {
                project_id: projectId || undefined,
                limit: 100,
            });
            setMessages(snapshot.messages || []);
        }
        catch (e) {
            setError(String(e));
        }
    };
    useEffect(() => {
        void refresh();
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [workspaceId, projectId]);
    useEffect(() => {
        if (!workspaceId) {
            setLive(false);
            return;
        }
        const qs = new URLSearchParams();
        if (projectId)
            qs.set("project_id", projectId);
        const source = new EventSource(`/api/workspaces/${encodeURIComponent(workspaceId)}/line/stream${qs.toString() ? `?${qs.toString()}` : ""}`);
        source.onopen = () => setLive(true);
        source.onmessage = (event) => {
            try {
                const message = JSON.parse(event.data);
                merge([message]);
            }
            catch {
                /* keep-alive frames */
            }
        };
        source.onerror = () => {
            setLive(false);
            source.close();
        };
        return () => {
            source.close();
            setLive(false);
        };
    }, [workspaceId, projectId]);
    return { messages, live, error, merge, refresh };
}
