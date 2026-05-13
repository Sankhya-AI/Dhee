import { useEffect, useState } from "react";
import { api } from "../api";
import type { OrgGraphSnapshot, OrgNode, Viewer } from "../types";
import { SectionHeader } from "./ui/SectionHeader";

interface Props {
  node: OrgNode | null;
  graph: OrgGraphSnapshot | null;
  viewer: Viewer | null;
  isManager: boolean;
  onClose: () => void;
  onOpenVault: (teamId?: string) => void;
  onOpenSession: (sessionId: string, taskId?: string | null) => void;
  onChanged: () => void;
}

interface RepoMapping {
  mapping_id?: string;
  repo_url?: string | null;
  local_path?: string | null;
  provider?: string | null;
  branch?: string | null;
  project_id?: string | null;
  team_id?: string | null;
  metadata?: Record<string, unknown> | null;
}

interface JoinEvent {
  repo_root?: string | null;
  role?: string | null;
  received_at?: string | null;
}

interface CollaboratingTeam {
  team_id?: string | null;
  name?: string | null;
  project_id?: string | null;
}

function metaRecord(node: OrgNode | null): Record<string, unknown> {
  return (node?.meta || {}) as Record<string, unknown>;
}

function sessionTaskId(node: OrgNode): string | null {
  const meta = metaRecord(node);
  const taskId = String(meta.task_id || meta.taskId || "");
  return taskId || null;
}

function repoMappingsFromNode(node: OrgNode | null): RepoMapping[] {
  const rows = metaRecord(node).repo_mappings;
  return Array.isArray(rows) ? (rows as RepoMapping[]) : [];
}

function repoMappingLabel(mapping: RepoMapping): string {
  const meta = (mapping.metadata || {}) as Record<string, unknown>;
  const label = typeof meta.label === "string" ? meta.label.trim() : "";
  const raw = label || mapping.local_path || mapping.repo_url || "folder";
  return String(raw).split("/").filter(Boolean).pop() || String(raw);
}

function runtimeColor(runtime: unknown): string {
  const value = String(runtime || "").toLowerCase();
  if (value === "codex") return "var(--indigo)";
  if (value === "claude-code" || value === "claude") return "var(--accent)";
  return "var(--ink3)";
}

function uniqueMappings(rows: RepoMapping[]): RepoMapping[] {
  const seen = new Set<string>();
  const out: RepoMapping[] = [];
  for (const row of rows) {
    const key = String(row.mapping_id || row.local_path || row.repo_url || "");
    if (!key || seen.has(key)) continue;
    seen.add(key);
    out.push(row);
  }
  return out;
}

