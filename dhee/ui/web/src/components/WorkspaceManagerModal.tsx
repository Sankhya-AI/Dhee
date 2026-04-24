import { useEffect, useState } from "react";
import { api } from "../api";
import type {
  ProjectIndexSnapshot,
  ProjectSummary,
  WorkspaceSummary,
} from "../types";

// ---------------------------------------------------------------------------
// WorkspaceManagerModal — single dialog that handles:
//
//   * create workspace (name + root folder)
//   * rename / describe / repoint workspace
//   * delete workspace (with type-to-confirm)
//   * create project inside the selected workspace
//   * rename / change default runtime on a project
//   * delete project
//
// The backend already exposes all six operations; this is the missing
// UX surface. Kept as a modal instead of a dedicated page so users can
// open it from the Channel view without leaving context.
// ---------------------------------------------------------------------------

type Tab = "workspaces" | "projects";

interface Props {
  open: boolean;
  onClose: () => void;
  projectIndex?: ProjectIndexSnapshot | null;
  initialWorkspaceId?: string;
  initialTab?: Tab;
  onChanged: () => Promise<void> | void;
}

const overlayStyle: React.CSSProperties = {
  position: "fixed",
  inset: 0,
  background: "rgba(20,16,10,0.28)",
  backdropFilter: "blur(4px)",
  display: "flex",
  alignItems: "center",
  justifyContent: "center",
  zIndex: 60,
};

