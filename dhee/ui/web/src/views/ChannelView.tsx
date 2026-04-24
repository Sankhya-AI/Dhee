import { useEffect, useMemo, useState } from "react";
import { AssetDrawer } from "../components/AssetDrawer";
import {
  ConnectedAgents,
  LineComposer,
  LineMessageCard,
  useWorkspaceLine,
} from "../components/LinePanel";
import { StatPill } from "../components/ui/StatPill";
import type {
  ProjectIndexSnapshot,
  ProjectSummary,
  SankhyaTask,
  Tweaks,
  WorkspaceGraphSnapshot,
  WorkspaceSummary,
} from "../types";

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

function countSuggestedTasks(
  workspace: WorkspaceSummary | null,
  tasks: SankhyaTask[],
): SankhyaTask[] {
  if (!workspace) return [];
  const workspaceProjectIds = new Set(workspace.projects.map((project) => project.id));
  return tasks.filter((task) => {
    const source = String(task.source || "").toLowerCase();
    if (source !== "broadcast" && !source.includes("suggested")) return false;
    const projectId = (task as unknown as { project_id?: string }).project_id;
    return !projectId || workspaceProjectIds.has(String(projectId));
  });
}

export function ChannelView({
  projectIndex,
  workspaceGraph,
  tasks,
  selectedWorkspaceId,
  selectedProjectId,
  onSelectWorkspace,
  onSelectProject,
  onSelectTask,
  onTasksRefresh,
  onOpenCanvas,
  onLaunchSession,
  onOpenManager,
}: {
  projectIndex?: ProjectIndexSnapshot | null;
  workspaceGraph?: WorkspaceGraphSnapshot | null;
  tasks: SankhyaTask[];
  selectedWorkspaceId: string;
  selectedProjectId: string;
  onSelectWorkspace: (workspaceId: string) => void;
  onSelectProject: (projectId: string, workspaceId?: string | null) => void;
  onSelectTask: (taskId: string) => void;
  onTasksRefresh: () => Promise<void> | void;
  onOpenCanvas: () => void;
  onLaunchSession: (
    title: string,
    runtime: "claude-code" | "codex",
    workspaceId?: string,
    permissionMode?: "standard" | "full-access",
    projectId?: string,
  ) => Promise<void> | void;
  onOpenManager?: (tab?: "workspaces" | "projects") => void;
  tweaks: Tweaks;
}) {
  const workspaces = projectIndex?.workspaces || [];

  // Local state for kind filters so operators can scope the feed.
  const [kindFilter, setKindFilter] = useState<"all" | "broadcast" | "tool" | "note">("all");

  const currentWorkspace: WorkspaceSummary | null = useMemo(() => {
    return (
      workspaces.find((workspace) => workspace.id === selectedWorkspaceId) ||
      workspaces.find((workspace) => workspace.id === projectIndex?.currentWorkspaceId) ||
      workspaces[0] ||
      workspaceGraph?.workspace ||
      null
    );
  }, [workspaces, selectedWorkspaceId, projectIndex?.currentWorkspaceId, workspaceGraph]);

  const currentProject: ProjectSummary | null = useMemo(() => {
    if (!currentWorkspace) return null;
    return (
      currentWorkspace.projects.find((project) => project.id === selectedProjectId) ||
      currentWorkspace.projects.find((project) => project.id === projectIndex?.currentProjectId) ||
      null
    );
  }, [currentWorkspace, selectedProjectId, projectIndex?.currentProjectId]);

  const workspaceSessions = currentWorkspace?.sessions || [];
  const activeSession = useMemo(() => {
    const currentId = projectIndex?.currentSessionId;
    if (!currentWorkspace) return null;
    const inProject = currentProject?.sessions?.find((session) => session.id === currentId) ||
      currentProject?.sessions?.[0];
    if (inProject) return inProject;
    return (
      workspaceSessions.find((session) => session.id === currentId) || workspaceSessions[0] || null
    );
  }, [currentWorkspace, currentProject, projectIndex?.currentSessionId, workspaceSessions]);

  const { messages, live, error, refresh } = useWorkspaceLine(
    currentWorkspace?.id,
    currentProject?.id,
  );

  // Apply the kind filter after merge so user-facing counts are honest.
  const filtered = useMemo(() => {
    if (kindFilter === "all") return messages;
    return messages.filter((message) => {
      const kind = String(message.message_kind || "").toLowerCase();
      if (kindFilter === "broadcast") return kind === "broadcast";
      if (kindFilter === "tool") return kind.startsWith("tool.");
      if (kindFilter === "note") return kind === "note" || kind === "update";
      return true;
    });
  }, [messages, kindFilter]);

  const suggestedTasks = useMemo(
    () => countSuggestedTasks(currentWorkspace, tasks),
    [currentWorkspace, tasks],
  );

  // Auto-scroll the feed to top when a new message arrives (new messages
  // are at the head of the sorted list).
  const [latestId, setLatestId] = useState<string | null>(null);
  useEffect(() => {
    const head = messages[0]?.id;
    if (head && head !== latestId) setLatestId(head);
  }, [messages, latestId]);

  const chipButton = (active: boolean): React.CSSProperties => ({
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

  const navChipStyle = (active: boolean): React.CSSProperties => ({
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

  return (
    <div style={{ height: "100%", display: "flex", flexDirection: "column", overflow: "hidden" }}>
      {/* Header */}
      <div
        style={{
          height: 48,
          borderBottom: "1px solid var(--border)",
          padding: "0 20px",
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          flexShrink: 0,
        }}
      >
        <div style={{ display: "flex", alignItems: "center", gap: 10, minWidth: 0 }}>
          <span style={{ fontFamily: "var(--mono)", fontSize: 10, color: "var(--ink3)" }}>
            {currentWorkspace?.label || currentWorkspace?.name || "channel"}
            {currentProject ? ` / ${currentProject.name}` : ""}
          </span>
          <StatPill label={live ? "live" : "offline"} tone={live ? "var(--green)" : "var(--ink3)"} />
          <span style={{ fontFamily: "var(--mono)", fontSize: 10, color: "var(--ink3)" }}>
            {filtered.length} events · {suggestedTasks.length} suggested tasks
          </span>
        </div>
        <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
          <button
            onClick={onOpenCanvas}
            style={{
              padding: "6px 12px",
              border: "1px solid var(--border)",
              fontFamily: "var(--mono)",
              fontSize: 9,
              color: "var(--ink2)",
              background: "white",
              cursor: "pointer",
              letterSpacing: 0.4,
            }}
          >
            open canvas
          </button>
          <button
            onClick={() => void refresh()}
            style={{
              padding: "6px 12px",
              border: "1px solid var(--border)",
              fontFamily: "var(--mono)",
              fontSize: 9,
              color: "var(--ink2)",
              background: "white",
              cursor: "pointer",
              letterSpacing: 0.4,
            }}
          >
            refresh
          </button>
        </div>
      </div>

      {/* Body grid */}
      <div
        style={{
          flex: 1,
          display: "grid",
          gridTemplateColumns: "260px minmax(0, 1fr) 360px",
          overflow: "hidden",
        }}
      >
        {/* Left rail */}
        <div
          style={{
            borderRight: "1px solid var(--border)",
            padding: 16,
            overflowY: "auto",
            display: "flex",
            flexDirection: "column",
            gap: 14,
          }}
        >
          <div>
            <div
              style={{
                display: "flex",
                justifyContent: "space-between",
                alignItems: "center",
                marginBottom: 8,
                gap: 8,
              }}
            >
              <span
                style={{
                  fontFamily: "var(--mono)",
                  fontSize: 9,
                  letterSpacing: 0.6,
                  color: "var(--ink3)",
                  textTransform: "uppercase",
                }}
              >
                Workspace
              </span>
              {onOpenManager ? (
                <button
                  onClick={() => onOpenManager("workspaces")}
                  title="Manage workspaces"
                  style={{
                    padding: "3px 7px",
                    border: "1px solid var(--border)",
                    background: "white",
                    fontFamily: "var(--mono)",
                    fontSize: 9,
                    letterSpacing: 0.4,
                    color: "var(--ink3)",
                    cursor: "pointer",
                  }}
                >
                  + new / manage
                </button>
              ) : null}
            </div>
            {workspaces.length === 0 ? (
              <button
                onClick={() => onOpenManager?.("workspaces")}
                style={{
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
                }}
              >
                Create your first workspace → e.g. Office, Personal, Sankhya AI Labs.
              </button>
            ) : (
              <select
                value={currentWorkspace?.id || ""}
                onChange={(e) => onSelectWorkspace(e.target.value)}
                style={{
                  width: "100%",
                  padding: "9px 10px",
                  border: "1px solid var(--border)",
                  background: "white",
                  fontSize: 12,
                }}
              >
                {workspaces.map((workspace) => (
                  <option key={workspace.id} value={workspace.id}>
                    {workspace.label || workspace.name}
                  </option>
                ))}
              </select>
            )}
          </div>

          <div>
            <div
              style={{
                display: "flex",
                justifyContent: "space-between",
                alignItems: "center",
                marginBottom: 8,
                gap: 8,
              }}
            >
              <span
                style={{
                  fontFamily: "var(--mono)",
                  fontSize: 9,
                  letterSpacing: 0.6,
                  color: "var(--ink3)",
                  textTransform: "uppercase",
                }}
              >
                Projects · {currentWorkspace?.projects?.length || 0}
              </span>
              {onOpenManager && currentWorkspace ? (
                <button
                  onClick={() => onOpenManager("projects")}
                  title="Add or edit projects"
                  style={{
                    padding: "3px 7px",
                    border: "1px solid var(--border)",
                    background: "white",
                    fontFamily: "var(--mono)",
                    fontSize: 9,
                    letterSpacing: 0.4,
                    color: "var(--ink3)",
                    cursor: "pointer",
                  }}
                >
                  + project
                </button>
              ) : null}
            </div>
            <div style={{ display: "grid", gap: 6 }}>
              <button
                onClick={() => onSelectProject("", currentWorkspace?.id)}
                style={navChipStyle(!currentProject)}
              >
                <span>All projects (workspace line)</span>
                <span style={{ fontFamily: "var(--mono)", fontSize: 9, color: "var(--ink3)" }}>
                  {workspaceSessions.length}
                </span>
              </button>
              {(currentWorkspace?.projects || []).map((project) => {
                const active = project.id === currentProject?.id;
                const sessionCount = project.sessions?.length || 0;
                return (
                  <button
                    key={project.id}
                    onClick={() => onSelectProject(project.id, currentWorkspace?.id)}
                    style={navChipStyle(active)}
                  >
                    <span>{project.name}</span>
                    <span style={{ fontFamily: "var(--mono)", fontSize: 9, color: "var(--ink3)" }}>
                      {sessionCount}
                    </span>
                  </button>
                );
              })}
            </div>
          </div>

          <ConnectedAgents
            workspace={currentWorkspace}
            projects={currentWorkspace?.projects || []}
            workspaceSessions={workspaceSessions}
          />

          {currentWorkspace && (
            <div
              style={{
                display: "flex",
                flexDirection: "column",
                gap: 6,
              }}
            >
              <span
                style={{
                  fontFamily: "var(--mono)",
                  fontSize: 9,
                  letterSpacing: 0.6,
                  color: "var(--ink3)",
                  textTransform: "uppercase",
                }}
              >
                Launch
              </span>
              <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
                <button
                  onClick={() =>
                    void onLaunchSession(
                      "channel session",
                      "codex",
                      currentWorkspace.id,
                      undefined,
                      currentProject?.id,
                    )
                  }
                  style={chipButton(false)}
                >
                  + codex
                </button>
                <button
                  onClick={() =>
                    void onLaunchSession(
                      "channel session",
                      "claude-code",
                      currentWorkspace.id,
                      "standard",
                      currentProject?.id,
                    )
                  }
                  style={chipButton(false)}
                >
                  + claude
                </button>
              </div>
            </div>
          )}
        </div>

        {/* Center feed */}
        <div
          style={{
            display: "flex",
            flexDirection: "column",
            overflow: "hidden",
            background: "var(--bg)",
          }}
        >
          <div
            style={{
              padding: "12px 20px",
              borderBottom: "1px solid var(--border)",
              display: "flex",
              alignItems: "center",
              gap: 8,
              flexWrap: "wrap",
            }}
          >
            <span
              style={{
                fontFamily: "var(--mono)",
                fontSize: 9,
                letterSpacing: 0.6,
                color: "var(--ink3)",
                textTransform: "uppercase",
              }}
            >
              Shared line
            </span>
            <button onClick={() => setKindFilter("all")} style={chipButton(kindFilter === "all")}>
              all
            </button>
            <button
              onClick={() => setKindFilter("broadcast")}
              style={chipButton(kindFilter === "broadcast")}
            >
              broadcasts
            </button>
            <button onClick={() => setKindFilter("tool")} style={chipButton(kindFilter === "tool")}>
              tool events
            </button>
            <button onClick={() => setKindFilter("note")} style={chipButton(kindFilter === "note")}>
              notes
            </button>
            <span style={{ flex: 1 }} />
            {error ? (
              <span style={{ fontFamily: "var(--mono)", fontSize: 9, color: "var(--rose)" }}>
                {error}
              </span>
            ) : null}
          </div>
          <div
            style={{
              flex: 1,
              overflowY: "auto",
              padding: 20,
              display: "flex",
              flexDirection: "column",
              gap: 10,
            }}
          >
            {filtered.length === 0 ? (
              <div
                style={{
                  padding: 24,
                  border: "1px dashed var(--border)",
                  background: "white",
                  textAlign: "center",
                }}
              >
                <div style={{ fontSize: 13, fontWeight: 600, marginBottom: 6 }}>
                  The line is quiet.
                </div>
                <div
                  style={{
                    fontFamily: "var(--mono)",
                    fontSize: 10,
                    color: "var(--ink3)",
                    lineHeight: 1.55,
                  }}
                >
                  Every agent tool-call in this workspace will appear here. Launch a session from
                  the left rail, or broadcast a note from the composer to get started.
                </div>
              </div>
            ) : (
              filtered.map((message) => (
                <LineMessageCard
                  key={message.id}
                  message={message}
                  workspace={currentWorkspace}
                  onOpenTask={onSelectTask}
                />
              ))
            )}
          </div>
        </div>

        {/* Right rail */}
        <div
          style={{
            borderLeft: "1px solid var(--border)",
            padding: 16,
            overflowY: "auto",
            display: "flex",
            flexDirection: "column",
            gap: 14,
          }}
        >
          <div
            style={{
              fontFamily: "var(--mono)",
              fontSize: 9,
              letterSpacing: 0.6,
              color: "var(--ink3)",
              textTransform: "uppercase",
            }}
          >
            Broadcast
          </div>
          <LineComposer
            workspace={currentWorkspace}
            activeProjectId={currentProject?.id}
            sessionId={activeSession?.id}
            onPublished={async () => {
              await onTasksRefresh();
              void refresh();
            }}
          />

          <AssetDrawer
            workspace={currentWorkspace}
            project={currentProject}
            onActivity={() => void refresh()}
          />

          <div
            style={{
              fontFamily: "var(--mono)",
              fontSize: 9,
              letterSpacing: 0.6,
              color: "var(--ink3)",
              textTransform: "uppercase",
              marginTop: 2,
            }}
          >
            Suggested tasks · {suggestedTasks.length}
          </div>
          <div style={{ display: "grid", gap: 8 }}>
            {suggestedTasks.length === 0 ? (
              <div
                style={{
                  padding: 12,
                  border: "1px dashed var(--border)",
                  fontFamily: "var(--mono)",
                  fontSize: 10,
                  color: "var(--ink3)",
                  lineHeight: 1.55,
                  background: "white",
                }}
              >
                When an agent broadcasts to another project, a task is auto-created there. It will
                show up here.
              </div>
            ) : (
              suggestedTasks.slice(0, 10).map((task) => (
                <button
                  key={task.id}
                  onClick={() => onSelectTask(task.id)}
                  style={{
                    textAlign: "left",
                    padding: "10px 12px",
                    border: "1px solid var(--border)",
                    background: "white",
                    cursor: "pointer",
                    display: "flex",
                    flexDirection: "column",
                    gap: 6,
                  }}
                >
                  <div style={{ fontSize: 12, fontWeight: 600, lineHeight: 1.35 }}>
                    {task.title}
                  </div>
                  <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
                    <StatPill label={task.status || "active"} tone="var(--accent)" />
                    {task.harness ? <StatPill label={String(task.harness)} /> : null}
                  </div>
                </button>
              ))
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