export function OrgDrawer({
  node,
  graph,
  viewer,
  isManager,
  onClose,
  onOpenVault,
  onOpenSession,
  onChanged,
}: Props) {
  const [busy, setBusy] = useState<string | null>(null);
  const [folderPath, setFolderPath] = useState("");
  const [folderLabel, setFolderLabel] = useState("");
  const [projectName, setProjectName] = useState("");
  const [teamName, setTeamName] = useState("");
  const [gitUrl, setGitUrl] = useState("");
  const [collabTeamId, setCollabTeamId] = useState("");
  const [confirmReset, setConfirmReset] = useState(false);
  const [confirmDeleteProject, setConfirmDeleteProject] = useState(false);

  useEffect(() => {
    setFolderPath("");
    setFolderLabel("");
    setProjectName("");
    setTeamName("");
    setGitUrl("");
    setCollabTeamId("");
    setConfirmReset(false);
    setConfirmDeleteProject(false);
  }, [node?.id]);

  if (!node) return null;
  const isWorkspace = node.type === "workspace";
  const isProject = node.type === "project";
  const isTeam = node.type === "team" || node.type === "global_team";
  const isRepo = node.type === "repo";
  const isFolder = node.type === "folder";
  const isSession = node.type === "session";

  // ─── Workspace ─────────────────────────────────────────────────────────
  const projects =
    isWorkspace && graph
      ? graph.edges
          .filter((e) => e.source === node.id && e.kind === "contains")
          .map((e) => graph.nodes.find((n) => n.id === e.target))
          .filter((n): n is OrgNode => Boolean(n) && n!.type === "project")
      : [];

  // ─── Project ───────────────────────────────────────────────────────────
  const projectId = isProject
    ? String((node.meta as { project_id?: string })?.project_id || "")
    : "";
  const projectTeams =
    isProject && graph
      ? graph.edges
          .filter((e) => e.kind === "contains" && e.source === node.id)
          .map((e) => graph.nodes.find((n) => n.id === e.target))
          .filter(
            (n): n is OrgNode =>
              Boolean(n) && (n!.type === "team" || n!.type === "global_team")
          )
      : [];

  // ─── Team / repo body data ─────────────────────────────────────────────
  const repoMappings = isTeam ? repoMappingsFromNode(node) : [];
  const teamMeta = metaRecord(node);
  const teamId = isTeam ? String(teamMeta.team_id || "") : "";
  const developerCount =
    typeof teamMeta.developer_count === "number" ? teamMeta.developer_count : 0;
  const developerJoinEvents = Array.isArray(teamMeta.developer_join_events)
    ? (teamMeta.developer_join_events as JoinEvent[])
    : [];
  const collaboratingTeams = Array.isArray(teamMeta.collaborating_teams)
    ? (teamMeta.collaborating_teams as CollaboratingTeam[])
    : [];
  const allTeamNodes = graph
    ? graph.nodes.filter(
        (n) =>
          (n.type === "team" || n.type === "global_team") &&
          String((n.meta as { team_id?: string })?.team_id || "") !== teamId
      )
    : [];
  const folderMeta = metaRecord(node);
  const selectedFolderPath = isFolder ? String(folderMeta.path || "") : "";
  const folderShared = isFolder ? Boolean(folderMeta.shared) : false;
  const folderSessions =
    isFolder && graph
      ? graph.edges
          .filter((e) => e.source === node.id && e.kind === "contains")
          .map((e) => graph.nodes.find((n) => n.id === e.target))
          .filter((n): n is OrgNode => Boolean(n) && n!.type === "session")
      : [];

  // ─── Actions ───────────────────────────────────────────────────────────
  const handleResetWorkspace = async () => {
    setBusy("reset");
    try {
      await api.enterpriseResetWorkspace();
      onChanged();
    } finally {
      setBusy(null);
    }
  };

  const handleCreateProject = async () => {
    if (!projectName.trim()) return;
    setBusy("create-project");
    try {
      await api.enterpriseCreateProject({ name: projectName.trim() });
      setProjectName("");
      onChanged();
    } finally {
      setBusy(null);
    }
  };

  const handleCreateProjectTeam = async () => {
    if (!projectId || !teamName.trim()) return;
    setBusy("create-team");
    try {
      await api.enterpriseCreateProjectTeam(projectId, { name: teamName.trim() });
      setTeamName("");
      onChanged();
    } finally {
      setBusy(null);
    }
  };

  const handleAddFolder = async () => {
    if (!teamId || !folderPath.trim()) return;
    setBusy("add-folder");
    try {
      await api.enterpriseAddTeamFolder(teamId, {
        local_path: folderPath.trim(),
        label: folderLabel.trim() || undefined,
        kind: "folder",
      });
      setFolderPath("");
      setFolderLabel("");
      onChanged();
    } finally {
      setBusy(null);
    }
  };

  const handleAddGitRepo = async () => {
    if (!teamId || !gitUrl.trim()) return;
    setBusy("add-git");
    try {
      await api.enterpriseAddTeamFolder(teamId, {
        repo_url: gitUrl.trim(),
        kind: "git",
      });
      setGitUrl("");
      onChanged();
    } finally {
      setBusy(null);
    }
  };

  const handlePickFolder = async () => {
    setBusy("pick-folder");
    try {
      const r = await api.pickFolderPath("Pick a folder for this team");
      if (r.ok && r.path) setFolderPath(r.path);
    } finally {
      setBusy(null);
    }
  };

  const handleDeleteProject = async () => {
    if (!projectId) return;
    setBusy("delete-project");
    try {
      await api.enterpriseDeleteProject(projectId);
      onChanged();
    } finally {
      setBusy(null);
    }
  };

  const handleRemoveFolder = async (mappingId?: string | null) => {
    if (!mappingId) return;
    setBusy("remove-folder");
    try {
      await api.enterpriseRemoveFolder(mappingId);
      onChanged();
    } finally {
      setBusy(null);
    }
  };

  const handleAddCollaborator = async () => {
    if (!teamId || !collabTeamId.trim()) return;
    setBusy("collaborate");
    try {
      await api.enterpriseAddTeamCollaborator(teamId, collabTeamId.trim());
      setCollabTeamId("");
      onChanged();
    } finally {
      setBusy(null);
    }
  };

  const handleExtractProject = async () => {
    if (!projectId) return;
    setBusy("extract");
    try {
      const result = await api.enterpriseExtractProject(projectId);
      onChanged();
      const summary =
        `AST extraction · ${result.folders_seen} folder(s) · ` +
        `${result.files_seen} files (${result.files_extracted} new, ${result.files_cached} cached) · ` +
        `${result.nodes_upserted} nodes · ${result.edges_upserted} edges`;
      // eslint-disable-next-line no-alert
      window.alert(summary);
    } catch (err) {
      // eslint-disable-next-line no-alert
      window.alert(`Extraction failed: ${String(err)}`);
    } finally {
      setBusy(null);
    }
  };

  const handleExtractTeam = async () => {
    if (!teamId) return;
    setBusy("extract");
    try {
      const result = await api.enterpriseExtractTeam(teamId);
      onChanged();
      const summary =
        `AST extraction · ${result.folders_seen} folder(s) · ` +
        `${result.files_seen} files (${result.files_extracted} new, ${result.files_cached} cached) · ` +
        `${result.nodes_upserted} nodes · ${result.edges_upserted} edges`;
      // eslint-disable-next-line no-alert
      window.alert(summary);
    } catch (err) {
      // eslint-disable-next-line no-alert
      window.alert(`Extraction failed: ${String(err)}`);
    } finally {
      setBusy(null);
    }
  };

  const handleToggleFolderShare = async () => {
    if (!selectedFolderPath) return;
    setBusy("share-folder");
    try {
      await api.localContextShareFolder({
        path: selectedFolderPath,
        shared: !folderShared,
      });
      onChanged();
    } finally {
      setBusy(null);
    }
  };

  return (
    <aside
      style={{
        position: "absolute",
        top: 0,
        right: 0,
        bottom: 0,
        width: 440,
        background: "var(--bg)",
        borderLeft: "1px solid var(--border)",
        boxShadow: "-12px 0 30px rgba(20,16,10,0.06)",
        display: "flex",
        flexDirection: "column",
        zIndex: 25,
        animation: "fadein 0.18s ease",
      }}
    >
      <header
        style={{
          padding: "12px 16px",
          borderBottom: "1px solid var(--border)",
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
        }}
      >
        <div>
          <div
            style={{
              fontFamily: "var(--mono)",
              fontSize: 9,
              letterSpacing: "0.12em",
              color: "var(--ink3)",
              textTransform: "uppercase",
            }}
          >
            {nodeKindLabel(node.type)}
          </div>
          <div style={{ fontSize: 16, fontWeight: 500, color: "var(--ink)" }}>
            {node.label}
          </div>
        </div>
        <button
          onClick={onClose}
          aria-label="Close drawer"
          style={{
            width: 24,
            height: 24,
            borderRadius: 4,
            background: "var(--surface)",
            border: "1px solid var(--border)",
            color: "var(--ink2)",
          }}
        >
          ×
        </button>
      </header>

      <div style={{ flex: 1, overflowY: "auto", padding: 14 }}>
        {isFolder ? (
          <FolderBody
            node={node}
            sessions={folderSessions}
            shared={folderShared}
            onToggleShare={handleToggleFolderShare}
            onOpenVault={() => onOpenVault()}
            onOpenSession={onOpenSession}
            busy={busy}
          />
        ) : null}
        {isSession ? (
          <SessionBody
            node={node}
            onOpenSession={() => onOpenSession(node.id, sessionTaskId(node))}
          />
        ) : null}
        {isWorkspace ? (
          <WorkspaceBody
            projects={projects}
            projectName={projectName}
            onProjectName={setProjectName}
            onCreateProject={handleCreateProject}
            confirmReset={confirmReset}
            onAskReset={() => setConfirmReset(true)}
            onCancelReset={() => setConfirmReset(false)}
            onConfirmReset={handleResetWorkspace}
            busy={busy}
          />
        ) : null}
        {isProject ? (
          <ProjectBody
            teams={projectTeams}
            teamName={teamName}
            onTeamName={setTeamName}
            onCreateTeam={handleCreateProjectTeam}
            confirmDelete={confirmDeleteProject}
            onAskDelete={() => setConfirmDeleteProject(true)}
            onCancelDelete={() => setConfirmDeleteProject(false)}
            onConfirmDelete={handleDeleteProject}
            busy={busy}
          />
        ) : null}
        {isTeam ? (
          <TeamBody
            node={node}
            repoMappings={repoMappings}
            developerCount={developerCount}
            developerJoinEvents={developerJoinEvents}
            collaboratingTeams={collaboratingTeams}
            collaboratorOptions={allTeamNodes}
            collabTeamId={collabTeamId}
            onCollabTeamId={setCollabTeamId}
            onAddCollaborator={handleAddCollaborator}
            folderPath={folderPath}
            folderLabel={folderLabel}
            gitUrl={gitUrl}
            onFolderPath={setFolderPath}
            onFolderLabel={setFolderLabel}
            onGitUrl={setGitUrl}
            onPickFolder={handlePickFolder}
            onAddFolder={handleAddFolder}
            onAddGit={handleAddGitRepo}
            onExtract={handleExtractTeam}
            onRemoveFolder={handleRemoveFolder}
            onOpenVault={() =>
              onOpenVault(
                String((node.meta as { team_id?: string })?.team_id || "")
              )
            }
            isManager={isManager}
            viewer={viewer}
            busy={busy}
          />
        ) : null}
        {isRepo ? (
          <RepoBody
            node={node}
            onRemove={() =>
              handleRemoveFolder(
                String(
                  (node.meta as { mapping_id?: string })?.mapping_id || ""
                )
              )
            }
            busy={busy}
          />
        ) : null}
      </div>
    </aside>
  );
}