const cardStyle: React.CSSProperties = {
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

const inputStyle: React.CSSProperties = {
  width: "100%",
  border: "1px solid var(--border)",
  padding: "9px 11px",
  background: "var(--bg)",
  fontSize: 13,
  lineHeight: 1.4,
};

const labelStyle: React.CSSProperties = {
  fontFamily: "var(--mono)",
  fontSize: 9,
  color: "var(--ink3)",
  letterSpacing: 0.5,
  textTransform: "uppercase",
  marginBottom: 4,
  display: "block",
};

const buttonPrimary: React.CSSProperties = {
  padding: "8px 14px",
  border: "1px solid var(--ink)",
  background: "var(--ink)",
  color: "white",
  fontFamily: "var(--mono)",
  fontSize: 10,
  letterSpacing: 0.4,
  cursor: "pointer",
};

const buttonGhost: React.CSSProperties = {
  padding: "8px 14px",
  border: "1px solid var(--border)",
  background: "white",
  color: "var(--ink2)",
  fontFamily: "var(--mono)",
  fontSize: 10,
  letterSpacing: 0.4,
  cursor: "pointer",
};

const buttonDanger: React.CSSProperties = {
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

export function WorkspaceManagerModal({
  open,
  onClose,
  projectIndex,
  initialWorkspaceId,
  initialTab = "workspaces",
  onChanged,
}: Props) {
  const workspaces = projectIndex?.workspaces || [];
  const [tab, setTab] = useState<Tab>(initialTab);
  const [selectedWorkspaceId, setSelectedWorkspaceId] = useState<string>(
    initialWorkspaceId || workspaces[0]?.id || "",
  );
  const currentWorkspace: WorkspaceSummary | null = workspaces.find(
    (workspace) => workspace.id === selectedWorkspaceId,
  ) || null;

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
  const [newProjectRuntime, setNewProjectRuntime] = useState<string>("codex");

  // Edit-project state (keyed by project id so edits don't leak across selections)
  const [projectEdits, setProjectEdits] = useState<
    Record<string, { name: string; description: string; defaultRuntime: string }>
  >({});

  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  useEffect(() => {
    if (!open) return;
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
    if (!currentWorkspace) return;
    setProjectEdits((prev) => {
      const next: typeof prev = {};
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

  if (!open) return null;

  const pickFolder = async (onPicked: (path: string) => void) => {
    setError(null);
    try {
      const res = await api.pickFolder("Choose a workspace root");
      if (res.ok && res.path) onPicked(res.path);
    } catch (e) {
      setError(String(e));
    }
  };

  const runGuarded = async (label: string, fn: () => Promise<void>) => {
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      await fn();
      setNotice(label);
      await onChanged();
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  };

  const onCreateWorkspace = () =>
    runGuarded("Workspace created.", async () => {
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

  const onUpdateWorkspace = () =>
    runGuarded("Workspace updated.", async () => {
      if (!currentWorkspace) return;
      const payload: { label?: string; description?: string; root_path?: string } = {};
      if (editWsName.trim() && editWsName !== (currentWorkspace.label || currentWorkspace.name)) {
        payload.label = editWsName.trim();
      }
      if (editWsDesc !== (currentWorkspace.description || "")) {
        payload.description = editWsDesc;
      }
      const currentRoot = String(
        currentWorkspace.workspacePath || currentWorkspace.rootPath || "",
      );
      if (editWsRoot.trim() && editWsRoot.trim() !== currentRoot) {
        payload.root_path = editWsRoot.trim();
      }
      if (!Object.keys(payload).length) {
        throw new Error("Nothing to save.");
      }
      await api.updateWorkspace(currentWorkspace.id, payload);
    });

  const onDeleteWorkspace = () =>
    runGuarded("Workspace deleted.", async () => {
      if (!currentWorkspace) return;
      if (deleteConfirm.trim() !== (currentWorkspace.label || currentWorkspace.name)) {
        throw new Error("Type the workspace name exactly to confirm.");
      }
      await api.deleteWorkspace(currentWorkspace.id);
      setSelectedWorkspaceId("");
      setDeleteConfirm("");
    });

  const onCreateProject = () =>
    runGuarded("Project created.", async () => {
      if (!currentWorkspace) throw new Error("Pick a workspace first.");
      const name = newProjectName.trim();
      if (!name) throw new Error("Project name is required.");
      await api.createProject(currentWorkspace.id, {
        name,
        description: newProjectDesc.trim() || undefined,
        default_runtime: newProjectRuntime,
      });
      setNewProjectName("");
      setNewProjectDesc("");
      setNewProjectRuntime("codex");
    });

  const onUpdateProject = (project: ProjectSummary) => () =>
    runGuarded("Project updated.", async () => {
      const draft = projectEdits[project.id];
      if (!draft) return;
      const payload: { name?: string; description?: string; default_runtime?: string } = {};
      if (draft.name.trim() && draft.name.trim() !== project.name) payload.name = draft.name.trim();
      if (draft.description !== (project.description || "")) payload.description = draft.description;
      if (draft.defaultRuntime && draft.defaultRuntime !== (project.defaultRuntime || "codex")) {
        payload.default_runtime = draft.defaultRuntime;
      }
      if (!Object.keys(payload).length) throw new Error("Nothing to save.");
      await api.updateProject(project.id, payload);
    });

  const onDeleteProject = (project: ProjectSummary) => () =>
    runGuarded("Project deleted.", async () => {
      const ok = window.confirm(
        `Delete project "${project.name}"? This removes its assets. The workspace stays.`,
      );
      if (!ok) throw new Error("cancelled");
      await api.deleteProject(project.id);
    });

  return (
    <div style={overlayStyle} onClick={onClose}>
      <div style={cardStyle} onClick={(e) => e.stopPropagation()}>
        {/* Header */}
        <div
          style={{
            padding: "14px 20px",
            borderBottom: "1px solid var(--border)",
            display: "flex",
            alignItems: "center",
            justifyContent: "space-between",
          }}
        >
          <div>
            <div style={{ fontSize: 15, fontWeight: 700 }}>Workspaces & projects</div>
            <div style={{ fontFamily: "var(--mono)", fontSize: 9, color: "var(--ink3)", marginTop: 2 }}>
              Organise every agent under a shared brain.
            </div>
          </div>
          <button
            onClick={onClose}
            style={{
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
            }}
          >
            <svg width={12} height={12} viewBox="0 0 24 24" fill="none">
              <path d="M6 6l12 12M18 6L6 18" stroke="currentColor" strokeWidth={2} strokeLinecap="round" />
            </svg>
          </button>
        </div>

        {/* Tabs */}
        <div
          style={{
            padding: "10px 20px",
            borderBottom: "1px solid var(--border)",
            display: "flex",
            gap: 6,
          }}
        >
          {(["workspaces", "projects"] as Tab[]).map((key) => (
            <button
              key={key}
              onClick={() => setTab(key)}
              style={{
                padding: "6px 12px",
                border: `1px solid ${tab === key ? "var(--ink)" : "var(--border)"}`,
                background: tab === key ? "var(--ink)" : "white",
                color: tab === key ? "white" : "var(--ink2)",
                fontFamily: "var(--mono)",
                fontSize: 10,
                letterSpacing: 0.4,
                textTransform: "uppercase",
                cursor: "pointer",
              }}
            >
              {key}
            </button>
          ))}
        </div>

        {/* Body */}
        <div style={{ padding: 20, overflowY: "auto", display: "flex", flexDirection: "column", gap: 16 }}>
          {tab === "workspaces" && (
            <>
              {/* Create workspace */}
              <section style={{ display: "flex", flexDirection: "column", gap: 8 }}>
                <div style={labelStyle}>New workspace</div>
                <input
                  placeholder="Name (e.g. Office, Personal, Sankhya AI Labs)"
                  value={newWsName}
                  onChange={(e) => setNewWsName(e.target.value)}
                  style={inputStyle}
                />
                <div style={{ display: "flex", gap: 8 }}>
                  <input
                    placeholder="Root folder (absolute path)"
                    value={newWsRoot}
                    onChange={(e) => setNewWsRoot(e.target.value)}
                    style={{ ...inputStyle, flex: 1 }}
                  />
                  <button
                    onClick={() => void pickFolder((path) => setNewWsRoot(path))}
                    style={buttonGhost}
                    disabled={busy}
                  >
                    browse…
                  </button>
                </div>
                <textarea
                  placeholder="Description (optional)"
                  value={newWsDesc}
                  onChange={(e) => setNewWsDesc(e.target.value)}
                  rows={2}
                  style={{ ...inputStyle, resize: "vertical" }}
                />
                <div style={{ display: "flex", justifyContent: "flex-end" }}>
                  <button
                    onClick={() => void onCreateWorkspace()}
                    disabled={busy || !newWsName.trim() || !newWsRoot.trim()}
                    style={{
                      ...buttonPrimary,
                      opacity: busy || !newWsName.trim() || !newWsRoot.trim() ? 0.5 : 1,
                    }}
                  >
                    create workspace
                  </button>
                </div>
              </section>

              {/* Existing workspaces */}
              <section style={{ display: "flex", flexDirection: "column", gap: 8 }}>
                <div style={labelStyle}>Existing · {workspaces.length}</div>
                {workspaces.length === 0 ? (
                  <div
                    style={{
                      padding: 14,
                      border: "1px dashed var(--border)",
                      fontFamily: "var(--mono)",
                      fontSize: 10,
                      color: "var(--ink3)",
                      lineHeight: 1.55,
                    }}
                  >
                    No workspaces yet. Create one above.
                  </div>
                ) : (
                  <div style={{ display: "grid", gap: 6 }}>
                    {workspaces.map((workspace) => {
                      const active = workspace.id === selectedWorkspaceId;
                      const projectCount = workspace.projects?.length || 0;
                      return (
                        <button
                          key={workspace.id}
                          onClick={() => setSelectedWorkspaceId(workspace.id)}
                          style={{
                            textAlign: "left",
                            padding: "10px 12px",
                            border: `1px solid ${active ? "var(--accent)" : "var(--border)"}`,
                            background: active ? "var(--surface)" : "white",
                            display: "flex",
                            justifyContent: "space-between",
                            gap: 12,
                            cursor: "pointer",
                          }}
                        >
                          <div style={{ minWidth: 0 }}>
                            <div style={{ fontSize: 13, fontWeight: 600, lineHeight: 1.3 }}>
                              {workspace.label || workspace.name}
                            </div>
                            <div style={{ fontFamily: "var(--mono)", fontSize: 9, color: "var(--ink3)", marginTop: 2 }}>
                              {workspace.workspacePath || workspace.rootPath || "—"}
                            </div>
                          </div>
                          <span style={{ fontFamily: "var(--mono)", fontSize: 9, color: "var(--ink3)" }}>
                            {projectCount} project{projectCount === 1 ? "" : "s"}
                          </span>
                        </button>
                      );
                    })}
                  </div>
                )}
              </section>

              {/* Edit selected workspace */}
              {currentWorkspace && (
                <section
                  style={{
                    display: "flex",
                    flexDirection: "column",
                    gap: 8,
                    borderTop: "1px solid var(--border)",
                    paddingTop: 14,
                  }}
                >
                  <div style={labelStyle}>Edit · {currentWorkspace.label || currentWorkspace.name}</div>
                  <input
                    value={editWsName}
                    onChange={(e) => setEditWsName(e.target.value)}
                    placeholder="Name"
                    style={inputStyle}
                  />
                  <div style={{ display: "flex", gap: 8 }}>
                    <input
                      value={editWsRoot}
                      onChange={(e) => setEditWsRoot(e.target.value)}
                      placeholder="Root folder"
                      style={{ ...inputStyle, flex: 1 }}
                    />
                    <button
                      onClick={() => void pickFolder((path) => setEditWsRoot(path))}
                      style={buttonGhost}
                      disabled={busy}
                    >
                      browse…
                    </button>
                  </div>
                  <textarea
                    value={editWsDesc}
                    onChange={(e) => setEditWsDesc(e.target.value)}
                    placeholder="Description"
                    rows={2}
                    style={{ ...inputStyle, resize: "vertical" }}
                  />
                  <div style={{ display: "flex", justifyContent: "flex-end", gap: 8 }}>
                    <button
                      onClick={() => void onUpdateWorkspace()}
                      disabled={busy}
                      style={{ ...buttonPrimary, opacity: busy ? 0.5 : 1 }}
                    >
                      save changes
                    </button>
                  </div>

                  <div
                    style={{
                      marginTop: 8,
                      padding: 12,
                      border: "1px solid rgba(203,63,78,0.3)",
                      borderRadius: 4,
                      background: "rgba(203,63,78,0.04)",
                    }}
                  >
                    <div
                      style={{
                        fontFamily: "var(--mono)",
                        fontSize: 9,
                        color: "var(--rose)",
                        letterSpacing: 0.5,
                        textTransform: "uppercase",
                        marginBottom: 6,
                      }}
                    >
                      Danger zone
                    </div>
                    <div style={{ fontSize: 11, color: "var(--ink2)", lineHeight: 1.5, marginBottom: 8 }}>
                      Deleting <strong>{currentWorkspace.label || currentWorkspace.name}</strong> removes every
                      project, asset, and line message. Sessions remain but detach. Type the workspace name to
                      confirm.
                    </div>
                    <div style={{ display: "flex", gap: 8 }}>
                      <input
                        value={deleteConfirm}
                        onChange={(e) => setDeleteConfirm(e.target.value)}
                        placeholder={currentWorkspace.label || currentWorkspace.name}
                        style={{ ...inputStyle, flex: 1 }}
                      />
                      <button
                        onClick={() => void onDeleteWorkspace()}
                        disabled={
                          busy ||
                          deleteConfirm.trim() !== (currentWorkspace.label || currentWorkspace.name)
                        }
                        style={{
                          ...buttonDanger,
                          opacity:
                            busy ||
                            deleteConfirm.trim() !== (currentWorkspace.label || currentWorkspace.name)
                              ? 0.5
                              : 1,
                        }}
                      >
                        delete workspace
                      </button>
                    </div>
                  </div>
                </section>
              )}
            </>
          )}

          {tab === "projects" && (
            <>
              <section style={{ display: "flex", flexDirection: "column", gap: 8 }}>
                <div style={labelStyle}>Workspace</div>
                <select
                  value={selectedWorkspaceId}
                  onChange={(e) => setSelectedWorkspaceId(e.target.value)}
                  style={inputStyle}
                >
                  <option value="">— pick a workspace —</option>
                  {workspaces.map((workspace) => (
                    <option key={workspace.id} value={workspace.id}>
                      {workspace.label || workspace.name}
                    </option>
                  ))}
                </select>
              </section>

              {currentWorkspace && (
                <>
                  {/* Create project */}
                  <section style={{ display: "flex", flexDirection: "column", gap: 8 }}>
                    <div style={labelStyle}>Add project to {currentWorkspace.label || currentWorkspace.name}</div>
                    <input
                      placeholder="Project name (e.g. frontend, backend, design)"
                      value={newProjectName}
                      onChange={(e) => setNewProjectName(e.target.value)}
                      style={inputStyle}
                    />
                    <textarea
                      placeholder="Description (optional)"
                      value={newProjectDesc}
                      onChange={(e) => setNewProjectDesc(e.target.value)}
                      rows={2}
                      style={{ ...inputStyle, resize: "vertical" }}
                    />
                    <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
                      <span style={{ fontFamily: "var(--mono)", fontSize: 10, color: "var(--ink3)" }}>
                        default runtime
                      </span>
                      <select
                        value={newProjectRuntime}
                        onChange={(e) => setNewProjectRuntime(e.target.value)}
                        style={{ ...inputStyle, flex: 1 }}
                      >
                        {runtimeOptions.map((option) => (
                          <option key={option} value={option}>
                            {option}
                          </option>
                        ))}
                      </select>
                    </div>
                    <div style={{ display: "flex", justifyContent: "flex-end" }}>
                      <button
                        onClick={() => void onCreateProject()}
                        disabled={busy || !newProjectName.trim()}
                        style={{
                          ...buttonPrimary,
                          opacity: busy || !newProjectName.trim() ? 0.5 : 1,
                        }}
                      >
                        add project
                      </button>
                    </div>
                  </section>

                  {/* Edit projects */}
                  <section style={{ display: "flex", flexDirection: "column", gap: 8 }}>
                    <div style={labelStyle}>
                      Projects · {(currentWorkspace.projects || []).length}
                    </div>
                    {(currentWorkspace.projects || []).length === 0 ? (
                      <div
                        style={{
                          padding: 14,
                          border: "1px dashed var(--border)",
                          fontFamily: "var(--mono)",
                          fontSize: 10,
                          color: "var(--ink3)",
                          lineHeight: 1.55,
                        }}
                      >
                        No projects yet — add the first one above.
                      </div>
                    ) : (
                      <div style={{ display: "grid", gap: 10 }}>
                        {(currentWorkspace.projects || []).map((project) => {
                          const draft =
                            projectEdits[project.id] || {
                              name: project.name,
                              description: project.description || "",
                              defaultRuntime: project.defaultRuntime || "codex",
                            };
                          const setDraft = (patch: Partial<typeof draft>) =>
                            setProjectEdits((prev) => ({
                              ...prev,
                              [project.id]: { ...draft, ...patch },
                            }));
                          return (
                            <div
                              key={project.id}
                              style={{
                                border: "1px solid var(--border)",
                                borderRadius: 6,
                                padding: 10,
                                display: "flex",
                                flexDirection: "column",
                                gap: 8,
                              }}
                            >
                              <input
                                value={draft.name}
                                onChange={(e) => setDraft({ name: e.target.value })}
                                style={inputStyle}
                              />
                              <textarea
                                value={draft.description}
                                onChange={(e) => setDraft({ description: e.target.value })}
                                rows={2}
                                style={{ ...inputStyle, resize: "vertical" }}
                              />
                              <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
                                <span
                                  style={{
                                    fontFamily: "var(--mono)",
                                    fontSize: 10,
                                    color: "var(--ink3)",
                                  }}
                                >
                                  runtime
                                </span>
                                <select
                                  value={draft.defaultRuntime}
                                  onChange={(e) => setDraft({ defaultRuntime: e.target.value })}
                                  style={{ ...inputStyle, flex: 1 }}
                                >
                                  {runtimeOptions.map((option) => (
                                    <option key={option} value={option}>
                                      {option}
                                    </option>
                                  ))}
                                </select>
                              </div>
                              <div style={{ display: "flex", justifyContent: "space-between", gap: 8 }}>
                                <button
                                  onClick={() => void onDeleteProject(project)()}
                                  disabled={busy}
                                  style={{ ...buttonDanger, opacity: busy ? 0.5 : 1 }}
                                >
                                  delete
                                </button>
                                <button
                                  onClick={() => void onUpdateProject(project)()}
                                  disabled={busy}
                                  style={{ ...buttonPrimary, opacity: busy ? 0.5 : 1 }}
                                >
                                  save
                                </button>
                              </div>
                            </div>
                          );
                        })}
                      </div>
                    )}
                  </section>
                </>
              )}
            </>
          )}
        </div>

        {/* Footer */}
        <div
          style={{
            padding: "10px 20px",
            borderTop: "1px solid var(--border)",
            background: "var(--bg)",
            display: "flex",
            alignItems: "center",
            justifyContent: "space-between",
            minHeight: 44,
          }}
        >
          <div style={{ fontFamily: "var(--mono)", fontSize: 10, lineHeight: 1.4 }}>
            {error ? (
              <span style={{ color: "var(--rose)" }}>{error}</span>
            ) : notice ? (
              <span style={{ color: "var(--green)" }}>{notice}</span>
            ) : (
              <span style={{ color: "var(--ink3)" }}>Changes save immediately.</span>
            )}
          </div>
          <button onClick={onClose} style={buttonGhost}>
            close
          </button>
        </div>
      </div>
    </div>
  );
}
