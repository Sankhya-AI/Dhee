import { useEffect, useMemo, useState } from "react";
import { api } from "../api";
import { SectionHeader } from "../components/ui/SectionHeader";
import { StatPill } from "../components/ui/StatPill";
import { TierBadge } from "../components/ui/TierBadge";
import type {
  Engram,
  ProjectIndexSnapshot,
  RuntimeStatusCard,
  SankhyaTask,
  WorkspaceGraphSnapshot,
} from "../types";

export function LaunchView({
  tasks,
  memories,
  projectIndex,
  selectedWorkspaceId,
  workspaceGraph,
  onLaunched,
}: {
  tasks: SankhyaTask[];
  memories: Engram[];
  projectIndex?: ProjectIndexSnapshot | null;
  selectedWorkspaceId?: string;
  workspaceGraph?: WorkspaceGraphSnapshot | null;
  onLaunched?: (taskId: string | null, runtime: string) => void;
}) {
  const [runtime, setRuntime] = useState<"claude-code" | "codex" | "both">(
    "claude-code"
  );
  const [selectedTask, setSelectedTask] = useState<string>(tasks[0]?.id || "");
  const [customTask, setCustomTask] = useState("");
  const [launching, setLaunching] = useState(false);
  const [step, setStep] = useState(-1);
  const [done, setDone] = useState(false);
  const [serverMessage, setServerMessage] = useState<string | null>(null);
  const [runtimeCards, setRuntimeCards] = useState<RuntimeStatusCard[]>([]);
  const [runtimeError, setRuntimeError] = useState<string | null>(null);
  const [permissionMode, setPermissionMode] = useState<"standard" | "full-access">(
    "standard"
  );

  const runtimes = [
    {
      id: "claude-code" as const,
      label: "Claude Code",
      sub: "native hooks · shared kernel",
      cmd: "dhee install --harness claude-code",
    },
    {
      id: "codex" as const,
      label: "Codex",
      sub: "config.toml · stream sync",
      cmd: "dhee install --harness codex",
    },
    {
      id: "both" as const,
      label: "Both",
      sub: "one shared memory kernel",
      cmd: "dhee install --harness all",
    },
  ];

  const taskTitle = (
    tasks.find((t) => t.id === selectedTask)?.title || customTask
  ).trim();
  const contextMemories = memories
    .filter((m) => m.tier === "canonical" || m.tier === "high")
    .slice(0, 4);
  const contextTokens = contextMemories.reduce((a, m) => a + m.tokens, 0);
  const liveSessions = workspaceGraph?.sessions?.slice(0, 4) || [];
  const workspaces =
    projectIndex?.workspaces?.map((workspace) => ({
      id: workspace.id,
      label: workspace.label || workspace.name,
    })) || [];
  const [selectedWorkspace, setSelectedWorkspace] = useState(
    selectedWorkspaceId || workspaces[0]?.id || ""
  );
  const selectedRuntimeCards = useMemo(() => {
    if (runtime === "both") return runtimeCards;
    return runtimeCards.filter((card) => card.id === runtime);
  }, [runtime, runtimeCards]);

  useEffect(() => {
    (async () => {
      try {
        const snapshot = await api.runtimeStatus();
        setRuntimeCards(snapshot.runtimes || []);
        setRuntimeError(snapshot.error || null);
      } catch (e) {
        setRuntimeError(String(e));
      }
    })();
  }, []);

  const steps = [
    "Initialising Dhee kernel…",
    `Assembling context slice (${contextMemories.length} memories, ~${contextTokens} tokens)…`,
    `Loading samskara log · ${memories.length} engrams indexed…`,
    `Starting ${
      runtime === "both"
        ? "Claude Code + Codex"
        : runtimes.find((r) => r.id === runtime)?.label
    } harness…`,
    "Memory hooks active · router enforcement on…",
    "✓ Ready.",
  ];

  const launch = async () => {
    if (!taskTitle) return;
    setLaunching(true);
    setStep(0);
    setDone(false);
    try {
      const workspaceId = selectedWorkspace || selectedWorkspaceId || workspaces[0]?.id;
      if (!workspaceId) throw new Error("No workspace selected");
      const res = await api.launchWorkspaceSession(
        workspaceId,
        runtime,
        taskTitle,
        runtime === "claude-code" ? permissionMode : undefined,
        selectedTask || undefined
      );
      setServerMessage(
        `${res.control_state} · ${res.launch_command}`
      );
      const snapshot = await api.runtimeStatus().catch(() => null);
      if (snapshot?.runtimes) setRuntimeCards(snapshot.runtimes);
      onLaunched?.(res.task_id || selectedTask || null, runtime);
      setDone(true);
    } catch (e) {
      setServerMessage(String(e));
      setDone(false);
    } finally {
      setLaunching(false);
    }
  };

  return (
    <div
      style={{ display: "flex", flexDirection: "column", height: "100%" }}
    >
      <div
        style={{
          borderBottom: "1px solid var(--border)",
          padding: "0 24px",
          height: 48,
          display: "flex",
          alignItems: "center",
          gap: 10,
          flexShrink: 0,
        }}
      >
        <span
          style={{
            fontFamily: "var(--mono)",
            fontSize: 11,
            fontWeight: 700,
            letterSpacing: "0.06em",
          }}
        >
          LAUNCH
        </span>
        <span
          style={{
            fontFamily: "var(--mono)",
            fontSize: 10,
            color: "var(--ink3)",
          }}
        >
          no middle-man · claude code orchestrates · dhee is the substrate
        </span>
      </div>

      <div style={{ flex: 1, overflowY: "auto", padding: "28px 24px" }}>
        {!launching ? (
          <div style={{ maxWidth: 640 }}>
            <div style={{ marginBottom: 28 }}>
              <SectionHeader label="Workspace" />
              <select
                value={selectedWorkspace}
                onChange={(e) => setSelectedWorkspace(e.target.value)}
                style={{
                  width: "100%",
                  border: "1px solid var(--border)",
                  padding: "10px 12px",
                  fontFamily: "var(--mono)",
                  fontSize: 11,
                  marginBottom: 14,
                }}
              >
                {workspaces.map((workspace) => (
                  <option key={workspace.id} value={workspace.id}>
                    {workspace.label}
                  </option>
                ))}
              </select>
            </div>

            <div style={{ marginBottom: 28 }}>
              <SectionHeader label="Runtime" />
              <div style={{ display: "flex", gap: 10 }}>
                {runtimes.map((r) => (
                  <div
                    key={r.id}
                    onClick={() => setRuntime(r.id)}
                    style={{
                      flex: 1,
                      padding: "14px",
                      border: `1.5px solid ${
                        runtime === r.id ? "var(--accent)" : "var(--border)"
                      }`,
                      cursor: "pointer",
                      background:
                        runtime === r.id ? "oklch(0.97 0.04 36)" : "white",
                      transition: "all 0.12s",
                    }}
                  >
                    <div
                      style={{
                        fontWeight: 600,
                        fontSize: 14,
                        marginBottom: 4,
                      }}
                    >
                      {r.label}
                    </div>
                    <div
                      style={{
                        fontFamily: "var(--mono)",
                        fontSize: 10,
                        color: "var(--ink3)",
                        marginBottom: 8,
                      }}
                    >
                      {r.sub}
                    </div>
                    <div
                      style={{
                        fontFamily: "var(--mono)",
                        fontSize: 9,
                        color: "var(--ink3)",
                      }}
                    >
                      {r.cmd}
                    </div>
                  </div>
                ))}
              </div>
              <div style={{ marginTop: 14, display: "grid", gap: 10 }}>
                {selectedRuntimeCards.map((card) => (
                  <div
                    key={card.id}
                    style={{
                      border: "1px solid var(--border)",
                      padding: "12px 14px",
                      background: "white",
                    }}
                  >
                    <div
                      style={{
                        display: "flex",
                        justifyContent: "space-between",
                        alignItems: "center",
                        gap: 12,
                        marginBottom: 8,
                      }}
                    >
                      <div style={{ fontSize: 13, fontWeight: 600 }}>{card.label}</div>
                      <StatPill
                        label={card.installed ? "attached" : "not attached"}
                        tone={card.installed ? "var(--green)" : "var(--rose)"}
                      />
                    </div>
                    <div style={{ fontSize: 12, color: "var(--ink2)", marginBottom: 5 }}>
                      {card.currentSession?.title || card.currentSession?.cwd || "No active session in this repo"}
                    </div>
                    <div style={{ fontFamily: "var(--mono)", fontSize: 9, color: "var(--ink3)" }}>
                      limit: {card.limits.state}
                      {card.limits.resetAt ? ` · reset ${new Date(card.limits.resetAt).toLocaleString()}` : ""}
                    </div>
                  </div>
                ))}
                {runtimeError && (
                  <div style={{ fontSize: 12, color: "var(--rose)" }}>{runtimeError}</div>
                )}
              </div>
              {runtime === "claude-code" && (
                <div style={{ marginTop: 14 }}>
                  <SectionHeader label="Claude Permissions" sub="full access is explicit and visible" />
                  <div style={{ display: "flex", gap: 10 }}>
                    {[
                      ["standard", "standard permissions"],
                      ["full-access", "full access"],
                    ].map(([id, label]) => (
                      <button
                        key={id}
                        onClick={() => setPermissionMode(id as "standard" | "full-access")}
                        style={{
                          padding: "8px 12px",
                          border: `1px solid ${
                            permissionMode === id ? "var(--accent)" : "var(--border)"
                          }`,
                          background:
                            permissionMode === id ? "oklch(0.97 0.04 36)" : "white",
                          fontFamily: "var(--mono)",
                          fontSize: 10,
                          color:
                            permissionMode === id ? "var(--accent)" : "var(--ink2)",
                        }}
                      >
                        {label}
                      </button>
                    ))}
                  </div>
                </div>
              )}
            </div>

            <div style={{ marginBottom: 28 }}>
              <SectionHeader label="Task" />
              <div style={{ marginBottom: 8 }}>
                {tasks.map((t) => {
                  const c =
                    ({
                      green: "var(--green)",
                      indigo: "var(--indigo)",
                      orange: "var(--accent)",
                      rose: "var(--rose)",
                    } as Record<string, string>)[t.color] || "var(--green)";
                  return (
                    <div
                      key={t.id}
                      onClick={() => {
                        setSelectedTask(t.id);
                        setCustomTask("");
                      }}
                      style={{
                        padding: "10px 14px",
                        marginBottom: 4,
                        border: `1px solid ${
                          selectedTask === t.id
                            ? "var(--accent)"
                            : "var(--border)"
                        }`,
                        cursor: "pointer",
                        display: "flex",
                        gap: 10,
                        alignItems: "center",
                        background:
                          selectedTask === t.id
                            ? "oklch(0.97 0.04 36)"
                            : "white",
                      }}
                    >
                      <div
                        style={{
                          width: 7,
                          height: 7,
                          background: c,
                          flexShrink: 0,
                        }}
                      />
                      <span style={{ fontSize: 13, flex: 1 }}>{t.title}</span>
                      {selectedTask === t.id && (
                        <span
                          style={{
                            fontFamily: "var(--mono)",
                            fontSize: 9,
                            color: "var(--accent)",
                          }}
                        >
                          SELECTED
                        </span>
                      )}
                    </div>
                  );
                })}
              </div>
              <div
                style={{
                  fontFamily: "var(--mono)",
                  fontSize: 9,
                  color: "var(--ink3)",
                  marginBottom: 5,
                }}
              >
                OR NEW TASK:
              </div>
              <textarea
                value={customTask}
                onChange={(e) => {
                  setCustomTask(e.target.value);
                  setSelectedTask("");
                }}
                placeholder="Describe the task…"
                rows={2}
                style={{
                  width: "100%",
                  border: "1px solid var(--border)",
                  padding: "10px",
                  fontFamily: "var(--font)",
                  fontSize: 13,
                  color: "var(--ink)",
                  background: "white",
                  resize: "none",
                  outline: "none",
                }}
              />
            </div>

            {contextMemories.length > 0 && (
              <div style={{ marginBottom: 28 }}>
                <SectionHeader
                  label="Memory Context Preview"
                  sub={`~${contextTokens} tokens · Dhee will inject before launch`}
                />
                <div style={{ border: "1px solid var(--border)", background: "white" }}>
                  {contextMemories.map((m, i) => (
                    <div
                      key={m.id}
                      style={{
                        padding: "10px 14px",
                        borderBottom:
                          i < contextMemories.length - 1
                            ? "1px solid var(--surface2)"
                            : "none",
                        display: "flex",
                        gap: 10,
                        alignItems: "flex-start",
                      }}
                    >
                      <TierBadge tier={m.tier} />
                      <span
                        style={{
                          fontSize: 12.5,
                          color: "var(--ink2)",
                          lineHeight: 1.4,
                        }}
                      >
                        {m.content.slice(0, 90)}
                        {m.content.length > 90 ? "…" : ""}
                      </span>
                    </div>
                  ))}
                </div>
              </div>
            )}

            {liveSessions.length > 0 && (
              <div style={{ marginBottom: 28 }}>
                <SectionHeader
                  label="Live Repo Sessions"
                  sub="current Codex work already visible to the shared canvas"
                />
                <div style={{ border: "1px solid var(--border)", background: "white" }}>
                  {liveSessions.map((session, index) => (
                    <div
                      key={session.id}
                      style={{
                        padding: "11px 14px",
                        borderBottom:
                          index < liveSessions.length - 1
                            ? "1px solid var(--surface2)"
                            : "none",
                      }}
                    >
                      <div
                        style={{
                          display: "flex",
                          justifyContent: "space-between",
                          gap: 12,
                          marginBottom: 4,
                        }}
                      >
                        <span style={{ fontSize: 12.5, fontWeight: 600 }}>{session.title}</span>
                        {session.isCurrent && (
                          <StatPill label="current" tone="var(--green)" />
                        )}
                      </div>
                      <div style={{ fontSize: 12, color: "var(--ink2)", lineHeight: 1.45 }}>
                        {session.preview || "No preview yet."}
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            )}

            <button
              onClick={launch}
              disabled={!taskTitle}
              style={{
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
              }}
              onMouseEnter={(e) => {
                if (taskTitle)
                  e.currentTarget.style.background = "var(--accent)";
              }}
              onMouseLeave={(e) => {
                if (taskTitle)
                  e.currentTarget.style.background = "var(--ink)";
              }}
            >
              LAUNCH WITH{" "}
              {runtimes.find((r) => r.id === runtime)?.label.toUpperCase()} →
            </button>
          </div>
        ) : (
          <div style={{ maxWidth: 540 }}>
            <div
              style={{
                fontFamily: "var(--mono)",
                fontSize: 9,
                color: "var(--ink3)",
                letterSpacing: "0.08em",
                marginBottom: 20,
              }}
            >
              INITIALISING HARNESS
            </div>
            {steps.map((s, i) => (
              <div
                key={i}
                style={{
                  display: "flex",
                  gap: 12,
                  padding: "7px 0",
                  opacity: i <= step ? 1 : 0.18,
                  transition: "opacity 0.25s",
                }}
              >
                <span
                  style={{
                    fontFamily: "var(--mono)",
                    fontSize: 13,
                    color:
                      i < step
                        ? "var(--green)"
                        : i === step
                        ? "var(--accent)"
                        : "var(--ink3)",
                    flexShrink: 0,
                    width: 16,
                  }}
                >
                  {i < step ? "✓" : i === step ? "›" : "·"}
                </span>
                <span
                  style={{
                    fontFamily: "var(--mono)",
                    fontSize: 13,
                    color: i === step ? "var(--ink)" : "var(--ink2)",
                  }}
                >
                  {s}
                </span>
              </div>
            ))}
            {done && (
              <div
                style={{
                  marginTop: 24,
                  padding: "14px 18px",
                  border: "1px solid var(--green)",
                  background: "oklch(0.96 0.06 145)",
                }}
              >
                <div
                  style={{
                    fontFamily: "var(--mono)",
                    fontSize: 12,
                    color: "var(--green)",
                    marginBottom: 4,
                  }}
                >
                  ✓ HARNESS ACTIVE
                </div>
                <div style={{ fontSize: 12, color: "var(--ink2)" }}>
                  {serverMessage ||
                    "Memory hooks live. Switching to workspace…"}
                </div>
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
