import { useEffect, useMemo, useRef, useState } from "react";
import { api } from "../api";
import { ChatMessage } from "../components/ChatMessage";
import { LineComposer, LineMessageCard, useWorkspaceLine } from "../components/LinePanel";
import { StatPill } from "../components/ui/StatPill";
import type {
  AssetContextSummary,
  FileContextSummary,
  ProjectIndexSnapshot,
  RuntimeStatusCard,
  SessionDetailSnapshot,
  SankhyaTask,
  Tweaks,
  WorkspaceGraphSnapshot,
} from "../types";

function fmtTime(value?: string | null) {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "—";
  return date.toLocaleString();
}

function runtimeSummary(runtime?: RuntimeStatusCard | null) {
  if (!runtime) return "No runtime selected";
  if (runtime.currentSession?.state === "active") return "session live";
  if (runtime.installed) return "installed";
  return "not attached";
}

function workspaceRootPath(workspace?: { rootPath?: string | null; workspacePath?: string | null; folders?: { path: string; primary?: boolean }[]; mounts?: { path: string; primary?: boolean }[] } | null) {
  const mounts = workspace?.mounts || workspace?.folders || [];
  const primary = mounts.find((mount) => mount.primary) || mounts[0];
  return primary?.path || workspace?.rootPath || workspace?.workspacePath || "";
}