function nodeKindLabel(t: string): string {
  if (t === "global_team") return "GLOBAL TEAM";
  if (t === "folder") return "LOCAL FOLDER";
  if (t === "session") return "AGENT SESSION";
  return t.toUpperCase();
}

function FolderBody({
  node,
  sessions,
  shared,
  onToggleShare,
  onOpenVault,
  onOpenSession,
  busy,
}: {
  node: OrgNode;
  sessions: OrgNode[];
  shared: boolean;
  onToggleShare: () => void;
  onOpenVault: () => void;
  onOpenSession: (sessionId: string, taskId?: string | null) => void;
  busy: string | null;
}) {
  const meta = metaRecord(node);
  const path = String(meta.path || "");
  const activeSessions = Number(meta.active_session_count || 0);
  const manager =
    typeof meta.context_manager === "object" && meta.context_manager
      ? (meta.context_manager as Record<string, unknown>)
      : null;
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>
      <button onClick={onOpenVault} style={primaryBtnFilled(false)}>
        OPEN CONTEXT →
      </button>
      <div>
        <SectionHeader>Folder</SectionHeader>
        <div
          style={{
            marginTop: 6,
            padding: "8px 10px",
            border: "1px solid var(--border)",
            borderRadius: 4,
            background: "var(--surface)",
            display: "grid",
            gap: 4,
          }}
        >
          <KeyValue label="path" value={path || node.label} mono />
          <KeyValue label="sessions" value={`${sessions.length}`} />
          <KeyValue label="active" value={`${activeSessions}`} />
        </div>
      </div>
      <div>
        <SectionHeader>Context manager</SectionHeader>
        <div
          style={{
            marginTop: 6,
            padding: "8px 10px",
            border: "1px solid var(--border)",
            borderRadius: 4,
            background: "var(--surface)",
            display: "grid",
            gap: 4,
          }}
        >
          <KeyValue
            label="owner"
            value={String(manager?.display_name || `${node.label} Context Manager`)}
          />
          <KeyValue
            label="scope"
            value={String(manager?.folder_path || path || node.label)}
            mono
          />
        </div>
      </div>
      <div>
        <SectionHeader>Context sharing</SectionHeader>
        <button
          onClick={onToggleShare}
          disabled={busy === "share-folder"}
          style={shared ? primaryBtnFilled(busy === "share-folder") : primaryBtn(busy === "share-folder")}
        >
          {busy === "share-folder"
            ? "UPDATING..."
            : shared
              ? "SHARING ENABLED"
              : "SHARE THIS FOLDER"}
        </button>
        <Hint>
          Shared folders exchange local context with the other folders you enable here.
        </Hint>
      </div>
      <div>
        <SectionHeader>Agent sessions ({sessions.length})</SectionHeader>
        {sessions.length === 0 ? (
          <Hint>No Claude Code or Codex sessions detected for this folder yet.</Hint>
        ) : (
          <div style={{ display: "grid", gap: 4, marginTop: 6 }}>
            {sessions.map((session) => {
              const smeta = metaRecord(session);
              const color = runtimeColor(smeta.runtime);
              return (
                <div
                  key={session.id}
                  style={{
                    padding: "7px 10px",
                    border: "1px solid var(--border)",
                    borderLeft: `3px solid ${color}`,
                    borderRadius: 4,
                    background: "var(--surface)",
                    display: "grid",
                    gridTemplateColumns: "minmax(0, 1fr) auto",
                    gap: 8,
                    alignItems: "center",
                  }}
                >
                  <div style={{ minWidth: 0 }}>
                    <div
                      style={{
                        fontSize: 12,
                        color: "var(--ink)",
                        overflow: "hidden",
                        textOverflow: "ellipsis",
                        whiteSpace: "nowrap",
                      }}
                      title={session.label}
                    >
                      {session.label}
                    </div>
                    <div style={{ fontFamily: "var(--mono)", fontSize: 10, color }}>
                      {String(smeta.runtime || "agent")} · {String(smeta.state || "recent")}
                    </div>
                  </div>
                  <button
                    onClick={() => onOpenSession(session.id, sessionTaskId(session))}
                    style={smallActionBtn(color)}
                  >
                    OPEN
                  </button>
                </div>
              );
            })}
          </div>
        )}
      </div>
    </div>
  );
}

