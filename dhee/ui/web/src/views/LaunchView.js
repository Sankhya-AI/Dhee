import { jsx as _jsx, jsxs as _jsxs } from "react/jsx-runtime";
import { useEffect, useMemo, useState } from "react";
import { api } from "../api";
import { SectionHeader } from "../components/ui/SectionHeader";
import { StatPill } from "../components/ui/StatPill";
import { TierBadge } from "../components/ui/TierBadge";
export function LaunchView({ tasks, memories, projectIndex, selectedWorkspaceId, workspaceGraph, onLaunched, }) {
    const [runtime, setRuntime] = useState("claude-code");
    const [selectedTask, setSelectedTask] = useState(tasks[0]?.id || "");
    const [customTask, setCustomTask] = useState("");
    const [launching, setLaunching] = useState(false);
    const [step, setStep] = useState(-1);
    const [done, setDone] = useState(false);
    const [serverMessage, setServerMessage] = useState(null);
    const [runtimeCards, setRuntimeCards] = useState([]);
    const [runtimeError, setRuntimeError] = useState(null);
    const [permissionMode, setPermissionMode] = useState("standard");
    const runtimes = [
        {
            id: "claude-code",
            label: "Claude Code",
            sub: "native hooks · shared kernel",
            cmd: "dhee install --harness claude-code",
        },
        {
            id: "codex",
            label: "Codex",
            sub: "config.toml · stream sync",
            cmd: "dhee install --harness codex",
        },
        {
            id: "both",
            label: "Both",
            sub: "one shared memory kernel",
            cmd: "dhee install --harness all",
        },
    ];
    const taskTitle = (tasks.find((t) => t.id === selectedTask)?.title || customTask).trim();
    const contextMemories = memories
        .filter((m) => m.tier === "canonical" || m.tier === "high")
        .slice(0, 4);
    const contextTokens = contextMemories.reduce((a, m) => a + m.tokens, 0);
    const liveSessions = workspaceGraph?.sessions?.slice(0, 4) || [];
    const workspaces = projectIndex?.workspaces?.map((workspace) => ({
        id: workspace.id,
        label: workspace.label || workspace.name,
    })) || [];
    const [selectedWorkspace, setSelectedWorkspace] = useState(selectedWorkspaceId || workspaces[0]?.id || "");
    const selectedRuntimeCards = useMemo(() => {
        if (runtime === "both")
            return runtimeCards;
        return runtimeCards.filter((card) => card.id === runtime);
    }, [runtime, runtimeCards]);
    useEffect(() => {
        (async () => {
            try {
                const snapshot = await api.runtimeStatus();
                setRuntimeCards(snapshot.runtimes || []);
                setRuntimeError(snapshot.error || null);
            }
            catch (e) {
                setRuntimeError(String(e));
            }
        })();
    }, []);
    const steps = [
        "Initialising Dhee kernel…",
        `Assembling context slice (${contextMemories.length} memories, ~${contextTokens} tokens)…`,
        `Loading samskara log · ${memories.length} engrams indexed…`,
        `Starting ${runtime === "both"
            ? "Claude Code + Codex"
            : runtimes.find((r) => r.id === runtime)?.label} harness…`,
        "Memory hooks active · router enforcement on…",
        "✓ Ready.",
    ];
    const launch = async () => {
        if (!taskTitle)
            return;
        setLaunching(true);
        setStep(0);
        setDone(false);
        try {
            const workspaceId = selectedWorkspace || selectedWorkspaceId || workspaces[0]?.id;
            if (!workspaceId)
                throw new Error("No workspace selected");
            const res = await api.launchWorkspaceSession(workspaceId, runtime, taskTitle, runtime === "claude-code" ? permissionMode : undefined, selectedTask || undefined);
            setServerMessage(`${res.control_state} · ${res.launch_command}`);
            const snapshot = await api.runtimeStatus().catch(() => null);
            if (snapshot?.runtimes)
                setRuntimeCards(snapshot.runtimes);
            onLaunched?.(res.task_id || selectedTask || null, runtime);
            setDone(true);
        }
        catch (e) {
            setServerMessage(String(e));
            setDone(false);
        }
        finally {
            setLaunching(false);
        }
    };
    return (_jsxs("div", { style: { display: "flex", flexDirection: "column", height: "100%" }, children: [_jsxs("div", { style: {
                    borderBottom: "1px solid var(--border)",
                    padding: "0 24px",
                    height: 48,
                    display: "flex",
                    alignItems: "center",
                    gap: 10,
                    flexShrink: 0,
                }, children: [_jsx("span", { style: {
                            fontFamily: "var(--mono)",
                            fontSize: 11,
                            fontWeight: 700,
                            letterSpacing: "0.06em",
                        }, children: "LAUNCH" }), _jsx("span", { style: {
                            fontFamily: "var(--mono)",
                            fontSize: 10,
                            color: "var(--ink3)",
                        }, children: "no middle-man \u00B7 claude code orchestrates \u00B7 dhee is the substrate" })] }), _jsx("div", { style: { flex: 1, overflowY: "auto", padding: "28px 24px" }, children: !launching ? (_jsxs("div", { style: { maxWidth: 640 }, children: [_jsxs("div", { style: { marginBottom: 28 }, children: [_jsx(SectionHeader, { label: "Workspace" }), _jsx("select", { value: selectedWorkspace, onChange: (e) => setSelectedWorkspace(e.target.value), style: {
                                        width: "100%",
                                        border: "1px solid var(--border)",
                                        padding: "10px 12px",
                                        fontFamily: "var(--mono)",
                                        fontSize: 11,
                                        marginBottom: 14,
                                    }, children: workspaces.map((workspace) => (_jsx("option", { value: workspace.id, children: workspace.label }, workspace.id))) })] }), _jsxs("div", { style: { marginBottom: 28 }, children: [_jsx(SectionHeader, { label: "Runtime" }), _jsx("div", { style: { display: "flex", gap: 10 }, children: runtimes.map((r) => (_jsxs("div", { onClick: () => setRuntime(r.id), style: {
                                            flex: 1,
                                            padding: "14px",
                                            border: `1.5px solid ${runtime === r.id ? "var(--accent)" : "var(--border)"}`,
                                            cursor: "pointer",
                                            background: runtime === r.id ? "oklch(0.97 0.04 36)" : "white",
                                            transition: "all 0.12s",
                                        }, children: [_jsx("div", { style: {
                                                    fontWeight: 600,
                                                    fontSize: 14,
                                                    marginBottom: 4,
                                                }, children: r.label }), _jsx("div", { style: {
                                                    fontFamily: "var(--mono)",
                                                    fontSize: 10,
                                                    color: "var(--ink3)",
                                                    marginBottom: 8,
                                                }, children: r.sub }), _jsx("div", { style: {
                                                    fontFamily: "var(--mono)",
                                                    fontSize: 9,
                                                    color: "var(--ink3)",
                                                }, children: r.cmd })] }, r.id))) }), _jsxs("div", { style: { marginTop: 14, display: "grid", gap: 10 }, children: [selectedRuntimeCards.map((card) => (_jsxs("div", { style: {
                                                border: "1px solid var(--border)",
                                                padding: "12px 14px",
                                                background: "white",
                                            }, children: [_jsxs("div", { style: {
                                                        display: "flex",
                                                        justifyContent: "space-between",
                                                        alignItems: "center",
                                                        gap: 12,
                                                        marginBottom: 8,
                                                    }, children: [_jsx("div", { style: { fontSize: 13, fontWeight: 600 }, children: card.label }), _jsx(StatPill, { label: card.installed ? "attached" : "not attached", tone: card.installed ? "var(--green)" : "var(--rose)" })] }), _jsx("div", { style: { fontSize: 12, color: "var(--ink2)", marginBottom: 5 }, children: card.currentSession?.title || card.currentSession?.cwd || "No active session in this repo" }), _jsxs("div", { style: { fontFamily: "var(--mono)", fontSize: 9, color: "var(--ink3)" }, children: ["limit: ", card.limits.state, card.limits.resetAt ? ` · reset ${new Date(card.limits.resetAt).toLocaleString()}` : ""] })] }, card.id))), runtimeError && (_jsx("div", { style: { fontSize: 12, color: "var(--rose)" }, children: runtimeError }))] }), runtime === "claude-code" && (_jsxs("div", { style: { marginTop: 14 }, children: [_jsx(SectionHeader, { label: "Claude Permissions", sub: "full access is explicit and visible" }), _jsx("div", { style: { display: "flex", gap: 10 }, children: [
                                                ["standard", "standard permissions"],
                                                ["full-access", "full access"],
                                            ].map(([id, label]) => (_jsx("button", { onClick: () => setPermissionMode(id), style: {
                                                    padding: "8px 12px",
                                                    border: `1px solid ${permissionMode === id ? "var(--accent)" : "var(--border)"}`,
                                                    background: permissionMode === id ? "oklch(0.97 0.04 36)" : "white",
                                                    fontFamily: "var(--mono)",
                                                    fontSize: 10,
                                                    color: permissionMode === id ? "var(--accent)" : "var(--ink2)",
                                                }, children: label }, id))) })] }))] }), _jsxs("div", { style: { marginBottom: 28 }, children: [_jsx(SectionHeader, { label: "Task" }), _jsx("div", { style: { marginBottom: 8 }, children: tasks.map((t) => {
                                        const c = {
                                            green: "var(--green)",
                                            indigo: "var(--indigo)",
                                            orange: "var(--accent)",
                                            rose: "var(--rose)",
                                        }[t.color] || "var(--green)";
                                        return (_jsxs("div", { onClick: () => {
                                                setSelectedTask(t.id);
                                                setCustomTask("");
                                            }, style: {
                                                padding: "10px 14px",
                                                marginBottom: 4,
                                                border: `1px solid ${selectedTask === t.id
                                                    ? "var(--accent)"
                                                    : "var(--border)"}`,
                                                cursor: "pointer",
                                                display: "flex",
                                                gap: 10,
                                                alignItems: "center",
                                                background: selectedTask === t.id
                                                    ? "oklch(0.97 0.04 36)"
                                                    : "white",
                                            }, children: [_jsx("div", { style: {
                                                        width: 7,
                                                        height: 7,
                                                        background: c,
                                                        flexShrink: 0,
                                                    } }), _jsx("span", { style: { fontSize: 13, flex: 1 }, children: t.title }), selectedTask === t.id && (_jsx("span", { style: {
                                                        fontFamily: "var(--mono)",
                                                        fontSize: 9,
                                                        color: "var(--accent)",
                                                    }, children: "SELECTED" }))] }, t.id));
                                    }) }), _jsx("div", { style: {
                                        fontFamily: "var(--mono)",
                                        fontSize: 9,
                                        color: "var(--ink3)",
                                        marginBottom: 5,
                                    }, children: "OR NEW TASK:" }), _jsx("textarea", { value: customTask, onChange: (e) => {
                                        setCustomTask(e.target.value);
                                        setSelectedTask("");
                                    }, placeholder: "Describe the task\u2026", rows: 2, style: {
                                        width: "100%",
                                        border: "1px solid var(--border)",
                                        padding: "10px",
                                        fontFamily: "var(--font)",
                                        fontSize: 13,
                                        color: "var(--ink)",
                                        background: "white",
                                        resize: "none",
                                        outline: "none",
                                    } })] }), contextMemories.length > 0 && (_jsxs("div", { style: { marginBottom: 28 }, children: [_jsx(SectionHeader, { label: "Memory Context Preview", sub: `~${contextTokens} tokens · Dhee will inject before launch` }), _jsx("div", { style: { border: "1px solid var(--border)", background: "white" }, children: contextMemories.map((m, i) => (_jsxs("div", { style: {
                                            padding: "10px 14px",
                                            borderBottom: i < contextMemories.length - 1
                                                ? "1px solid var(--surface2)"
                                                : "none",
                                            display: "flex",
                                            gap: 10,
                                            alignItems: "flex-start",
                                        }, children: [_jsx(TierBadge, { tier: m.tier }), _jsxs("span", { style: {
                                                    fontSize: 12.5,
                                                    color: "var(--ink2)",
                                                    lineHeight: 1.4,
                                                }, children: [m.content.slice(0, 90), m.content.length > 90 ? "…" : ""] })] }, m.id))) })] })), liveSessions.length > 0 && (_jsxs("div", { style: { marginBottom: 28 }, children: [_jsx(SectionHeader, { label: "Live Repo Sessions", sub: "current Codex work already visible to the shared canvas" }), _jsx("div", { style: { border: "1px solid var(--border)", background: "white" }, children: liveSessions.map((session, index) => (_jsxs("div", { style: {
                                            padding: "11px 14px",
                                            borderBottom: index < liveSessions.length - 1
                                                ? "1px solid var(--surface2)"
                                                : "none",
                                        }, children: [_jsxs("div", { style: {
                                                    display: "flex",
                                                    justifyContent: "space-between",
                                                    gap: 12,
                                                    marginBottom: 4,
                                                }, children: [_jsx("span", { style: { fontSize: 12.5, fontWeight: 600 }, children: session.title }), session.isCurrent && (_jsx(StatPill, { label: "current", tone: "var(--green)" }))] }), _jsx("div", { style: { fontSize: 12, color: "var(--ink2)", lineHeight: 1.45 }, children: session.preview || "No preview yet." })] }, session.id))) })] })), _jsxs("button", { onClick: launch, disabled: !taskTitle, style: {
                                width: "100%",
                                padding: "16px",
                                background: taskTitle ? "var(--ink)" : "var(--surface2)",
                                color: taskTitle ? "var(--bg)" : "var(--ink3)",
                                fontFamily: "var(--mono)",
                                fontSize: 13,
                                fontWeight: 700,
                                letterSpacing: "0.06em",
                                cursor: taskTitle ? "pointer" : "not-allowed",
                                transition: "background 0.15s",
                            }, onMouseEnter: (e) => {
                                if (taskTitle)
                                    e.currentTarget.style.background = "var(--accent)";
                            }, onMouseLeave: (e) => {
                                if (taskTitle)
                                    e.currentTarget.style.background = "var(--ink)";
                            }, children: ["LAUNCH WITH", " ", runtimes.find((r) => r.id === runtime)?.label.toUpperCase(), " \u2192"] })] })) : (_jsxs("div", { style: { maxWidth: 540 }, children: [_jsx("div", { style: {
                                fontFamily: "var(--mono)",
                                fontSize: 9,
                                color: "var(--ink3)",
                                letterSpacing: "0.08em",
                                marginBottom: 20,
                            }, children: "INITIALISING HARNESS" }), steps.map((s, i) => (_jsxs("div", { style: {
                                display: "flex",
                                gap: 12,
                                padding: "7px 0",
                                opacity: i <= step ? 1 : 0.18,
                                transition: "opacity 0.25s",
                            }, children: [_jsx("span", { style: {
                                        fontFamily: "var(--mono)",
                                        fontSize: 13,
                                        color: i < step
                                            ? "var(--green)"
                                            : i === step
                                                ? "var(--accent)"
                                                : "var(--ink3)",
                                        flexShrink: 0,
                                        width: 16,
                                    }, children: i < step ? "✓" : i === step ? "›" : "·" }), _jsx("span", { style: {
                                        fontFamily: "var(--mono)",
                                        fontSize: 13,
                                        color: i === step ? "var(--ink)" : "var(--ink2)",
                                    }, children: s })] }, i))), done && (_jsxs("div", { style: {
                                marginTop: 24,
                                padding: "14px 18px",
                                border: "1px solid var(--green)",
                                background: "oklch(0.96 0.06 145)",
                            }, children: [_jsx("div", { style: {
                                        fontFamily: "var(--mono)",
                                        fontSize: 12,
                                        color: "var(--green)",
                                        marginBottom: 4,
                                    }, children: "\u2713 HARNESS ACTIVE" }), _jsx("div", { style: { fontSize: 12, color: "var(--ink2)" }, children: serverMessage ||
                                        "Memory hooks live. Switching to workspace…" })] }))] })) })] }));
}