export function WorkspaceView({
  tasks,
  activeTaskId,
  selectedProjectId,
  selectedWorkspaceId,
  selectedSessionId,
  projectIndex,
  workspaceGraph,
  onSelectTask,
  onSelectSession,
  onSelectProject,
  onCanvasOpen,
  onNotepadOpen,
  onAddTaskNote,
  onUpdateWorkspace,
  onAddWorkspaceFolder,
  onRemoveWorkspaceFolder,
  onCreateProject,
  onUpdateProject,
  onTasksRefresh,
  tweaks,
}: {
  tasks: SankhyaTask[];
  activeTaskId: string;
  selectedProjectId: string;
  selectedWorkspaceId: string;
  selectedSessionId: string;
  projectIndex?: ProjectIndexSnapshot | null;
  workspaceGraph?: WorkspaceGraphSnapshot | null;
  onSelectTask: (id: string) => void;
  onSelectSession: (sessionId: string, taskId?: string | null) => void;
  onSelectProject: (projectId: string, workspaceId?: string | null) => void;
  onCanvasOpen: () => void;
  onNotepadOpen: () => void;
  onAddTaskNote: (taskId: string, content: string) => Promise<void> | void;
  onUpdateWorkspace: (workspaceId: string, label: string) => Promise<void> | void;
  onAddWorkspaceFolder: (workspaceId: string, path: string, label?: string) => Promise<void> | void;
  onRemoveWorkspaceFolder: (workspaceId: string, path: string) => Promise<void> | void;
  onCreateProject: (
    workspaceId: string,
    payload: { name: string; description?: string; default_runtime?: string; scope_rules?: { path_prefix: string; label?: string }[] }
  ) => Promise<void> | void;
  onUpdateProject: (
    projectId: string,
    payload: { name?: string; description?: string; default_runtime?: string; scope_rules?: { path_prefix: string; label?: string }[] }
  ) => Promise<void> | void;
  onTasksRefresh: () => Promise<void> | void;
  tweaks: Tweaks;
}) {
  const [detail, setDetail] = useState<SessionDetailSnapshot | null>(null);
  const [workspaceRuntime, setWorkspaceRuntime] = useState<RuntimeStatusCard[]>([]);
  const [input, setInput] = useState("");
  const [saving, setSaving] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [selectedFile, setSelectedFile] = useState<FileContextSummary | null>(null);
  const [selectedAsset, setSelectedAsset] = useState<AssetContextSummary | null>(null);
  const [assetQuestion, setAssetQuestion] = useState("");
  const [showWorkspaceModal, setShowWorkspaceModal] = useState(false);
  const [managingWorkspaceId, setManagingWorkspaceId] = useState("");
  const [managingProjectId, setManagingProjectId] = useState("");
  const [workspaceLabelDraft, setWorkspaceLabelDraft] = useState("");
  const [projectNameDraft, setProjectNameDraft] = useState("");
  const [projectRuntimeDraft, setProjectRuntimeDraft] = useState<"codex" | "claude-code">("codex");
  const [projectScopeDraft, setProjectScopeDraft] = useState("");
  const [newProjectNameDraft, setNewProjectNameDraft] = useState("");
  const [newProjectRuntimeDraft, setNewProjectRuntimeDraft] = useState<"codex" | "claude-code">("codex");
  const [workspaceModalBusy, setWorkspaceModalBusy] = useState(false);
  // Line panel state is owned by the shared LinePanel hook — one
  // SSE subscription for Workspace and Channel views, same drop-oldest
  // fanout from workspace_line_bus.
  const [error, setError] = useState<string | null>(null);
  const inputRef = useRef<HTMLTextAreaElement | null>(null);
  const fileInputRef = useRef<HTMLInputElement | null>(null);

  const currentWorkspace =
    projectIndex?.workspaces?.find((workspace) => workspace.id === selectedWorkspaceId) ||
    projectIndex?.workspaces?.find((workspace) => workspace.id === workspaceGraph?.currentWorkspaceId) ||
    projectIndex?.workspaces?.[0] ||
    workspaceGraph?.workspace ||
    null;
  const currentProject =
    currentWorkspace?.projects?.find((project) => project.id === selectedProjectId) ||
    currentWorkspace?.projects?.find((project) => project.id === workspaceGraph?.currentProjectId) ||
    currentWorkspace?.projects?.[0] ||
    null;
  const currentSession =
    currentProject?.sessions?.find((session) => session.id === selectedSessionId) ||
    currentProject?.sessions?.find((session) => session.id === workspaceGraph?.currentSessionId) ||
    currentProject?.sessions?.[0] ||
    currentWorkspace?.sessions?.find((session) => session.id === selectedSessionId) ||
    currentWorkspace?.sessions?.find((session) => session.id === workspaceGraph?.currentSessionId) ||
    currentWorkspace?.sessions?.[0] ||
    null;
  const managedWorkspace =
    projectIndex?.workspaces?.find((workspace) => workspace.id === (managingWorkspaceId || currentWorkspace?.id)) ||
    currentWorkspace ||
    null;
  const managedProject =
    managedWorkspace?.projects?.find((project) => project.id === (managingProjectId || currentProject?.id)) ||
    managedWorkspace?.projects?.[0] ||
    null;

  useEffect(() => {
    if (currentWorkspace && !managingWorkspaceId) {
      setManagingWorkspaceId(currentWorkspace.id);
    }
  }, [currentWorkspace, managingWorkspaceId]);

  useEffect(() => {
    if (managedWorkspace && !managedWorkspace.projects.some((project) => project.id === managingProjectId)) {
      setManagingProjectId(managedWorkspace.projects[0]?.id || "");
    }
  }, [managedWorkspace, managingProjectId]);

  useEffect(() => {
    if (managedWorkspace) {
      setWorkspaceLabelDraft(managedWorkspace.label || managedWorkspace.name);
    }
  }, [managedWorkspace?.id]);

  useEffect(() => {
    if (currentProject) {
      setProjectNameDraft(currentProject.name);
    }
  }, [currentProject?.id]);

  useEffect(() => {
    if (managedProject) {
      setProjectNameDraft(managedProject.name);
      setProjectRuntimeDraft(
        managedProject.defaultRuntime === "claude-code" ? "claude-code" : "codex"
      );
      setProjectScopeDraft(
        (managedProject.scopeRules || [])
          .map((rule) => rule.pathPrefix)
          .filter(Boolean)
          .join("\n")
      );
    }
  }, [managedProject?.id]);

  const loadDetail = async (sessionId?: string | null) => {
    const nextSessionId = sessionId || currentSession?.id;
    if (!nextSessionId) return;
    try {
      const snapshot = await api.sessionDetail(nextSessionId);
      setDetail(snapshot);
      setWorkspaceRuntime(snapshot.runtime?.runtimes || []);
      setSelectedFile(snapshot.files?.[0] || null);
      setSelectedAsset(null);
      setError(null);
    } catch (e) {
      setError(String(e));
    }
  };

  const { messages: lineMessages } = useWorkspaceLine(
    currentWorkspace?.id,
    currentProject?.id,
  );

  useEffect(() => {
    void loadDetail(selectedSessionId || currentSession?.id);
  }, [selectedSessionId, currentWorkspace?.id]);

  useEffect(() => {
    const timer = window.setInterval(() => {
      void loadDetail(selectedSessionId || currentSession?.id);
    }, 5000);
    return () => window.clearInterval(timer);
  }, [selectedSessionId, currentSession?.id]);

  useEffect(() => {
    inputRef.current?.focus();
  }, [detail?.session?.id]);

  const fallbackTask = tasks.find((task) => task.id === activeTaskId) || null;
  const activeTask = detail?.task || fallbackTask || null;
  const messages = useMemo(() => {
    if (detail?.session?.messages?.length) return detail.session.messages;
    if (activeTask?.messages?.length) return activeTask.messages;
    return [];
  }, [detail?.session?.messages, activeTask]);

  const relevantRuntimes = useMemo(() => {
    if (!detail?.session?.runtime) return workspaceRuntime;
    return workspaceRuntime.filter((runtime) => runtime.id === detail.session.runtime);
  }, [detail?.session?.runtime, workspaceRuntime]);

  const saveNote = async () => {
    const content = input.trim();
    if (!content || !activeTask || saving) return;
    setSaving(true);
    try {
      await onAddTaskNote(activeTask.id, content);
      setInput("");
      await onTasksRefresh();
      await loadDetail(detail?.session?.id);
    } catch (e) {
      setError(String(e));
    } finally {
      setSaving(false);
    }
  };

  const handleAssetUpload = async (file?: File | null) => {
    if (!file || !detail?.session?.id) return;
    setUploading(true);
    try {
      await api.uploadSessionAsset(detail.session.id, file);
      await loadDetail(detail.session.id);
    } catch (e) {
      setError(String(e));
    } finally {
      setUploading(false);
      if (fileInputRef.current) fileInputRef.current.value = "";
    }
  };

  const handleAssetOpen = async (assetId: string) => {
    try {
      const snapshot = await api.assetContext(assetId);
      setSelectedAsset(snapshot);
    } catch (e) {
      setError(String(e));
    }
  };

  const openWorkspaceModal = () => {
    if (currentWorkspace) {
      setManagingWorkspaceId(currentWorkspace.id);
      setWorkspaceLabelDraft(currentWorkspace.label || currentWorkspace.name);
      setManagingProjectId(currentProject?.id || currentWorkspace.projects?.[0]?.id || "");
    }
    setShowWorkspaceModal(true);
    setError(null);
  };

  const saveWorkspaceRename = async () => {
    if (!managedWorkspace || !workspaceLabelDraft.trim() || workspaceModalBusy) return;
    setWorkspaceModalBusy(true);
    try {
      await onUpdateWorkspace(managedWorkspace.id, workspaceLabelDraft.trim());
      setError(null);
    } catch (e) {
      setError(String(e));
    } finally {
      setWorkspaceModalBusy(false);
    }
  };

  const addMountedFolder = async () => {
    if (!managedWorkspace || workspaceModalBusy) return;
    setWorkspaceModalBusy(true);
    setError(null);
    try {
      const picked = await api.pickFolder("Select a folder to mount in this workspace");
      if (picked.ok && picked.path) {
        await onAddWorkspaceFolder(managedWorkspace.id, picked.path);
      }
    } catch (e) {
      setError(String(e));
    } finally {
      setWorkspaceModalBusy(false);
    }
  };

  const removeMountedFolder = async (path: string) => {
    if (!managedWorkspace || workspaceModalBusy) return;
    setWorkspaceModalBusy(true);
    setError(null);
    try {
      await onRemoveWorkspaceFolder(managedWorkspace.id, path);
    } catch (e) {
      setError(String(e));
    } finally {
      setWorkspaceModalBusy(false);
    }
  };

  const saveProjectSettings = async () => {
    if (!managedProject || workspaceModalBusy) return;
    const scopeRules = projectScopeDraft
      .split("\n")
      .map((line) => line.trim())
      .filter(Boolean)
      .map((pathPrefix, index) => ({
        path_prefix: pathPrefix,
        label: index === 0 ? "primary" : `scope-${index + 1}`,
      }));
    setWorkspaceModalBusy(true);
    setError(null);
    try {
      await onUpdateProject(managedProject.id, {
        name: projectNameDraft.trim() || managedProject.name,
        default_runtime: projectRuntimeDraft,
        scope_rules: scopeRules,
      });
    } catch (e) {
      setError(String(e));
    } finally {
      setWorkspaceModalBusy(false);
    }
  };

  if (!currentProject || !currentWorkspace || !currentSession) {
    return (
      <div
        style={{
          height: "100%",
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          flexDirection: "column",
          gap: 12,
          fontFamily: "var(--mono)",
          fontSize: 11,
          color: "var(--ink3)",
        }}
      >
        <div style={{ fontSize: 13, color: "var(--ink)" }}>No mirrored workspace session yet</div>
        <div>Launch or attach a Codex / Claude Code session to populate this workspace.</div>
        <button
          onClick={onNotepadOpen}
          style={{
            padding: "6px 14px",
            border: "1px solid var(--border)",
            fontFamily: "var(--mono)",
            fontSize: 10,
            color: "var(--ink2)",
          }}
        >
          ← back to notepad
        </button>
      </div>
    );
  }

  return (
    <div style={{ display: "flex", flexDirection: "column", height: "100%" }}>
      <div
        style={{
          borderBottom: "1px solid var(--border)",
          padding: "0 14px",
          height: 48,
          display: "flex",
          alignItems: "center",
          gap: 10,
          flexShrink: 0,
        }}
      >
        <div style={{ display: "flex", alignItems: "center", gap: 10, minWidth: 0, flex: 1 }}>
          <span style={{ fontFamily: "var(--mono)", fontSize: 10, color: "var(--ink3)" }}>
            {currentWorkspace.label || currentWorkspace.name} / {currentProject.name}
          </span>
          <span
            style={{
              fontSize: 14,
              fontWeight: 600,
              overflow: "hidden",
              textOverflow: "ellipsis",
              whiteSpace: "nowrap",
            }}
          >
            {detail?.session?.title || currentSession.title}
          </span>
          <StatPill
            label={`${detail?.session?.runtime || currentSession.runtime}`.replace("-", " ")}
            tone="var(--green)"
          />
          <StatPill label={detail?.session?.permissionMode || currentSession.permissionMode || "native"} />
          <StatPill label={detail?.session?.state || currentSession.state} />
        </div>
        <button
          onClick={() => void loadDetail(detail?.session?.id)}
          style={{
            height: 48,
            padding: "0 12px",
            borderLeft: "1px solid var(--border)",
            fontFamily: "var(--mono)",
            fontSize: 9,
            color: "var(--ink2)",
          }}
        >
          REFRESH
        </button>
        <button
          onClick={onCanvasOpen}
          style={{
            height: 48,
            padding: "0 14px",
            borderLeft: "1px solid var(--border)",
            fontFamily: "var(--mono)",
            fontSize: 9,
            color: "var(--ink2)",
          }}
        >
          ⊞ CANVAS
        </button>
        <button
          onClick={openWorkspaceModal}
          style={{
            height: 48,
            padding: "0 14px",
            borderLeft: "1px solid var(--border)",
            fontFamily: "var(--mono)",
            fontSize: 9,
            color: "var(--ink2)",
          }}
        >
          MANAGE
        </button>
      </div>

      <div style={{ flex: 1, display: "flex", overflow: "hidden" }}>
        <div
          style={{
            width: tweaks.compactNav ? 74 : 260,
            borderRight: "1px solid var(--border)",
            display: "flex",
            flexDirection: "column",
            overflowY: "auto",
          }}
        >
          <div style={{ padding: "12px 12px 10px", borderBottom: "1px solid var(--border)" }}>
            <div style={{ fontFamily: "var(--mono)", fontSize: 9, color: "var(--ink3)", marginBottom: 8 }}>
              WORKSPACE
            </div>
            <div style={{ border: "1px solid var(--border)", background: "white", padding: 10, marginBottom: 12 }}>
              <div style={{ fontSize: 12, fontWeight: 600, marginBottom: 4 }}>
                {currentWorkspace.label || currentWorkspace.name}
              </div>
              <div style={{ fontFamily: "var(--mono)", fontSize: 9, color: "var(--ink3)", lineHeight: 1.5 }}>
                {(currentWorkspace.folders || currentWorkspace.mounts || []).length} mounted folders · {currentWorkspace.sessionCount || currentWorkspace.sessions.length} sessions
              </div>
              <button
                onClick={openWorkspaceModal}
                style={{
                  marginTop: 8,
                  padding: "6px 10px",
                  border: "1px solid var(--border)",
                  fontFamily: "var(--mono)",
                  fontSize: 9,
                  color: "var(--accent)",
                }}
              >
                manage workspace
              </button>
            </div>
            <div style={{ fontFamily: "var(--mono)", fontSize: 9, color: "var(--ink3)", marginBottom: 8 }}>
              PROJECTS
            </div>
            {currentWorkspace.projects.map((project) => (
              <div key={project.id} style={{ marginBottom: 10 }}>
                <button
                  onClick={() => onSelectProject(project.id, currentWorkspace.id)}
                  style={{
                    width: "100%",
                    textAlign: "left",
                    padding: "8px 9px",
                    border: `1px solid ${project.id === currentProject.id ? "var(--border2)" : "var(--border)"}`,
                    background: project.id === currentProject.id ? "var(--surface)" : "white",
                    fontSize: 12,
                    fontWeight: project.id === currentProject.id ? 700 : 500,
                    color: project.id === currentProject.id ? "var(--ink)" : "var(--ink2)",
                  }}
                >
                  {project.name}
                  <div style={{ marginTop: 4, fontFamily: "var(--mono)", fontSize: 9, color: "var(--ink3)" }}>
                    {project.sessions.length} sessions · default {project.defaultRuntime || "codex"}
                  </div>
                </button>
              </div>
            ))}
          </div>

          <div style={{ padding: "12px" }}>
            <div style={{ fontFamily: "var(--mono)", fontSize: 9, color: "var(--ink3)", marginBottom: 8 }}>
              SESSIONS
            </div>
            {(currentProject.sessions || []).map((session) => (
              <button
                key={session.id}
                onClick={() => onSelectSession(session.id, session.taskId)}
                style={{
                  width: "100%",
                  textAlign: "left",
                  padding: "9px 10px",
                  marginBottom: 8,
                  border: `1px solid ${
                    detail?.session?.id === session.id ? "var(--green)" : "var(--border)"
                  }`,
                  background: detail?.session?.id === session.id ? "oklch(0.98 0.02 145)" : "white",
                }}
              >
                <div style={{ fontSize: 12, fontWeight: 600, marginBottom: 4 }}>{session.title}</div>
                <div style={{ fontFamily: "var(--mono)", fontSize: 9, color: "var(--ink3)" }}>
                  {session.runtime} · {session.model || "unknown model"}
                </div>
                <div
                  style={{
                    fontSize: 10.5,
                    color: "var(--ink3)",
                    marginTop: 6,
                    lineHeight: 1.4,
                  }}
                >
                  {session.preview || "No preview yet."}
                </div>
              </button>
            ))}
          </div>
        </div>

        <div style={{ flex: 1, display: "flex", flexDirection: "column", minWidth: 0 }}>
          <div
            style={{
              padding: "12px 16px",
              borderBottom: "1px solid var(--border)",
              display: "flex",
              gap: 8,
              flexWrap: "wrap",
            }}
          >
            <StatPill label={detail?.session?.runtime || currentSession.runtime} tone="var(--green)" />
            <StatPill label={detail?.session?.taskStatus || activeTask?.status || "mirrored"} />
            <StatPill label={`${detail?.session?.touchedFiles?.length || 0} files`} />
            <StatPill label={`${detail?.assets?.length || 0} assets`} />
            <StatPill label={`${detail?.results?.length || 0} shared results`} />
          </div>

          <div style={{ flex: 1, overflowY: "auto", padding: "20px 18px 8px" }}>
            {messages.length === 0 && (
              <div style={{ fontFamily: "var(--mono)", fontSize: 11, color: "var(--ink3)" }}>
                No mirrored messages yet.
              </div>
            )}
            {messages.map((msg) => (
              <ChatMessage
                key={msg.id}
                msg={msg}
                tasks={tasks}
                onSelectTask={onSelectTask}
              />
            ))}
            {detail?.results?.slice(0, 8).map((result) => (
              <ChatMessage
                key={result.id}
                msg={{
                  id: `result:${result.id}`,
                  role: "agent",
                  content: `${result.tool_name}: ${result.digest || "No digest recorded."}`,
                }}
                tasks={tasks}
                onSelectTask={onSelectTask}
              />
            ))}
            <div style={{ height: 1 }} />
          </div>

          <div style={{ borderTop: "1px solid var(--border)", padding: "12px 16px" }}>
            <textarea
              ref={inputRef}
              value={input}
              onChange={(e) => setInput(e.target.value)}
              placeholder={
                activeTask
                  ? "Add a Dhee note to this session task…"
                  : "This mirrored session has no linked Dhee task yet."
              }
              rows={3}
              disabled={!activeTask}
              style={{
                width: "100%",
                fontFamily: "var(--font)",
                fontSize: 14,
                lineHeight: 1.5,
                border: "1px solid var(--border)",
                padding: "12px 14px",
                background: activeTask ? "white" : "var(--surface)",
              }}
            />
            <div style={{ marginTop: 10, display: "flex", gap: 10, alignItems: "center" }}>
              <button
                onClick={() => void saveNote()}
                disabled={!activeTask || saving}
                style={{
                  padding: "7px 14px",
                  border: "1px solid var(--ink)",
                  background: activeTask ? "var(--ink)" : "transparent",
                  color: activeTask ? "white" : "var(--ink3)",
                  fontFamily: "var(--mono)",
                  fontSize: 10,
                }}
              >
                {saving ? "saving…" : "save note"}
              </button>
              {error && <span style={{ fontSize: 11, color: "var(--rose)" }}>{error}</span>}
            </div>
          </div>
        </div>

        <div
          style={{
            width: 320,
            borderLeft: "1px solid var(--border)",
            overflowY: "auto",
            padding: "14px 14px 18px",
          }}
        >
          <div style={{ marginBottom: 18 }}>
            <div style={{ fontFamily: "var(--mono)", fontSize: 9, color: "var(--ink3)", marginBottom: 10 }}>
              RUNTIME
            </div>
            {relevantRuntimes.map((runtime) => (
              <div
                key={runtime.id}
                style={{
                  border: "1px solid var(--border)",
                  background: "white",
                  padding: 12,
                  marginBottom: 10,
                }}
              >
                <div
                  style={{
                    display: "flex",
                    justifyContent: "space-between",
                    alignItems: "center",
                    gap: 8,
                    marginBottom: 6,
                  }}
                >
                  <div style={{ fontSize: 12.5, fontWeight: 600 }}>{runtime.label}</div>
                  <StatPill
                    label={runtimeSummary(runtime)}
                    tone={runtime.installed ? "var(--green)" : "var(--rose)"}
                  />
                </div>
                <div style={{ fontSize: 11, color: "var(--ink2)" }}>
                  {runtime.currentSession?.title || runtime.currentSession?.cwd || "No active session"}
                </div>
                <div style={{ fontFamily: "var(--mono)", fontSize: 9, color: "var(--ink3)", marginTop: 6 }}>
                  limit: {runtime.limits.state}
                  {runtime.limits.resetAt ? ` · reset ${fmtTime(runtime.limits.resetAt)}` : ""}
                </div>
              </div>
            ))}
          </div>

          <div style={{ marginBottom: 18 }}>
            <div style={{ fontFamily: "var(--mono)", fontSize: 9, color: "var(--ink3)", marginBottom: 10 }}>
              COLLABORATION LINE
            </div>
            <LineComposer
              workspace={currentWorkspace}
              activeProjectId={currentProject?.id}
              sessionId={detail?.session?.id || currentSession?.id}
              taskId={activeTask?.id}
              onPublished={async () => {
                await onTasksRefresh();
              }}
            />
            <div style={{ display: "grid", gap: 8, marginTop: 10 }}>
              {(lineMessages || []).slice(0, 8).map((message) => (
                <LineMessageCard
                  key={message.id}
                  message={message}
                  workspace={currentWorkspace}
                />
              ))}
            </div>
          </div>

          <div style={{ marginBottom: 18 }}>
            <div style={{ fontFamily: "var(--mono)", fontSize: 9, color: "var(--ink3)", marginBottom: 10 }}>
              FILE CONTEXT
            </div>
            {detail?.files?.map((file) => (
              <button
                key={file.path}
                onClick={() => setSelectedFile(file)}
                style={{
                  width: "100%",
                  textAlign: "left",
                  padding: "10px 11px",
                  border: `1px solid ${
                    selectedFile?.path === file.path ? "var(--indigo)" : "var(--border)"
                  }`,
                  background: "white",
                  marginBottom: 8,
                }}
              >
                <div style={{ fontSize: 12, fontWeight: 600, marginBottom: 4 }}>{file.path.split("/").pop()}</div>
                <div style={{ fontFamily: "var(--mono)", fontSize: 9, color: "var(--ink3)" }}>
                  {file.results.length} results · {file.memories.length} memories
                </div>
              </button>
            ))}
            {selectedFile && (
              <div style={{ border: "1px solid var(--border)", background: "white", padding: 12 }}>
                <div style={{ fontSize: 12, fontWeight: 600, marginBottom: 6 }}>{selectedFile.path}</div>
                <div style={{ fontSize: 11, color: "var(--ink2)", lineHeight: 1.5, marginBottom: 8 }}>
                  {selectedFile.summary || "No stored summary yet."}
                </div>
                {selectedFile.results.slice(0, 3).map((result) => (
                  <div key={result.id} style={{ fontFamily: "var(--mono)", fontSize: 9, color: "var(--ink3)", marginBottom: 6 }}>
                    {result.tool_name}: {String(result.digest || "").slice(0, 120)}
                  </div>
                ))}
              </div>
            )}
          </div>

          <div>
            <div
              style={{
                display: "flex",
                justifyContent: "space-between",
                alignItems: "center",
                marginBottom: 10,
              }}
            >
              <div style={{ fontFamily: "var(--mono)", fontSize: 9, color: "var(--ink3)" }}>
                SESSION ASSETS
              </div>
              <button
                onClick={() => fileInputRef.current?.click()}
                style={{
                  padding: "4px 10px",
                  border: "1px solid var(--border)",
                  fontFamily: "var(--mono)",
                  fontSize: 9,
                  color: "var(--accent)",
                }}
              >
                {uploading ? "uploading…" : "upload"}
              </button>
            </div>
            <input
              ref={fileInputRef}
              type="file"
              hidden
              onChange={(e) => void handleAssetUpload(e.target.files?.[0])}
            />
            {detail?.assets?.map((asset) => (
              <button
                key={asset.id}
                onClick={() => void handleAssetOpen(asset.id)}
                style={{
                  width: "100%",
                  textAlign: "left",
                  border: "1px solid var(--border)",
                  background: "white",
                  padding: 12,
                  marginBottom: 8,
                }}
              >
                <div style={{ fontSize: 12, fontWeight: 600, marginBottom: 4 }}>{asset.name}</div>
                <div style={{ fontFamily: "var(--mono)", fontSize: 9, color: "var(--ink3)" }}>
                  {asset.mime_type || "file"} · {(asset.size_bytes || 0).toLocaleString()} bytes
                </div>
              </button>
            ))}
            {selectedAsset && (
              <div style={{ border: "1px solid var(--border)", background: "white", padding: 12, marginTop: 10 }}>
                <div style={{ fontSize: 12, fontWeight: 600, marginBottom: 6 }}>
                  {selectedAsset.asset.name}
                </div>
                <div style={{ fontSize: 11, color: "var(--ink2)", lineHeight: 1.5, marginBottom: 8 }}>
                  {selectedAsset.summary || "No extracted summary yet. Re-upload or parse this asset to make it queryable."}
                </div>
                {(selectedAsset.chunks || []).slice(0, 3).map((chunk) => (
                  <div key={`${selectedAsset.asset.id}:${chunk.chunk_index}`} style={{ fontFamily: "var(--mono)", fontSize: 9, color: "var(--ink3)", marginBottom: 6 }}>
                    chunk {chunk.chunk_index}: {chunk.content.slice(0, 180)}
                  </div>
                ))}
                <textarea
                  value={assetQuestion}
                  onChange={(e) => setAssetQuestion(e.target.value)}
                  placeholder="Ask Dhee about this asset…"
                  rows={3}
                  style={{ width: "100%", border: "1px solid var(--border)", padding: "10px 12px", marginTop: 8, background: "var(--bg)" }}
                />
                <button
                  onClick={async () => {
                    if (!assetQuestion.trim()) return;
                    try {
                      const result = await api.askAsset(selectedAsset.asset.id, assetQuestion.trim());
                      setAssetQuestion("");
                      if (result.launch?.session_id) onSelectSession(result.launch.session_id, result.launch.task_id || null);
                    } catch (e) {
                      setError(String(e));
                    }
                  }}
                  style={{ marginTop: 8, padding: "8px 12px", border: "1px solid var(--ink)", background: "var(--ink)", color: "white", fontFamily: "var(--mono)", fontSize: 10 }}
                >
                  ask with default claude code
                </button>
              </div>
            )}
          </div>
        </div>
      </div>
      {showWorkspaceModal && currentProject && managedWorkspace && (
        <div
          onClick={() => setShowWorkspaceModal(false)}
          style={{
            position: "fixed",
            inset: 0,
            background: "rgba(12, 12, 12, 0.22)",
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            padding: 24,
            zIndex: 80,
          }}
        >
          <div
            onClick={(e) => e.stopPropagation()}
            style={{
              width: "min(1040px, calc(100vw - 80px))",
              maxHeight: "calc(100vh - 80px)",
              background: "var(--bg)",
              border: "1px solid var(--border2)",
              display: "grid",
              gridTemplateColumns: "280px minmax(0, 1fr)",
              overflow: "hidden",
              boxShadow: "0 30px 80px rgba(0,0,0,0.12)",
            }}
          >
            <div style={{ borderRight: "1px solid var(--border)", background: "white", overflowY: "auto" }}>
              <div style={{ padding: 16, borderBottom: "1px solid var(--border)" }}>
                <div style={{ fontFamily: "var(--mono)", fontSize: 10, color: "var(--ink3)" }}>
                  WORKSPACES
                </div>
                <div style={{ marginTop: 6, fontSize: 13, fontWeight: 600 }}>switch workspace</div>
              </div>
              <div style={{ padding: 12 }}>
                {(projectIndex?.workspaces || []).map((workspace) => (
                  <button
                    key={workspace.id}
                    onClick={() => {
                      setManagingWorkspaceId(workspace.id);
                      setManagingProjectId(workspace.projects?.[0]?.id || "");
                    }}
                    style={{
                      width: "100%",
                      textAlign: "left",
                      padding: "10px 12px",
                      marginBottom: 8,
                      border: `1px solid ${workspace.id === managedWorkspace.id ? "var(--accent)" : "var(--border)"}`,
                      background: workspace.id === managedWorkspace.id ? "rgba(224, 107, 63, 0.06)" : "white",
                    }}
                    >
                      <div style={{ fontSize: 12, fontWeight: 600, marginBottom: 4 }}>
                        {workspace.label || workspace.name}
                      </div>
                      <div style={{ fontFamily: "var(--mono)", fontSize: 9, color: "var(--ink3)" }}>
                        {workspace.sessionCount || workspace.sessions.length} sessions · {(workspace.folders || workspace.mounts || []).length} folders
                      </div>
                    </button>
                ))}
              </div>
            </div>
            <div style={{ display: "flex", flexDirection: "column", overflow: "hidden" }}>
              <div
                style={{
                  padding: "16px 18px",
                  borderBottom: "1px solid var(--border)",
                  background: "white",
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "space-between",
                  gap: 12,
                }}
              >
                <div>
                  <div style={{ fontFamily: "var(--mono)", fontSize: 10, color: "var(--ink3)" }}>
                    WORKSPACE SETTINGS
                  </div>
                  <div style={{ marginTop: 6, fontSize: 18, fontWeight: 650 }}>
                    {managedWorkspace.label || managedWorkspace.name}
                  </div>
                </div>
                <button
                  onClick={() => setShowWorkspaceModal(false)}
                  style={{ fontFamily: "var(--mono)", fontSize: 10, color: "var(--ink3)" }}
                >
                  close
                </button>
              </div>
              <div style={{ flex: 1, overflowY: "auto", padding: 18 }}>
                <div style={{ marginBottom: 24 }}>
                  <div style={{ fontFamily: "var(--mono)", fontSize: 10, color: "var(--ink3)", marginBottom: 10 }}>
                    RENAME
                  </div>
                  <div style={{ display: "flex", gap: 10, alignItems: "center" }}>
                    <input
                      value={workspaceLabelDraft}
                      onChange={(e) => setWorkspaceLabelDraft(e.target.value)}
                      placeholder="Workspace name"
                      style={{
                        flex: 1,
                        border: "1px solid var(--border)",
                        padding: "10px 12px",
                        background: "white",
                        fontSize: 14,
                      }}
                    />
                    <button
                      onClick={() => void saveWorkspaceRename()}
                      disabled={!workspaceLabelDraft.trim() || workspaceModalBusy}
                      style={{
                        padding: "10px 12px",
                        border: "1px solid var(--ink)",
                        background: "var(--ink)",
                        color: "white",
                        fontFamily: "var(--mono)",
                        fontSize: 10,
                        opacity: !workspaceLabelDraft.trim() || workspaceModalBusy ? 0.6 : 1,
                      }}
                    >
                      {workspaceModalBusy ? "saving..." : "save name"}
                    </button>
                  </div>
                </div>

                <div>
                  <div
                    style={{
                      display: "flex",
                      justifyContent: "space-between",
                      alignItems: "center",
                      gap: 12,
                      marginBottom: 10,
                    }}
                  >
                    <div style={{ fontFamily: "var(--mono)", fontSize: 10, color: "var(--ink3)" }}>
                      MOUNTED FOLDERS
                    </div>
                    <button
                      onClick={() => void addMountedFolder()}
                      disabled={workspaceModalBusy}
                      style={{
                        padding: "8px 10px",
                        border: "1px solid var(--border)",
                        background: "white",
                        fontFamily: "var(--mono)",
                        fontSize: 9,
                        color: "var(--accent)",
                      }}
                    >
                      {workspaceModalBusy ? "working..." : "add folder"}
                    </button>
                  </div>
                  <div style={{ display: "grid", gap: 10 }}>
                    {(managedWorkspace.folders || managedWorkspace.mounts || []).map((folder) => (
                      <div
                        key={folder.path}
                        style={{
                          border: "1px solid var(--border)",
                          background: "white",
                          padding: 12,
                          display: "flex",
                          justifyContent: "space-between",
                          gap: 12,
                          alignItems: "flex-start",
                        }}
                      >
                        <div style={{ minWidth: 0 }}>
                          <div style={{ fontSize: 12, fontWeight: 600, marginBottom: 4 }}>
                            {folder.primary ? "root folder" : folder.label}
                          </div>
                          <div
                            style={{
                              fontFamily: "var(--mono)",
                              fontSize: 9,
                              color: "var(--ink3)",
                              lineHeight: 1.5,
                              wordBreak: "break-all",
                            }}
                          >
                            {folder.path}
                          </div>
                        </div>
                        <button
                          onClick={() => void removeMountedFolder(folder.path)}
                          disabled={workspaceModalBusy || (managedWorkspace.folders || managedWorkspace.mounts || []).length <= 1}
                          style={{
                            fontFamily: "var(--mono)",
                            fontSize: 9,
                            color: "var(--rose)",
                            opacity: workspaceModalBusy || (managedWorkspace.folders || managedWorkspace.mounts || []).length <= 1 ? 0.5 : 1,
                          }}
                        >
                          remove
                        </button>
                      </div>
                    ))}
                  </div>
                  <div style={{ marginTop: 10, fontSize: 11, color: "var(--ink3)", lineHeight: 1.5 }}>
                    Sessions are included in this workspace by matching their working directory against these mounted folders.
                    Removing a folder removes those sessions from this workspace view without deleting the mirrored session record.
                  </div>
                </div>
                <div style={{ marginTop: 24 }}>
                  <div style={{ fontFamily: "var(--mono)", fontSize: 10, color: "var(--ink3)", marginBottom: 10 }}>
                    PROJECTS
                  </div>
                  <div style={{ display: "grid", gridTemplateColumns: "240px minmax(0, 1fr)", gap: 14 }}>
                    <div style={{ display: "grid", gap: 10, alignContent: "start" }}>
                      {(managedWorkspace.projects || []).map((project) => (
                        <button
                          key={project.id}
                          onClick={() => setManagingProjectId(project.id)}
                          style={{
                            width: "100%",
                            textAlign: "left",
                            border: `1px solid ${project.id === managedProject?.id ? "var(--accent)" : "var(--border)"}`,
                            background: project.id === managedProject?.id ? "rgba(224, 107, 63, 0.06)" : "white",
                            padding: 12,
                          }}
                        >
                          <div style={{ fontSize: 12, fontWeight: 600 }}>{project.name}</div>
                          <div style={{ fontFamily: "var(--mono)", fontSize: 9, color: "var(--ink3)", marginTop: 4 }}>
                            {project.sessions.length} sessions · default {project.defaultRuntime || "codex"}
                          </div>
                        </button>
                      ))}
                    </div>
                    <div style={{ display: "grid", gap: 12, alignContent: "start" }}>
                      {managedProject ? (
                        <div style={{ border: "1px solid var(--border)", background: "white", padding: 14 }}>
                          <div style={{ fontFamily: "var(--mono)", fontSize: 10, color: "var(--ink3)", marginBottom: 10 }}>
                            EDIT PROJECT
                          </div>
                          <div style={{ display: "grid", gap: 10 }}>
                            <input
                              value={projectNameDraft}
                              onChange={(e) => setProjectNameDraft(e.target.value)}
                              placeholder="Project name"
                              style={{
                                border: "1px solid var(--border)",
                                padding: "10px 12px",
                                background: "white",
                                fontSize: 14,
                              }}
                            />
                            <select
                              value={projectRuntimeDraft}
                              onChange={(e) => setProjectRuntimeDraft(e.target.value as "codex" | "claude-code")}
                              style={{
                                border: "1px solid var(--border)",
                                padding: "10px 12px",
                                background: "white",
                                fontSize: 14,
                              }}
                            >
                              <option value="codex">Codex default runtime</option>
                              <option value="claude-code">Claude Code default runtime</option>
                            </select>
                            <textarea
                              value={projectScopeDraft}
                              onChange={(e) => setProjectScopeDraft(e.target.value)}
                              rows={5}
                              placeholder="One path scope rule per line"
                              style={{
                                border: "1px solid var(--border)",
                                padding: "10px 12px",
                                background: "white",
                                fontSize: 13,
                                lineHeight: 1.5,
                              }}
                            />
                            <div style={{ fontSize: 11, color: "var(--ink3)", lineHeight: 1.5 }}>
                              Sessions are assigned to this project by the longest matching scope rule path.
                            </div>
                            <button
                              onClick={() => void saveProjectSettings()}
                              disabled={workspaceModalBusy || !projectNameDraft.trim()}
                              style={{
                                justifySelf: "start",
                                padding: "10px 12px",
                                border: "1px solid var(--ink)",
                                background: "var(--ink)",
                                color: "white",
                                fontFamily: "var(--mono)",
                                fontSize: 10,
                                opacity: workspaceModalBusy || !projectNameDraft.trim() ? 0.6 : 1,
                              }}
                            >
                              {workspaceModalBusy ? "saving..." : "save project"}
                            </button>
                          </div>
                        </div>
                      ) : null}
                      <div style={{ border: "1px solid var(--border)", background: "white", padding: 14 }}>
                        <div style={{ fontFamily: "var(--mono)", fontSize: 10, color: "var(--ink3)", marginBottom: 10 }}>
                          ADD PROJECT
                        </div>
                        <div style={{ display: "grid", gap: 10 }}>
                          <input
                            value={newProjectNameDraft}
                            onChange={(e) => setNewProjectNameDraft(e.target.value)}
                            placeholder="New project name"
                            style={{
                              border: "1px solid var(--border)",
                              padding: "10px 12px",
                              background: "white",
                              fontSize: 14,
                            }}
                          />
                          <select
                            value={newProjectRuntimeDraft}
                            onChange={(e) => setNewProjectRuntimeDraft(e.target.value as "codex" | "claude-code")}
                            style={{
                              border: "1px solid var(--border)",
                              padding: "10px 12px",
                              background: "white",
                              fontSize: 14,
                            }}
                          >
                            <option value="codex">Codex default runtime</option>
                            <option value="claude-code">Claude Code default runtime</option>
                          </select>
                          <button
                            onClick={async () => {
                              if (!managedWorkspace || !newProjectNameDraft.trim()) return;
                              setWorkspaceModalBusy(true);
                              try {
                                await onCreateProject(managedWorkspace.id, {
                                  name: newProjectNameDraft.trim(),
                                  default_runtime: newProjectRuntimeDraft,
                                  scope_rules: [
                                    { path_prefix: workspaceRootPath(managedWorkspace), label: "root" },
                                  ],
                                });
                                setNewProjectNameDraft("");
                              } catch (e) {
                                setError(String(e));
                              } finally {
                                setWorkspaceModalBusy(false);
                              }
                            }}
                            style={{
                              justifySelf: "start",
                              padding: "10px 12px",
                              border: "1px solid var(--ink)",
                              background: "var(--ink)",
                              color: "white",
                              fontFamily: "var(--mono)",
                              fontSize: 10,
                            }}
                          >
                            add project
                          </button>
                        </div>
                      </div>
                    </div>
                  </div>
                </div>
                {error && (
                  <div style={{ marginTop: 18, fontSize: 11, color: "var(--rose)" }}>{error}</div>
                )}
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