function SessionBody({
  node,
  onOpenSession,
}: {
  node: OrgNode;
  onOpenSession: () => void;
}) {
  const meta = metaRecord(node);
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>
      <button onClick={onOpenSession} style={primaryBtnFilled(false)}>
        OPEN SESSION TASK →
      </button>
      <div>
        <SectionHeader>Session</SectionHeader>
        <div
          style={{
            marginTop: 6,
            padding: "8px 10px",
            border: "1px solid var(--border)",
            borderRadius: 4,
            background: "var(--surface)",
            display: "grid",
            gap: 4,
          }}
        >
          <KeyValue label="runtime" value={String(meta.runtime || "agent")} />
          <KeyValue label="state" value={String(meta.state || "recent")} />
          {meta.model ? <KeyValue label="model" value={String(meta.model)} /> : null}
          {meta.cwd ? <KeyValue label="folder" value={String(meta.cwd)} mono /> : null}
          {meta.updated_at ? <KeyValue label="updated" value={String(meta.updated_at)} /> : null}
        </div>
      </div>
      {meta.preview ? (
        <div>
          <SectionHeader>Preview</SectionHeader>
          <div
            style={{
              marginTop: 6,
              padding: "8px 10px",
              border: "1px solid var(--border)",
              borderRadius: 4,
              background: "var(--surface)",
              color: "var(--ink2)",
              fontSize: 12,
              lineHeight: 1.5,
              whiteSpace: "pre-wrap",
            }}
          >
            {String(meta.preview)}
          </div>
        </div>
      ) : null}
    </div>
  );
}

