import { jsx as _jsx, jsxs as _jsxs } from "react/jsx-runtime";
export function TasksView({ tasks, projectIndex, onSelectTask, onSelectSession, tweaks, }) {
    const colorMap = {
        green: "var(--green)",
        indigo: "var(--indigo)",
        orange: "var(--accent)",
        rose: "var(--rose)",
    };
    const currentWorkspace = projectIndex?.workspaces?.find((workspace) => workspace.id === projectIndex?.currentWorkspaceId) ||
        projectIndex?.workspaces?.[0] ||
        null;
    const currentProject = currentWorkspace?.projects?.find((project) => project.id === projectIndex?.currentProjectId) ||
        currentWorkspace?.projects?.[0] ||
        null;
    const currentSession = currentProject?.sessions?.find((session) => session.id === projectIndex?.currentSessionId) ||
        currentProject?.sessions?.[0] ||
        currentWorkspace?.sessions?.find((session) => session.id === projectIndex?.currentSessionId) ||
        currentWorkspace?.sessions?.[0] ||
        null;
    const liveTask = tasks.find((task) => task.id === currentSession?.taskId) || tasks[0] || null;
    const history = tasks.filter((task) => task.id !== liveTask?.id);
    const renderTaskRow = (task) => (_jsxs("button", { onClick: () => onSelectTask(task.id), style: {
            display: "grid",
            gridTemplateColumns: "12px minmax(0, 1fr) 18px",
            alignItems: "center",
            gap: 14,
            padding: "14px 0",
            borderBottom: "1px solid var(--border)",
            textAlign: "left",
            background: "transparent",
        }, children: [_jsx("span", { style: {
                    width: 10,
                    height: 10,
                    background: colorMap[task.color] || "var(--accent)",
                    display: "inline-block",
                } }), _jsxs("span", { style: { minWidth: 0 }, children: [_jsx("div", { style: {
                            fontSize: 16,
                            fontWeight: 560,
                            overflow: "hidden",
                            textOverflow: "ellipsis",
                            whiteSpace: "nowrap",
                        }, children: task.title }), _jsxs("div", { style: {
                            marginTop: 4,
                            fontFamily: "var(--mono)",
                            fontSize: 10,
                            color: "var(--ink3)",
                            display: "flex",
                            gap: 10,
                            flexWrap: "wrap",
                        }, children: [tweaks.showTimestamps && _jsx("span", { children: task.created }), _jsxs("span", { children: [task.messages.length, " msgs"] }), task.harness && _jsx("span", { children: task.harness })] })] }), _jsx("span", { style: { color: "var(--ink3)", fontSize: 18 }, children: "\u2192" })] }, task.id));
    return (_jsxs("div", { style: { height: "100%", display: "flex", flexDirection: "column", overflow: "hidden" }, children: [_jsxs("div", { style: {
                    height: 48,
                    borderBottom: "1px solid var(--border)",
                    padding: "0 24px",
                    display: "flex",
                    alignItems: "center",
                    justifyContent: "space-between",
                    flexShrink: 0,
                }, children: [_jsx("div", { style: { fontFamily: "var(--mono)", fontSize: 11, color: "var(--ink3)", letterSpacing: "0.08em" }, children: "TASKS" }), _jsxs("div", { style: { fontFamily: "var(--mono)", fontSize: 10, color: "var(--ink3)" }, children: [tasks.length, " tracked tasks"] })] }), _jsxs("div", { style: { flex: 1, overflow: "auto", padding: 24, display: "grid", gap: 20 }, children: [currentSession && (_jsxs("div", { onClick: () => onSelectSession(currentSession.id, currentSession.taskId || null), style: {
                            border: "1px solid var(--green)",
                            background: "white",
                            padding: 18,
                            cursor: "pointer",
                        }, children: [_jsxs("div", { style: { display: "flex", alignItems: "center", gap: 8, marginBottom: 10, flexWrap: "wrap" }, children: [_jsx("span", { style: { width: 9, height: 9, background: "var(--green)", display: "inline-block" } }), _jsx("span", { style: { fontFamily: "var(--mono)", fontSize: 10, color: "var(--ink3)" }, children: "LIVE TASK" }), _jsx("span", { style: { fontFamily: "var(--mono)", fontSize: 10, color: "var(--accent)" }, children: currentSession.runtime })] }), _jsx("div", { style: { fontSize: 24, fontWeight: 650, lineHeight: 1.2 }, children: liveTask?.title || currentSession.title }), _jsx("div", { style: { marginTop: 8, fontSize: 13, color: "var(--ink2)", lineHeight: 1.5 }, children: currentSession.preview || "Current mirrored session is ready to continue." })] })), _jsxs("div", { style: { border: "1px solid var(--border)", background: "white", padding: 18 }, children: [_jsx("div", { style: { marginBottom: 12, fontFamily: "var(--mono)", fontSize: 10, color: "var(--ink3)" }, children: "TASK HISTORY" }), _jsxs("div", { style: { display: "grid", gap: 0 }, children: [history.length === 0 && (_jsx("div", { style: {
                                            padding: "36px 0",
                                            textAlign: "center",
                                            fontFamily: "var(--mono)",
                                            fontSize: 11,
                                            color: "var(--ink3)",
                                        }, children: "No task history yet." })), history.map(renderTaskRow)] })] })] })] }));
}
