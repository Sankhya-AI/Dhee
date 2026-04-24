import { useEffect, useMemo, useRef, useState } from "react";
import type { ProjectIndexSnapshot, Tweaks } from "../types";

type NoteMode = "task" | "memory";

export function NotepadView({
  projectIndex,
  memories,
  tokensSaved,
  onAddMemory,
  onSelectSession,
  onCreateWorkspace,
  onLaunchSession,
  onOpenWorkspace,
  onOpenTasks,
}: {
  projectIndex?: ProjectIndexSnapshot | null;
  memories: number;
  tokensSaved: number;
  onAddTask: (title: string) => void;
  onAddMemory: (text: string) => void;
  onSelectSession: (sessionId: string, taskId?: string | null) => void;
  onCreateWorkspace: (name: string) => Promise<void> | void;
  onLaunchSession: (
    title: string,
    runtime: "claude-code" | "codex",
    workspaceId?: string,
    permissionMode?: "standard" | "full-access",
    projectId?: string
  ) => Promise<void> | void;
  onCreateProject: (
    workspaceId: string,
    payload: {
      name: string;
      description?: string;
      default_runtime?: string;
      scope_rules?: { path_prefix: string; label?: string }[];
    }
  ) => Promise<void> | void;
  onOpenWorkspace: () => void;
  onOpenTasks: () => void;
  tweaks: Tweaks;
}) {
  const [draft, setDraft] = useState("");
  const [mode, setMode] = useState<NoteMode>("task");
  const [runtime, setRuntime] = useState<"claude-code" | "codex">("codex");
  const [permissionMode, setPermissionMode] = useState<"standard" | "full-access">("standard");
  const [selectedWorkspaceId, setSelectedWorkspaceId] = useState("");
  const [selectedProjectId, setSelectedProjectId] = useState("");
  const [showWorkspaceModal, setShowWorkspaceModal] = useState(false);
  const [workspaceName, setWorkspaceName] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const inputRef = useRef<HTMLTextAreaElement | null>(null);

  useEffect(() => {
    inputRef.current?.focus();
  }, []);

  const workspaces = projectIndex?.workspaces || [];
  const currentWorkspace = useMemo(
    () =>
      workspaces.find((workspace) => workspace.id === (selectedWorkspaceId || projectIndex?.currentWorkspaceId)) ||
      workspaces[0] ||
      null,
    [projectIndex?.currentWorkspaceId, selectedWorkspaceId, workspaces]
  );

  const currentProject = useMemo(
    () =>
      currentWorkspace?.projects?.find((project) => project.id === (selectedProjectId || projectIndex?.currentProjectId)) ||
      currentWorkspace?.projects?.[0] ||
      null,
    [currentWorkspace, projectIndex?.currentProjectId, selectedProjectId]
  );

  const currentSession = useMemo(
    () =>
      currentProject?.sessions?.find((session) => session.id === projectIndex?.currentSessionId) ||
      currentProject?.sessions?.[0] ||
      currentWorkspace?.sessions?.find((session) => session.id === projectIndex?.currentSessionId) ||
      currentWorkspace?.sessions?.[0] ||
      null,
    [currentProject, currentWorkspace, projectIndex?.currentSessionId]
  );

  useEffect(() => {
    if (!selectedWorkspaceId && projectIndex?.currentWorkspaceId) {
      setSelectedWorkspaceId(projectIndex.currentWorkspaceId);
    }
  }, [projectIndex?.currentWorkspaceId, selectedWorkspaceId]);

  useEffect(() => {
    if (!selectedProjectId && projectIndex?.currentProjectId) {
      setSelectedProjectId(projectIndex.currentProjectId);
    }
  }, [projectIndex?.currentProjectId, selectedProjectId]);

  useEffect(() => {
    if (currentWorkspace && currentProject && !currentWorkspace.projects.some((project) => project.id === selectedProjectId)) {
      setSelectedProjectId(currentProject.id);
    }
  }, [currentWorkspace, currentProject, selectedProjectId]);

  const noteLines = draft
    .split("\n")
    .map((line) => line.trim())
    .filter(Boolean);

  const saveNotes = async () => {
    if (!noteLines.length || busy) return;
    setBusy(true);
    setError(null);
    try {
      if (mode === "memory") {
        for (const line of noteLines) {
          await onAddMemory(line);
        }
      } else {
        for (const line of noteLines) {
          await onLaunchSession(
            line,
            runtime,
            currentWorkspace?.id,
            runtime === "claude-code" ? permissionMode : undefined,
            currentProject?.id
          );
        }
      }
      setDraft("");
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  };

  const createWorkspace = async () => {
    if (!workspaceName.trim() || busy) return;
    setBusy(true);
    setError(null);
    try {
      await onCreateWorkspace(workspaceName.trim());
      setWorkspaceName("");
      setShowWorkspaceModal(false);
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  };

  const chipButton = (active: boolean): React.CSSProperties => ({
    padding: "6px 10px",
    border: "1px solid var(--border)",
    background: active ? "var(--ink)" : "white",
    color: active ? "white" : "var(--ink2)",
    fontFamily: "var(--mono)",
    fontSize: 9,
  });

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
        <div style={{ display: "flex", gap: 10, alignItems: "center", minWidth: 0 }}>
          <span style={{ fontFamily: "var(--mono)", fontSize: 10, color: "var(--ink3)" }}>
            {currentWorkspace?.label || currentWorkspace?.name || "workspace"}
            {currentProject ? ` / ${currentProject.name}` : ""}
          </span>
        </div>
        <div style={{ display: "flex", gap: 18, alignItems: "center", fontFamily: "var(--mono)", fontSize: 10 }}>
          <button onClick={onOpenTasks} style={{ color: "var(--ink3)" }}>
            tasks
          </button>
          <span style={{ color: "var(--ink3)" }}>{memories} engrams</span>
          <span style={{ color: "var(--accent)", fontWeight: 700 }}>
            {tokensSaved.toLocaleString()} tokens saved
          </span>
        </div>
      </div>

      <div
        style={{
          flex: 1,
          overflow: "auto",
          padding: 28,
          display: "grid",
          gridTemplateColumns: "minmax(0, 1fr) 280px",
          gap: 24,
        }}
      >
        <div
          style={{
            border: "1px solid var(--border)",
            background: "transparent",
            padding: 24,
            display: "flex",
            flexDirection: "column",
            minHeight: 0,
          }}
        >
          <div
            style={{
              display: "flex",
              justifyContent: "space-between",
              gap: 12,
              alignItems: "center",
              marginBottom: 16,
              flexWrap: "wrap",
            }}
          >
            <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
              <button onClick={() => setMode("task")} style={chipButton(mode === "task")}>
                create task
              </button>
              <button onClick={() => setMode("memory")} style={chipButton(mode === "memory")}>
                save memory
              </button>
              {mode === "task" && (
                <>
                  <button onClick={() => setRuntime("codex")} style={chipButton(runtime === "codex")}>
                    codex
                  </button>
                  <button onClick={() => setRuntime("claude-code")} style={chipButton(runtime === "claude-code")}>
                    claude-code
                  </button>
                  {runtime === "claude-code" && (
                    <>
                      <button onClick={() => setPermissionMode("standard")} style={chipButton(permissionMode === "standard")}>
                        standard
                      </button>
                      <button onClick={() => setPermissionMode("full-access")} style={chipButton(permissionMode === "full-access")}>
                        full access
                      </button>
                    </>
                  )}
                </>
              )}
            </div>
            <button
              onClick={onOpenWorkspace}
              style={{
                padding: "7px 10px",
                border: "1px solid var(--border)",
                fontFamily: "var(--mono)",
                fontSize: 9,
                color: "var(--ink3)",
              }}
            >
              open workspace
            </button>
          </div>

          {mode === "task" && (
            <div style={{ display: "grid", gap: 10, marginBottom: 14 }}>
              <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
                <span style={{ fontFamily: "var(--mono)", fontSize: 9, color: "var(--ink3)" }}>workspace</span>
                <select
                  value={currentWorkspace?.id || ""}
                  onChange={(e) => setSelectedWorkspaceId(e.target.value)}
                  style={{
                    border: "1px solid var(--border)",
                    padding: "7px 10px",
                    background: "white",
                    minWidth: 220,
                  }}
                >
                  {workspaces.map((workspace) => (
                    <option key={workspace.id} value={workspace.id}>
                      {workspace.label || workspace.name}
                    </option>
                  ))}
                </select>
              </div>
              {currentWorkspace?.projects?.length ? (
                <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
                  <span style={{ fontFamily: "var(--mono)", fontSize: 9, color: "var(--ink3)" }}>project</span>
                  {currentWorkspace.projects.map((project) => (
                    <button
                      key={project.id}
                      onClick={() => setSelectedProjectId(project.id)}
                      style={{
                        ...chipButton(project.id === currentProject?.id),
                        background: project.id === currentProject?.id ? "var(--accent)" : "white",
                      }}
                    >
                      {project.name}
                    </button>
                  ))}
                </div>
              ) : null}
            </div>
          )}

          <textarea
            ref={inputRef}
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            onKeyDown={(e) => {
              if ((e.metaKey || e.ctrlKey) && e.key === "Enter") {
                e.preventDefault();
                void saveNotes();
              }
            }}
            placeholder={
              mode === "task"
                ? "- broadcast backend contract changes\n- compare project scope rules\n- create follow-up task for frontend stream"
                : "- backend project now emits model version updates\n- paper asset is chunked and queryable\n- avoid reprocessing shared results"
            }
            style={{
              width: "100%",
              flex: 1,
              border: "1px solid var(--border)",
              padding: "18px 20px",
              fontSize: 22,
              lineHeight: 1.55,
              background: "white",
              resize: "none",
              minHeight: 420,
            }}
          />

          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginTop: 14 }}>
            <div style={{ fontFamily: "var(--mono)", fontSize: 10, color: "var(--ink3)" }}>
              {noteLines.length} point{noteLines.length === 1 ? "" : "s"}
            </div>
            <button
              onClick={() => void saveNotes()}
              style={{
                padding: "10px 16px",
                border: "1px solid var(--ink)",
                background: "var(--ink)",
                color: "white",
                fontFamily: "var(--mono)",
                fontSize: 10,
              }}
            >
              {busy ? "saving…" : mode === "task" ? "create task" : "save memory"}
            </button>
          </div>

          {error && <div style={{ marginTop: 12, fontSize: 11, color: "var(--rose)" }}>{error}</div>}
        </div>

        <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
          <div
            onClick={onOpenWorkspace}
            style={{
              border: "1px solid var(--border)",
              background: "white",
              padding: 16,
              cursor: "pointer",
            }}
          >
            <div style={{ marginBottom: 8, fontFamily: "var(--mono)", fontSize: 10, color: "var(--ink3)" }}>
              CURRENT WORKSPACE
            </div>
            <div style={{ fontSize: 16, fontWeight: 600 }}>
              {currentWorkspace?.label || currentWorkspace?.name || "No workspace"}
            </div>
            <div style={{ marginTop: 6, fontFamily: "var(--mono)", fontSize: 9, color: "var(--ink3)" }}>
              {currentWorkspace?.workspacePath || "Select or create a workspace"}
            </div>
            {currentProject && (
              <div style={{ marginTop: 10, fontFamily: "var(--mono)", fontSize: 9, color: "var(--ink3)" }}>
                {currentProject.name} · default {currentProject.defaultRuntime || "codex"}
              </div>
            )}
            {currentSession && (
              <button
                onClick={(e) => {
                  e.stopPropagation();
                  onSelectSession(currentSession.id, currentSession.taskId || null);
                }}
                style={{
                  marginTop: 12,
                  padding: "7px 10px",
                  border: "1px solid var(--border)",
                  fontFamily: "var(--mono)",
                  fontSize: 9,
                  color: "var(--ink3)",
                }}
              >
                open current session
              </button>
            )}
          </div>

          <button
            onClick={() => setShowWorkspaceModal(true)}
            style={{
              padding: "12px 14px",
              border: "1px solid var(--border)",
              background: "white",
              fontFamily: "var(--mono)",
              fontSize: 10,
              textAlign: "left",
            }}
          >
            + add workspace
          </button>
        </div>
      </div>

      {showWorkspaceModal && (
        <div
          style={{
            position: "fixed",
            inset: 0,
            background: "rgba(0,0,0,0.18)",
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            zIndex: 40,
          }}
          onClick={() => setShowWorkspaceModal(false)}
        >
          <div
            onClick={(e) => e.stopPropagation()}
            style={{
              width: 460,
              maxWidth: "calc(100vw - 32px)",
              border: "1px solid var(--border)",
              background: "white",
              padding: 20,
            }}
          >
            <div style={{ marginBottom: 14, fontFamily: "var(--mono)", fontSize: 10, color: "var(--ink3)" }}>
              ADD WORKSPACE
            </div>
            <div style={{ display: "grid", gap: 10 }}>
              <input
                value={workspaceName}
                onChange={(e) => setWorkspaceName(e.target.value)}
                placeholder="Workspace name"
                style={{ border: "1px solid var(--border)", padding: "11px 12px", background: "white" }}
              />
              <div style={{ fontFamily: "var(--mono)", fontSize: 9, color: "var(--ink3)", lineHeight: 1.5 }}>
                A workspace is a collection of projects. Add projects and their folders after creating it.
              </div>
              <div style={{ display: "flex", justifyContent: "flex-end", gap: 8, marginTop: 4 }}>
                <button
                  onClick={() => setShowWorkspaceModal(false)}
                  style={{
                    padding: "10px 12px",
                    border: "1px solid var(--border)",
                    background: "white",
                    fontFamily: "var(--mono)",
                    fontSize: 10,
                  }}
                >
                  cancel
                </button>
                <button
                  onClick={() => void createWorkspace()}
                  style={{
                    padding: "10px 12px",
                    border: "1px solid var(--ink)",
                    background: "var(--ink)",
                    color: "white",
                    fontFamily: "var(--mono)",
                    fontSize: 10,
                  }}
                >
                  {busy ? "creating…" : "create workspace"}
                </button>
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