// ─── Workspace ─────────────────────────────────────────────────────────────
function WorkspaceBody({
  projects,
  projectName,
  onProjectName,
  onCreateProject,
  confirmReset,
  onAskReset,
  onCancelReset,
  onConfirmReset,
  busy,
}: {
  projects: OrgNode[];
  projectName: string;
  onProjectName: (s: string) => void;
  onCreateProject: () => void;
  confirmReset: boolean;
  onAskReset: () => void;
  onCancelReset: () => void;
  onConfirmReset: () => void;
  busy: string | null;
}) {
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
      <div>
        <SectionHeader>Add a project</SectionHeader>
        <div style={{ display: "flex", gap: 6, marginTop: 8 }}>
          <input
            value={projectName}
            onChange={(e) => onProjectName(e.target.value)}
            placeholder="e.g. Text_to_Speech"
            onKeyDown={(e) => {
              if (e.key === "Enter") onCreateProject();
            }}
            style={inputStyle}
          />
          <button
            onClick={onCreateProject}
            disabled={busy === "create-project" || !projectName.trim()}
            style={primaryBtn(busy === "create-project")}
          >
            CREATE
          </button>
        </div>
      </div>

      <div>
        <SectionHeader>Projects ({projects.length})</SectionHeader>
        {projects.length === 0 ? (
          <Hint>No projects yet. Add one above.</Hint>
        ) : (
          <div style={{ display: "flex", flexDirection: "column", gap: 4, marginTop: 8 }}>
            {projects.map((p) => (
              <div
                key={p.id}
                style={{
                  padding: "6px 10px",
                  border: "1px solid var(--border)",
                  borderRadius: 4,
                  background: "var(--surface)",
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "space-between",
                  fontSize: 12,
                  color: "var(--ink)",
                }}
              >
                <span>{p.label}</span>
                <Pill label="open" tone="default" />
              </div>
            ))}
          </div>
        )}
      </div>

      <div
        style={{
          marginTop: 8,
          paddingTop: 14,
          borderTop: "1px solid var(--border)",
        }}
      >
        <SectionHeader>Danger zone</SectionHeader>
        {!confirmReset ? (
          <button onClick={onAskReset} style={dangerBtn}>
            RESET WORKSPACE
          </button>
        ) : (
          <div
            style={{
              marginTop: 8,
              padding: 10,
              border: "1px solid var(--rose)",
              background: "var(--rose-dim)",
              borderRadius: 4,
              fontSize: 12,
              color: "var(--ink)",
            }}
          >
            <div style={{ marginBottom: 8 }}>
              This deletes projects, teams, folders, context items, proposals, and findings for this org. Memory engrams in the Dhee tier are not affected. Continue?
            </div>
            <div style={{ display: "flex", gap: 6 }}>
              <button
                onClick={onConfirmReset}
                disabled={busy === "reset"}
                style={dangerBtn}
              >
                {busy === "reset" ? "RESETTING…" : "YES, RESET"}
              </button>
              <button onClick={onCancelReset} style={ghostBtn}>
                CANCEL
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

// ─── Project ───────────────────────────────────────────────────────────────
function ProjectBody({
  teams,
  teamName,
  onTeamName,
  onCreateTeam,
  confirmDelete,
  onAskDelete,
  onCancelDelete,
  onConfirmDelete,
  busy,
}: {
  teams: OrgNode[];
  teamName: string;
  onTeamName: (s: string) => void;
  onCreateTeam: () => void;
  confirmDelete: boolean;
  onAskDelete: () => void;
  onCancelDelete: () => void;
  onConfirmDelete: () => void;
  busy: string | null;
}) {
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
      <div>
        <SectionHeader>Add a team</SectionHeader>
        <div style={{ display: "flex", gap: 6, marginTop: 8 }}>
          <input
            value={teamName}
            onChange={(e) => onTeamName(e.target.value)}
            placeholder="Backend, Frontend, Data, Mobile"
            onKeyDown={(e) => {
              if (e.key === "Enter") onCreateTeam();
            }}
            style={inputStyle}
          />
          <button
            onClick={onCreateTeam}
            disabled={busy === "create-team" || !teamName.trim()}
            style={primaryBtn(busy === "create-team")}
          >
            ADD
          </button>
        </div>
      </div>

      <div>
        <SectionHeader>Teams ({teams.length})</SectionHeader>
        {teams.length === 0 ? (
          <Hint>No teams yet.</Hint>
        ) : (
          <div style={{ display: "flex", flexDirection: "column", gap: 4, marginTop: 8 }}>
            {teams.map((team) => {
              const mappings = uniqueMappings(repoMappingsFromNode(team));
              return (
                <div
                  key={team.id}
                  style={{
                    padding: "6px 10px",
                    border: "1px solid var(--border)",
                    borderRadius: 4,
                    background: "var(--surface)",
                    display: "flex",
                    alignItems: "center",
                    justifyContent: "space-between",
                    gap: 8,
                  }}
                >
                  <span style={{ color: "var(--ink)", fontSize: 12 }}>
                    {team.label}
                  </span>
                  <Pill
                    label={`${mappings.length} ${mappings.length === 1 ? "repo" : "repos"}`}
                    tone={mappings.length ? "green" : "default"}
                  />
                </div>
              );
            })}
          </div>
        )}
      </div>
      <div
        style={{
          marginTop: 8,
          paddingTop: 14,
          borderTop: "1px solid var(--border)",
        }}
      >
        {!confirmDelete ? (
          <button onClick={onAskDelete} style={dangerBtn}>
            DELETE PROJECT
          </button>
        ) : (
          <div
            style={{
              marginTop: 4,
              padding: 10,
              border: "1px solid var(--rose)",
              background: "var(--rose-dim)",
              borderRadius: 4,
              fontSize: 12,
              color: "var(--ink)",
            }}
          >
            <div style={{ marginBottom: 8 }}>
              Deletes the project and all its teams + context. Continue?
            </div>
            <div style={{ display: "flex", gap: 6 }}>
              <button
                onClick={onConfirmDelete}
                disabled={busy === "delete-project"}
                style={dangerBtn}
              >
                {busy === "delete-project" ? "DELETING…" : "YES, DELETE"}
              </button>
              <button onClick={onCancelDelete} style={ghostBtn}>
                CANCEL
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

function TeamBody({
  node,
  repoMappings,
  developerCount,
  developerJoinEvents,
  collaboratingTeams,
  collaboratorOptions,
  collabTeamId,
  onCollabTeamId,
  onAddCollaborator,
  folderPath,
  folderLabel,
  gitUrl,
  onFolderPath,
  onFolderLabel,
  onGitUrl,
  onPickFolder,
  onAddFolder,
  onAddGit,
  onExtract,
  onRemoveFolder,
  onOpenVault,
  busy,
}: {
  node: OrgNode;
  repoMappings: RepoMapping[];
  developerCount: number;
  developerJoinEvents: JoinEvent[];
  collaboratingTeams: CollaboratingTeam[];
  collaboratorOptions: OrgNode[];
  collabTeamId: string;
  onCollabTeamId: (value: string) => void;
  onAddCollaborator: () => void;
  folderPath: string;
  folderLabel: string;
  gitUrl: string;
  onFolderPath: (value: string) => void;
  onFolderLabel: (value: string) => void;
  onGitUrl: (value: string) => void;
  onPickFolder: () => void;
  onAddFolder: () => void;
  onAddGit: () => void;
  onExtract: () => void;
  onRemoveFolder: (mappingId?: string | null) => void;
  onOpenVault: () => void;
  isManager: boolean;
  viewer: Viewer | null;
  busy: string | null;
}) {
  const meta = metaRecord(node);
  const manager = meta.context_manager as Record<string, string> | undefined;
  const teamId = String(meta.team_id || "");
  const projectId = String(meta.project_id || "");
  const mappings = uniqueMappings(repoMappings);
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>
      <button onClick={onOpenVault} style={primaryBtnFilled(false)}>
        OPEN CONTEXT →
      </button>
      <div>
        <SectionHeader>Team details</SectionHeader>
        <div
          style={{
            marginTop: 6,
            display: "grid",
            gap: 4,
            padding: "8px 10px",
            border: "1px solid var(--border)",
            borderRadius: 4,
            background: "var(--surface)",
            fontSize: 12,
          }}
        >
          <KeyValue label="team" value={teamId || node.label} />
          {projectId ? <KeyValue label="project" value={projectId} /> : null}
          <KeyValue
            label="git access"
            value={`${developerCount} dev${developerCount === 1 ? "" : "s"} joined`}
          />
        </div>
      </div>
      <div>
        <SectionHeader>Manager</SectionHeader>
        <div
          style={{
            marginTop: 6,
            padding: "8px 10px",
            border: "1px solid var(--border)",
            borderRadius: 4,
            background: "var(--surface)",
            fontSize: 12,
          }}
        >
          {manager?.display_name ? (
            <>
              <div>{manager.display_name}</div>
              <div style={{ fontSize: 10, color: "var(--ink3)" }}>
                {manager.manager_id}
              </div>
            </>
          ) : (
            <span style={{ color: "var(--ink3)" }}>no manager assigned</span>
          )}
        </div>
      </div>
      <div>
        <SectionHeader>Add a local folder</SectionHeader>
        <div style={{ display: "flex", gap: 6, marginTop: 8 }}>
          <input
            value={folderPath}
            onChange={(e) => onFolderPath(e.target.value)}
            placeholder="/Users/me/code/backend"
            onKeyDown={(e) => {
              if (e.key === "Enter") onAddFolder();
            }}
            style={inputStyle}
          />
          <button
            onClick={onPickFolder}
            disabled={busy === "pick-folder"}
            style={ghostBtn}
            title="Browse"
          >
            BROWSE
          </button>
        </div>
        <div style={{ display: "flex", gap: 6, marginTop: 6 }}>
          <input
            value={folderLabel}
            onChange={(e) => onFolderLabel(e.target.value)}
            placeholder="Optional label"
            style={inputStyle}
          />
          <button
            onClick={onAddFolder}
            disabled={busy === "add-folder" || !folderPath.trim()}
            style={primaryBtn(busy === "add-folder")}
          >
            ADD
          </button>
        </div>
      </div>
      <div>
        <SectionHeader>Add a git repo</SectionHeader>
        <div style={{ display: "flex", gap: 6, marginTop: 8 }}>
          <input
            value={gitUrl}
            onChange={(e) => onGitUrl(e.target.value)}
            placeholder="git@github.com:org/backend.git"
            onKeyDown={(e) => {
              if (e.key === "Enter") onAddGit();
            }}
            style={inputStyle}
          />
          <button
            onClick={onAddGit}
            disabled={busy === "add-git" || !gitUrl.trim()}
            style={primaryBtn(busy === "add-git")}
          >
            ADD
          </button>
        </div>
      </div>
      <div>
        <div
          style={{
            display: "flex",
            alignItems: "center",
            justifyContent: "space-between",
            gap: 8,
          }}
        >
          <SectionHeader>Git + local folders ({mappings.length})</SectionHeader>
          <button
            onClick={onExtract}
            disabled={busy === "extract" || mappings.length === 0}
            title="Run AST extraction for this team's local folders"
            style={primaryBtn(busy === "extract")}
          >
            {busy === "extract" ? "INDEXING..." : "INDEX TEAM"}
          </button>
        </div>
        {mappings.length === 0 ? (
          <Hint>None mapped to this team.</Hint>
        ) : (
          <div style={{ display: "flex", flexDirection: "column", gap: 4, marginTop: 6 }}>
            {mappings.map((mapping) => {
              const key = String(mapping.mapping_id || mapping.local_path || mapping.repo_url);
              return (
                <div
                  key={key}
                  style={{
                    padding: "8px 10px",
                    border: "1px solid var(--border)",
                    borderRadius: 4,
                    background: "var(--surface)",
                    display: "grid",
                    gap: 4,
                  }}
                >
                  <div
                    style={{
                      display: "flex",
                      alignItems: "center",
                      justifyContent: "space-between",
                      gap: 8,
                    }}
                  >
                    <div style={{ fontSize: 12, color: "var(--ink)" }}>
                      {repoMappingLabel(mapping)}
                    </div>
                    <button
                      onClick={() => onRemoveFolder(mapping.mapping_id)}
                      style={iconBtn}
                      title="Remove mapping"
                      aria-label="Remove mapping"
                    >
                      ×
                    </button>
                  </div>
                  {mapping.repo_url ? (
                    <KeyValue label="repo" value={String(mapping.repo_url)} mono />
                  ) : null}
                  {mapping.local_path ? (
                    <KeyValue label="folder" value={String(mapping.local_path)} mono />
                  ) : null}
                </div>
              );
            })}
          </div>
        )}
      </div>
      {developerJoinEvents.length ? (
        <div>
          <SectionHeader>Recent joins</SectionHeader>
          <div style={{ display: "grid", gap: 4, marginTop: 6 }}>
            {developerJoinEvents.slice(0, 4).map((event, idx) => (
              <div
                key={`${event.repo_root || "join"}-${idx}`}
                style={{
                  padding: "6px 10px",
                  border: "1px solid var(--border)",
                  borderRadius: 4,
                  background: "var(--surface)",
                  fontSize: 11,
                  color: "var(--ink2)",
                }}
              >
                <div style={{ fontFamily: "var(--mono)", overflow: "hidden", textOverflow: "ellipsis" }}>
                  {event.repo_root || "workspace"}
                </div>
                <div style={{ color: "var(--ink3)", marginTop: 2 }}>
                  {event.role || "developer"} - {event.received_at || "recent"}
                </div>
              </div>
            ))}
          </div>
        </div>
      ) : null}
      <div>
        <SectionHeader>Collaborate teams</SectionHeader>
        {collaboratingTeams.length === 0 ? (
          <Hint>No team context shares yet.</Hint>
        ) : (
          <div style={{ display: "flex", flexWrap: "wrap", gap: 6, marginTop: 6 }}>
            {collaboratingTeams.map((team) => (
              <Pill
                key={String(team.team_id || team.name)}
                label={String(team.name || team.team_id)}
                tone="default"
              />
            ))}
          </div>
        )}
        <div style={{ display: "flex", gap: 6, marginTop: 8 }}>
          <select
            value={collabTeamId}
            onChange={(e) => onCollabTeamId(e.target.value)}
            style={inputStyle}
          >
            <option value="">Select team</option>
            {collaboratorOptions.map((team) => {
              const optionTeamId = String((team.meta as { team_id?: string })?.team_id || "");
              return (
                <option key={team.id} value={optionTeamId}>
                  {team.label}
                </option>
              );
            })}
          </select>
          <button
            onClick={onAddCollaborator}
            disabled={busy === "collaborate" || !collabTeamId}
            style={primaryBtn(busy === "collaborate")}
          >
            ADD
          </button>
        </div>
      </div>
    </div>
  );
}

function RepoBody({
  node,
  onRemove,
  busy,
}: {
  node: OrgNode;
  onRemove: () => void;
  busy: string | null;
}) {
  const meta = (node.meta as {
    repo_url?: string;
    local_path?: string;
    mapping_id?: string;
  }) || {};
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
      <SectionHeader>Folder / path</SectionHeader>
      <div
        style={{
          padding: "8px 10px",
          border: "1px solid var(--border)",
          borderRadius: 4,
          background: "var(--surface)",
          fontFamily: "var(--mono)",
          fontSize: 11,
          color: "var(--ink2)",
          wordBreak: "break-all",
        }}
      >
        {meta.local_path || meta.repo_url || node.label}
      </div>
      <button onClick={onRemove} disabled={busy === "remove-folder"} style={dangerBtn}>
        {busy === "remove-folder" ? "REMOVING…" : "REMOVE"}
      </button>
    </div>
  );
}

// ─── Atoms ─────────────────────────────────────────────────────────────────

function KeyValue({
  label,
  value,
  mono = false,
}: {
  label: string;
  value: string;
  mono?: boolean;
}) {
  return (
    <div
      style={{
        display: "grid",
        gridTemplateColumns: "82px minmax(0, 1fr)",
        gap: 8,
        alignItems: "baseline",
      }}
    >
      <span
        style={{
          fontFamily: "var(--mono)",
          fontSize: 10,
          color: "var(--ink3)",
          textTransform: "uppercase",
        }}
      >
        {label}
      </span>
      <span
        title={value}
        style={{
          minWidth: 0,
          overflow: "hidden",
          textOverflow: "ellipsis",
          whiteSpace: "nowrap",
          fontFamily: mono ? "var(--mono)" : undefined,
          fontSize: mono ? 10 : 12,
          color: "var(--ink2)",
        }}
      >
        {value}
      </span>
    </div>
  );
}

function Hint({ children }: { children: React.ReactNode }) {
  return (
    <div style={{ fontSize: 11, color: "var(--ink3)", marginTop: 6 }}>
      {children}
    </div>
  );
}

function Pill({
  label,
  tone = "default",
}: {
  label: string;
  tone?: "default" | "green" | "indigo" | "rose" | "accent";
}) {
  const map = {
    default: { bg: "var(--surface)", fg: "var(--ink2)" },
    green: { bg: "var(--green-dim)", fg: "var(--green)" },
    indigo: { bg: "var(--indigo-dim)", fg: "var(--indigo)" },
    rose: { bg: "var(--rose-dim)", fg: "var(--rose)" },
    accent: { bg: "var(--accent-dim)", fg: "var(--accent)" },
  } as const;
  const c = map[tone];
  return (
    <span
      style={{
        display: "inline-flex",
        padding: "2px 7px",
        borderRadius: 3,
        background: c.bg,
        color: c.fg,
        fontFamily: "var(--mono)",
        fontSize: 9,
        letterSpacing: "0.04em",
      }}
    >
      {label}
    </span>
  );
}

const inputStyle: React.CSSProperties = {
  flex: 1,
  fontFamily: "var(--mono)",
  fontSize: 11,
  padding: "6px 8px",
  background: "var(--surface)",
  border: "1px solid var(--border)",
  borderRadius: 3,
  color: "var(--ink)",
};

function primaryBtn(busy: boolean): React.CSSProperties {
  return {
    fontFamily: "var(--mono)",
    fontSize: 10,
    padding: "5px 12px",
    background: busy ? "var(--surface)" : "var(--accent-dim)",
    color: "var(--accent)",
    border: "1px solid var(--accent)",
    borderRadius: 3,
    cursor: busy ? "wait" : "pointer",
  };
}

function primaryBtnFilled(busy: boolean): React.CSSProperties {
  return {
    fontFamily: "var(--mono)",
    fontSize: 11,
    padding: "8px 12px",
    background: busy ? "var(--surface)" : "var(--accent-dim)",
    color: "var(--accent)",
    border: "1px solid var(--accent)",
    borderRadius: 4,
    textAlign: "center" as const,
    cursor: busy ? "wait" : "pointer",
  };
}

function smallActionBtn(color: string): React.CSSProperties {
  return {
    fontFamily: "var(--mono)",
    fontSize: 9,
    padding: "5px 8px",
    background: "white",
    color,
    border: `1px solid ${color}`,
    borderRadius: 3,
    cursor: "pointer",
    whiteSpace: "nowrap",
  };
}

const ghostBtn: React.CSSProperties = {
  fontFamily: "var(--mono)",
  fontSize: 10,
  padding: "5px 10px",
  background: "var(--surface)",
  color: "var(--ink2)",
  border: "1px solid var(--border)",
  borderRadius: 3,
};

const dangerBtn: React.CSSProperties = {
  fontFamily: "var(--mono)",
  fontSize: 10,
  padding: "5px 12px",
  background: "var(--rose-dim)",
  color: "var(--rose)",
  border: "1px solid var(--rose)",
  borderRadius: 3,
};

const iconBtn: React.CSSProperties = {
  width: 22,
  height: 22,
  borderRadius: 3,
  background: "var(--surface)",
  color: "var(--ink2)",
  border: "1px solid var(--border)",
  fontSize: 12,
  lineHeight: 1,
};
