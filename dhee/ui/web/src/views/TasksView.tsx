import type { ProjectIndexSnapshot, SankhyaTask, TaskColor, Tweaks } from "../types";

export function TasksView({
  tasks,
  projectIndex,
  onSelectTask,
  onSelectSession,
  tweaks,
}: {
  tasks: SankhyaTask[];
  projectIndex?: ProjectIndexSnapshot | null;
  onSelectTask: (id: string) => void;
  onSelectSession: (sessionId: string, taskId?: string | null) => void;
  tweaks: Tweaks;
}) {
  const colorMap: Record<TaskColor, string> = {
    green: "var(--green)",
    indigo: "var(--indigo)",
    orange: "var(--accent)",
    rose: "var(--rose)",
  };

  const currentWorkspace =
    projectIndex?.workspaces?.find((workspace) => workspace.id === projectIndex?.currentWorkspaceId) ||
    projectIndex?.workspaces?.[0] ||
    null;
  const currentProject =
    currentWorkspace?.projects?.find((project) => project.id === projectIndex?.currentProjectId) ||
    currentWorkspace?.projects?.[0] ||
    null;
  const currentSession =
    currentProject?.sessions?.find((session) => session.id === projectIndex?.currentSessionId) ||
    currentProject?.sessions?.[0] ||
    currentWorkspace?.sessions?.find((session) => session.id === projectIndex?.currentSessionId) ||
    currentWorkspace?.sessions?.[0] ||
    null;

  const liveTask = tasks.find((task) => task.id === currentSession?.taskId) || tasks[0] || null;
  const history = tasks.filter((task) => task.id !== liveTask?.id);

  const renderTaskRow = (task: SankhyaTask) => (
    <button
      key={task.id}
      onClick={() => onSelectTask(task.id)}
      style={{
        display: "grid",
        gridTemplateColumns: "12px minmax(0, 1fr) 18px",
        alignItems: "center",
        gap: 14,
        padding: "14px 0",
        borderBottom: "1px solid var(--border)",
        textAlign: "left",
        background: "transparent",
      }}
    >
      <span
        style={{
          width: 10,
          height: 10,
          background: colorMap[task.color] || "var(--accent)",
          display: "inline-block",
        }}
      />
      <span style={{ minWidth: 0 }}>
        <div
          style={{
            fontSize: 16,
            fontWeight: 560,
            overflow: "hidden",
            textOverflow: "ellipsis",
            whiteSpace: "nowrap",
          }}
        >
          {task.title}
        </div>
        <div
          style={{
            marginTop: 4,
            fontFamily: "var(--mono)",
            fontSize: 10,
            color: "var(--ink3)",
            display: "flex",
            gap: 10,
            flexWrap: "wrap",
          }}
        >
          {tweaks.showTimestamps && <span>{task.created}</span>}
          <span>{task.messages.length} msgs</span>
          {task.harness && <span>{task.harness}</span>}
        </div>
      </span>
      <span style={{ color: "var(--ink3)", fontSize: 18 }}>→</span>
    </button>
  );

  return (
    <div style={{ height: "100%", display: "flex", flexDirection: "column", overflow: "hidden" }}>
      <div
        style={{
          height: 48,
          borderBottom: "1px solid var(--border)",
          padding: "0 24px",
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          flexShrink: 0,
        }}
      >
        <div style={{ fontFamily: "var(--mono)", fontSize: 11, color: "var(--ink3)", letterSpacing: "0.08em" }}>
          TASKS
        </div>
        <div style={{ fontFamily: "var(--mono)", fontSize: 10, color: "var(--ink3)" }}>
          {tasks.length} tracked tasks
        </div>
      </div>

      <div style={{ flex: 1, overflow: "auto", padding: 24, display: "grid", gap: 20 }}>
        {currentSession && (
          <div
            onClick={() => onSelectSession(currentSession.id, currentSession.taskId || null)}
            style={{
              border: "1px solid var(--green)",
              background: "white",
              padding: 18,
              cursor: "pointer",
            }}
          >
            <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 10, flexWrap: "wrap" }}>
              <span style={{ width: 9, height: 9, background: "var(--green)", display: "inline-block" }} />
              <span style={{ fontFamily: "var(--mono)", fontSize: 10, color: "var(--ink3)" }}>LIVE TASK</span>
              <span style={{ fontFamily: "var(--mono)", fontSize: 10, color: "var(--accent)" }}>
                {currentSession.runtime}
              </span>
            </div>
            <div style={{ fontSize: 24, fontWeight: 650, lineHeight: 1.2 }}>
              {liveTask?.title || currentSession.title}
            </div>
            <div style={{ marginTop: 8, fontSize: 13, color: "var(--ink2)", lineHeight: 1.5 }}>
              {currentSession.preview || "Current mirrored session is ready to continue."}
            </div>
          </div>
        )}

        <div style={{ border: "1px solid var(--border)", background: "white", padding: 18 }}>
          <div style={{ marginBottom: 12, fontFamily: "var(--mono)", fontSize: 10, color: "var(--ink3)" }}>
            TASK HISTORY
          </div>
          <div style={{ display: "grid", gap: 0 }}>
            {history.length === 0 && (
              <div
                style={{
                  padding: "36px 0",
                  textAlign: "center",
                  fontFamily: "var(--mono)",
                  fontSize: 11,
                  color: "var(--ink3)",
                }}
              >
                No task history yet.
              </div>
            )}
            {history.map(renderTaskRow)}
          </div>
        </div>
      </div>
    </div>
  );
}
