import { useEffect, useMemo, useState } from "react";
import type { CSSProperties, ReactNode } from "react";
import { api } from "../api";
import type { View } from "../components/NavRail";

type AnyRow = Record<string, any>;

function asRows(value: unknown): AnyRow[] {
  return Array.isArray(value) ? (value.filter(Boolean) as AnyRow[]) : [];
}

function get(obj: unknown, key: string, fallback: any = undefined): any {
  if (!obj || typeof obj !== "object") return fallback;
  const row = obj as AnyRow;
  return row[key] ?? fallback;
}

function compact(value?: number | null) {
  const n = Number(value || 0);
  return new Intl.NumberFormat("en", {
    notation: Math.abs(n) >= 10_000 ? "compact" : "standard",
    maximumFractionDigits: Math.abs(n) >= 10_000 ? 1 : 0,
  }).format(n);
}

function money(value?: number | null) {
  const n = Number(value || 0);
  return new Intl.NumberFormat("en-US", {
    style: "currency",
    currency: "USD",
    maximumFractionDigits: n >= 100 ? 0 : 2,
  }).format(n);
}

function shortPath(value?: string | null) {
  const raw = String(value || "");
  if (!raw) return "not linked";
  const parts = raw.split("/").filter(Boolean);
  return parts.length > 3 ? `.../${parts.slice(-3).join("/")}` : raw;
}

function timeLabel(value?: string | number | null) {
  if (!value) return "no timestamp";
  let t: number;
  if (typeof value === "number") {
    t = value < 10_000_000_000 ? value * 1000 : value;
  } else {
    const raw = String(value).trim();
    const numeric = Number(raw);
    t = raw && !Number.isNaN(numeric) ? (numeric < 10_000_000_000 ? numeric * 1000 : numeric) : new Date(raw).getTime();
  }
  if (Number.isNaN(t)) return String(value);
  const delta = Date.now() - t;
  if (delta < 60_000) return "just now";
  if (delta < 3_600_000) return `${Math.floor(delta / 60_000)}m ago`;
  if (delta < 86_400_000) return `${Math.floor(delta / 3_600_000)}h ago`;
  return `${Math.floor(delta / 86_400_000)}d ago`;
}

function learningPreview(row: AnyRow) {
  const preview = String(row.preview || row.body || "").replace(/\s+/g, " ").trim();
  return preview || "No evidence preview captured yet.";
}

function learningMeta(row: AnyRow) {
  const rawChars = Number(row.raw_body_chars || 0);
  const evidenceCount = Number(row.evidence_count || 0);
  return [
    row.kind ? String(row.kind) : null,
    row.scope ? `${String(row.scope)} scope` : null,
    evidenceCount ? `${compact(evidenceCount)} evidence` : null,
    rawChars ? `${compact(rawChars)} raw chars compacted` : null,
    timeLabel(row.updated_at || row.created_at),
  ].filter(Boolean).join(" - ");
}

function toneFor(value?: string | null) {
  const raw = String(value || "").toLowerCase();
  if (raw.includes("reject") || raw.includes("fail") || raw.includes("stale")) return "var(--rose)";
  if (raw.includes("pending") || raw.includes("candidate") || raw.includes("derived")) return "var(--accent)";
  if (raw.includes("promoted") || raw.includes("active") || raw.includes("ok")) return "var(--green)";
  if (raw.includes("evidence") || raw.includes("digest")) return "var(--indigo)";
  return "var(--ink3)";
}

function useScreenData(loader: () => Promise<Record<string, unknown>>) {
  const [data, setData] = useState<Record<string, unknown> | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const refresh = async () => {
    setLoading(true);
    setError("");
    try {
      setData(await loader());
    } catch (e) {
      setError(String(e));
    } finally {
      setLoading(false);
    }
  };
  useEffect(() => {
    void refresh();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  return { data, error, loading, refresh };
}

function Screen({
  eyebrow,
  title,
  subtitle,
  children,
  action,
}: {
  eyebrow: string;
  title: string;
  subtitle: string;
  children: ReactNode;
  action?: ReactNode;
}) {
  return (
    <div className="product-screen">
      <div className="product-content">
        <div className="product-hero">
          <div className="product-hero-copy">
            <div className="product-eyebrow">{eyebrow}</div>
            <h1 className="product-title">{title}</h1>
            <p className="product-subtitle">{subtitle}</p>
          </div>
          {action ? <div className="product-hero-action">{action}</div> : null}
        </div>
        {children}
      </div>
    </div>
  );
}

function Panel({
  label,
  children,
  style,
}: {
  label?: string;
  children: ReactNode;
  style?: CSSProperties;
}) {
  return (
    <section
      className="product-panel"
      style={{
        ...style,
      }}
    >
      {label ? <div className="product-panel-label">{label}</div> : null}
      {children}
    </section>
  );
}

function Metric({ label, value, tone }: { label: string; value: ReactNode; tone?: string }) {
  return (
    <Panel style={{ minHeight: 96 }}>
      <div className="product-metric-value" style={{ color: tone || "var(--ink)" }}>
        {value}
      </div>
      <div className="product-metric-label">{label}</div>
    </Panel>
  );
}

function Pill({ children, tone }: { children: ReactNode; tone?: string }) {
  return (
    <span
      className="product-pill"
      style={{
        color: tone || "var(--ink2)",
      }}
    >
      {children}
    </span>
  );
}

function RowList({
  rows,
  empty,
  render,
}: {
  rows: AnyRow[];
  empty: string;
  render: (row: AnyRow, index: number) => ReactNode;
}) {
  if (!rows.length) {
    return <div className="product-empty">{empty}</div>;
  }
  return <div className="product-list">{rows.map(render)}</div>;
}

function LoadingState({ loading, error }: { loading: boolean; error: string }) {
  if (loading) return <Panel>Loading Dhee state...</Panel>;
  if (error) return <Panel><span style={{ color: "var(--rose)" }}>{error}</span></Panel>;
  return null;
}

export function CommandCenterView({ onNavigate }: { onNavigate: (view: View) => void }) {
  const { data, error, loading, refresh } = useScreenData(api.commandCenter);
  const router = get(data, "router", {});
  const context = get(data, "context", {});
  const learnings = get(data, "learnings", {});
  const inbox = get(data, "inbox", {});
  const activeTask = get(data, "active_task", null);
  const sessions = asRows(get(data, "router_sessions", []));
  const learningTotals = get(learnings, "totals", {});
  const inboxTotals = get(inbox, "totals", {});
  const aliases = ((get(data, "dhee_aliases", []) as string[]) || []).filter(Boolean);

  return (
    <Screen
      eyebrow="COMMAND CENTER"
      title="The current truth before the agent sees anything."
      subtitle="Start here to see task continuity, context health, routed savings, review queues, and the next best action for this repo."
      action={<button onClick={refresh} style={buttonStyle}>refresh</button>}
    >
      <LoadingState loading={loading} error={error} />
      {data ? (
        <>
          <div className="product-metric-grid">
            <Metric label="tokens avoided" value={compact(get(router, "sessionTokensSaved", 0))} tone="var(--green)" />
            <Metric label="router calls" value={compact(get(router, "totalCalls", 0))} tone="var(--accent)" />
            <Metric label="repo context" value={compact(get(get(context, "totals", {}), "repo_entries", 0))} tone="var(--indigo)" />
            <Metric label="learning candidates" value={compact(get(learningTotals, "candidate", 0))} tone="var(--accent)" />
          </div>
          <div className="product-grid product-grid--two">
            <Panel label="ACTIVE WORK">
              {activeTask ? (
                <div>
                  <div style={{ fontSize: 22, lineHeight: 1.2, fontWeight: 700 }}>
                    {String(get(activeTask, "title", "Active task"))}
                  </div>
                  <div style={{ marginTop: 8, display: "flex", gap: 8, flexWrap: "wrap" }}>
                    <Pill tone={toneFor(get(activeTask, "status"))}>{String(get(activeTask, "status", "active"))}</Pill>
                    <Pill>{String(get(activeTask, "harness", "agent"))}</Pill>
                  </div>
                </div>
              ) : (
                <div style={{ color: "var(--ink3)" }}>No active task yet. Start from a linked repo to let Dhee compile state.</div>
              )}
            </Panel>
            <Panel label="NEXT ACTION">
              <div style={{ fontSize: 18, fontWeight: 650, lineHeight: 1.35 }}>
                {String(get(data, "next_action", "Start a routed agent task"))}
              </div>
              <div style={{ marginTop: 14, display: "flex", gap: 8, flexWrap: "wrap" }}>
                <button onClick={() => onNavigate("handoff")} style={buttonStyle}>handoff</button>
                <button onClick={() => onNavigate("router")} style={ghostButtonStyle}>firewall</button>
                <button onClick={() => onNavigate("learnings")} style={ghostButtonStyle}>learnings</button>
              </div>
            </Panel>
          </div>
          <div className="product-grid product-grid--three">
            <Panel label="LIVE SESSIONS">
              <RowList
                rows={sessions.slice(0, 5)}
                empty="No routed sessions yet."
                render={(row) => (
                  <SmallRow
                    key={String(row.session_id)}
                    title={String(row.title || row.session_id || "session")}
                    meta={`${row.agent || row.runtime || "agent"} - ${compact(row.tokens_saved)} tokens`}
                    tone={toneFor(row.state)}
                  />
                )}
              />
            </Panel>
            <Panel label="REVIEW QUEUE">
              <SmallRow title="proposals" meta={compact(get(inboxTotals, "proposals", 0))} tone="var(--accent)" />
              <SmallRow title="findings" meta={compact(get(inboxTotals, "findings", 0))} tone="var(--rose)" />
              <SmallRow title="conflicts" meta={compact(get(inboxTotals, "conflicts", 0))} tone="var(--indigo)" />
            </Panel>
            <Panel label="ADDRESSABLE CONTEXT">
              {aliases.length ? (
                aliases.map((alias) => (
                  <div key={alias} style={{ fontFamily: "var(--mono)", fontSize: 11, padding: "5px 0", color: "var(--ink2)" }}>
                    {alias}
                  </div>
                ))
              ) : (
                <div className="product-empty">No dhee:// aliases exposed yet.</div>
              )}
            </Panel>
          </div>
        </>
      ) : null}
    </Screen>
  );
}

export function HandoffHubView() {
  const { data, error, loading, refresh } = useScreenData(api.handoffUi);
  const continuity = get(data, "continuity", {});
  const last = get(continuity, "last_session", {}) || {};
  const tasks = asRows(get(data, "tasks", []));
  const sessions = asRows(get(data, "sessions", []));
  const activeSession = sessions.find((row) => row.active || String(row.state || "").toLowerCase() === "active") || sessions[0] || {};
  const activeTaskId = get(get(activeSession, "task", {}), "id", "");
  const activeTask = tasks.find((row) => String(row.id || "") === String(activeTaskId)) || tasks.find((row) => String(row.status || "").toLowerCase() === "active") || {};
  const currentTitle = String(get(activeTask, "title") || get(activeSession, "title") || get(last, "task_summary", "No active session yet"));
  const currentUpdated = get(activeSession, "updated_at") || get(activeTask, "updatedAt") || get(last, "updated") || get(last, "ended_at");
  const currentRuntime = String(get(activeSession, "runtime") || get(activeSession, "agent") || get(activeTask, "harness") || get(last, "agent_id", "agent"));
  const currentModel = String(get(activeSession, "model") || "");
  const currentCwd = String(get(activeSession, "cwd") || get(activeSession, "repo_root") || get(data, "repo", ""));
  const files = asRows(get(last, "files_touched", get(last, "filesTouched", [])));
  const decisions = asRows(get(last, "decisions", []));
  const todos = asRows(get(last, "todos", []));

  return (
    <Screen
      eyebrow="HANDOFF HUB"
      title="Resume without replaying the transcript."
      subtitle="Dhee turns the latest work into task state: decisions, files, blockers, commands, tests, resume confidence, and the next step."
      action={<button onClick={refresh} style={buttonStyle}>refresh</button>}
    >
      <LoadingState loading={loading} error={error} />
      {data ? (
        <div className="product-grid product-grid--two">
          <Panel label="CURRENT WORK">
            <div style={{ fontSize: 24, lineHeight: 1.15, fontWeight: 700, overflowWrap: "anywhere" }}>
              {currentTitle}
            </div>
            <div style={{ marginTop: 12, display: "flex", gap: 8, flexWrap: "wrap" }}>
              <Pill tone={toneFor(get(activeSession, "state") || get(activeTask, "status"))}>{String(get(activeSession, "state") || get(activeTask, "status") || "ready")}</Pill>
              <Pill tone="var(--green)">confidence {Math.round(Number(get(data, "resume_confidence", 0)) * 100)}%</Pill>
              <Pill>{currentRuntime}</Pill>
              {currentModel ? <Pill>{currentModel}</Pill> : null}
              <Pill>{timeLabel(currentUpdated)}</Pill>
            </div>
            {currentCwd ? <div className="product-handoff-path">{shortPath(currentCwd)}</div> : null}
            <div className="product-handoff-stats">
              <SmallRow title="router calls" meta={compact(Number(get(activeSession, "router_calls", 0)))} tone="var(--accent)" />
              <SmallRow title="tokens saved" meta={compact(Number(get(activeSession, "tokens_saved", 0)))} tone="var(--green)" />
              <SmallRow title="task restore" meta={activeTaskId ? "linked" : "local handoff"} tone="var(--indigo)" />
            </div>
          </Panel>
          <Panel label="RESUME COMMAND">
            <div style={{ color: "var(--ink2)", lineHeight: 1.55 }}>
              Run the handoff command when a new agent needs the current state without replaying chat.
            </div>
            <pre style={preStyle}>{String(get(data, "command", ""))}</pre>
          </Panel>
          <Panel label="LATEST SAVED HANDOFF">
            <div style={{ fontSize: 20, lineHeight: 1.25, fontWeight: 700, overflowWrap: "anywhere" }}>
              {String(get(last, "task_summary", "No saved handoff yet"))}
            </div>
            <div style={{ marginTop: 12, display: "flex", gap: 8, flexWrap: "wrap" }}>
              <Pill>{timeLabel(get(last, "updated") || get(last, "ended_at"))}</Pill>
              <Pill>{String(get(last, "agent_id", get(last, "source", "dhee")))}</Pill>
            </div>
          </Panel>
          <Panel label="RESUME INVENTORY">
            <MetricStack
              rows={[
                ["tasks", tasks.length],
                ["sessions", sessions.length],
                ["files", files.length],
                ["decisions", decisions.length],
                ["todos", todos.length],
              ]}
            />
          </Panel>
          <Panel label="DECISIONS" style={{ gridColumn: "span 1" }}>
            <TextList rows={decisions} empty="No decisions captured yet." />
          </Panel>
          <Panel label="FILES TOUCHED">
            <TextList rows={files.map((path) => shortPath(String(path)))} empty="No files in the latest handoff." />
          </Panel>
        </div>
      ) : null}
    </Screen>
  );
}

export function ProofReplayView() {
  const { data, error, loading, refresh } = useScreenData(() => api.proofReplay(120));
  const rows = asRows(get(data, "items", []));
  const totals = get(data, "totals", {});
  return (
    <Screen
      eyebrow="PROOF REPLAY"
      title="Replay the context decisions, not just the chat."
      subtitle="See the expansion trace: what Dhee digested, hid, expanded, injected, promoted, rejected, or derived from local records."
      action={<button onClick={refresh} style={buttonStyle}>refresh</button>}
    >
      <LoadingState loading={loading} error={error} />
      <div className="product-metric-grid">
        <Metric label="events" value={compact(get(totals, "events", rows.length))} />
        <Metric label="digests" value={compact(get(totals, "digests", 0))} tone="var(--green)" />
        <Metric label="expansion trace" value={compact(get(totals, "expansions", 0))} tone="var(--accent)" />
        <Metric label="evidence" value={compact(get(totals, "evidence", 0))} tone="var(--indigo)" />
        <Metric label="derived rows" value={compact(get(totals, "derived", 0))} />
      </div>
      <Panel label="DECISION TIMELINE">
        <RowList
          rows={rows}
          empty="No context decisions recorded yet."
          render={(row, index) => (
            <TimelineRow
              key={String(row.id || index)}
              index={index}
              title={String(row.title || "Decision")}
              meta={`${row.source || "dhee"} - ${timeLabel(row.time)}`}
              detail={String(row.detail || "")}
              kind={String(row.kind || "event")}
              derived={Boolean(row.derived)}
            />
          )}
        />
      </Panel>
    </Screen>
  );
}

export function LearningInboxView() {
  const { data, error, loading, refresh } = useScreenData(() => api.learningsUi(160));
  const [busy, setBusy] = useState("");
  const rows = asRows(get(data, "items", []));
  const totals = get(data, "totals", {});
  const act = async (id: string, action: "promote" | "reject") => {
    setBusy(id);
    try {
      if (action === "promote") await api.promoteLearning(id, { approved_by: "dhee-ui" });
      else await api.rejectLearning(id, { reason: "rejected in Dhee UI" });
      await refresh();
    } finally {
      setBusy("");
    }
  };
  return (
    <Screen
      eyebrow="LEARNING INBOX"
      title="Only evidence-backed learnings get promoted."
      subtitle="Clear pending review candidates from agent work. Dhee should learn from success, avoided failure, repeated utility, or explicit approval."
      action={<button onClick={refresh} style={buttonStyle}>refresh</button>}
    >
      <LoadingState loading={loading} error={error} />
      <div className="product-metric-grid">
        <Metric label="candidates" value={compact(get(totals, "candidate", 0))} tone="var(--accent)" />
        <Metric label="promoted" value={compact(get(totals, "promoted", 0))} tone="var(--green)" />
        <Metric label="rejected" value={compact(get(totals, "rejected", 0))} tone="var(--rose)" />
        <Metric label="all learnings" value={compact(get(totals, "all", rows.length))} />
      </div>
      <Panel label="LEARNING REVIEW">
        <RowList
          rows={rows}
          empty="No learning candidates yet."
          render={(row) => {
            const id = String(row.id || "");
            const status = String(row.status || "candidate");
            const preview = learningPreview(row);
            const source = String(row.source_harness || row.source_agent_id || "agent");
            const sourceModel = String(row.source_model || "");
            return (
              <div key={id} className="product-learning-row">
                <div style={{ minWidth: 0 }}>
                  <div style={{ display: "flex", gap: 8, flexWrap: "wrap", marginBottom: 8 }}>
                    <Pill tone={toneFor(status)}>{status}</Pill>
                    <Pill>{String(row.evidence_gate || "needs approval")}</Pill>
                    {row.needs_distillation ? <Pill tone="var(--rose)">needs distillation</Pill> : null}
                    <Pill>{source}</Pill>
                    {sourceModel ? <Pill>{sourceModel}</Pill> : null}
                  </div>
                  <div className="product-learning-title">{String(row.title || id)}</div>
                  <div className="product-learning-meta">{learningMeta(row)}</div>
                  <div className="product-learning-body" title={preview}>{preview}</div>
                </div>
                <div className="product-learning-actions">
                  <button aria-label={`Promote ${id || "learning"}`} disabled={!id || busy === id || status === "promoted"} onClick={() => act(id, "promote")} style={buttonStyle}>
                    promote
                  </button>
                  <button aria-label={`Reject ${id || "learning"}`} disabled={!id || busy === id || status === "rejected"} onClick={() => act(id, "reject")} style={ghostButtonStyle}>
                    reject
                  </button>
                </div>
              </div>
            );
          }}
        />
      </Panel>
    </Screen>
  );
}

export function PortabilityTrustView() {
  const { data, error, loading, refresh } = useScreenData(api.portabilityUi);
  const [exporting, setExporting] = useState(false);
  const [packPath, setPackPath] = useState("");
  const [dryRun, setDryRun] = useState<Record<string, unknown> | null>(null);
  const [actionError, setActionError] = useState("");
  const counts = get(data, "counts", {});
  const packs = asRows(get(data, "packs", []));
  const contract = ((get(data, "contract", []) as string[]) || []).filter(Boolean);
  const doExport = async () => {
    setExporting(true);
    setActionError("");
    try {
      await api.exportPackUi({});
      await refresh();
    } catch (e) {
      setActionError(String(e));
    } finally {
      setExporting(false);
    }
  };
  const doDryRun = async () => {
    setActionError("");
    setDryRun(null);
    try {
      setDryRun(await api.importPackDryRunUi({ input_path: packPath }));
    } catch (e) {
      setActionError(String(e));
    }
  };
  return (
    <Screen
      eyebrow="PORTABILITY & TRUST"
      title="Local memory should be inspectable, signed, and movable."
      subtitle="Dhee keeps export/import as a product surface, not an afterthought. No lock-in tricks, no hidden hosted dependency."
      action={<button onClick={refresh} style={buttonStyle}>refresh</button>}
    >
      <LoadingState loading={loading} error={error} />
      <div className="product-metric-grid">
        <Metric label="memories" value={compact(get(counts, "memories", 0))} />
        <Metric label="artifacts" value={compact(get(counts, "artifacts", 0))} tone="var(--indigo)" />
        <Metric label="repo context" value={compact(get(counts, "repo_context_entries", 0))} tone="var(--green)" />
        <Metric label="packs found" value={compact(packs.length)} tone="var(--accent)" />
      </div>
      {actionError ? <Panel><span style={{ color: "var(--rose)" }}>{actionError}</span></Panel> : null}
      <div className="product-grid product-grid--split">
        <Panel label="PORTABLE SUBSTRATE">
          <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
            {contract.map((item) => <Pill key={item} tone="var(--green)">{item}</Pill>)}
          </div>
          <button disabled={exporting} onClick={doExport} style={{ ...buttonStyle, marginTop: 16 }}>
            {exporting ? "exporting..." : "export .dheemem"}
          </button>
        </Panel>
        <Panel label="IMPORT DRY RUN">
          <div style={{ display: "flex", gap: 10 }}>
            <input
              value={packPath}
              onChange={(e) => setPackPath(e.target.value)}
              placeholder="/path/to/backup.dheemem"
              style={inputStyle}
            />
            <button disabled={!packPath.trim()} onClick={doDryRun} style={buttonStyle}>dry run</button>
          </div>
          {dryRun ? <pre style={preStyle}>{JSON.stringify(get(dryRun, "result", dryRun), null, 2)}</pre> : null}
        </Panel>
      </div>
      <Panel label="RECENT PACKS">
        <RowList
          rows={packs}
          empty="No .dheemem packs found yet."
          render={(row) => (
            <SmallRow
              key={String(row.path)}
              title={String(row.name || row.path)}
              meta={`${row.verified ? "verified" : "unverified"} - ${compact(Number(row.size_bytes || 0))} bytes - ${timeLabel(row.updated_at)}`}
              tone={row.verified ? "var(--green)" : "var(--accent)"}
            />
          )}
        />
      </Panel>
    </Screen>
  );
}

export function RepoBrainHeader({ onOpenContext }: { onOpenContext?: () => void }) {
  return (
    <div className="repo-brain-header">
      <Pill tone="var(--green)">REPO BRAIN</Pill>
      <Pill>dhee://state/current</Pill>
      <Pill>dhee://handoff/latest</Pill>
      {onOpenContext ? <button onClick={onOpenContext} style={ghostButtonStyle}>context vault</button> : null}
    </div>
  );
}

function TextList({ rows, empty }: { rows: unknown[]; empty: string }) {
  if (!rows.length) return <div className="product-empty">{empty}</div>;
  return (
    <div className="product-list product-list--tight">
      {rows.map((row, index) => (
        <div key={index} className="product-text-row">
          {String(row)}
        </div>
      ))}
    </div>
  );
}

function MetricStack({ rows }: { rows: [string, number][] }) {
  return (
    <div style={{ display: "grid", gap: 8 }}>
      {rows.map(([label, value]) => (
        <div key={label} style={{ display: "flex", justifyContent: "space-between", gap: 20 }}>
          <span style={{ color: "var(--ink3)" }}>{label}</span>
          <strong>{compact(value)}</strong>
        </div>
      ))}
    </div>
  );
}

function SmallRow({ title, meta, tone }: { title: string; meta: string; tone?: string }) {
  return (
    <div className="product-small-row">
      <span className="product-row-dot" style={{ background: tone || "var(--ink3)" }} />
      <div style={{ minWidth: 0 }}>
        <div className="product-small-title">{title}</div>
        <div className="product-small-meta">{meta}</div>
      </div>
    </div>
  );
}

function TimelineRow({
  index,
  title,
  meta,
  detail,
  kind,
  derived,
}: {
  index: number;
  title: string;
  meta: string;
  detail: string;
  kind: string;
  derived: boolean;
}) {
  return (
    <div className="product-timeline-row">
      <div className="product-timeline-index">{String(index + 1).padStart(2, "0")}</div>
      <div>
        <div style={{ display: "flex", gap: 8, flexWrap: "wrap", marginBottom: 6 }}>
          <Pill tone={toneFor(kind)}>{kind}</Pill>
          {derived ? <Pill tone="var(--accent)">derived</Pill> : <Pill tone="var(--green)">recorded</Pill>}
          <Pill>{meta}</Pill>
        </div>
        <div className="product-timeline-title">{title}</div>
        {detail ? <div className="product-timeline-detail">{detail}</div> : null}
      </div>
    </div>
  );
}

const buttonStyle: CSSProperties = {
  border: "1px solid var(--ink)",
  background: "var(--ink)",
  color: "white",
  padding: "8px 12px",
  fontFamily: "var(--mono)",
  fontSize: 10,
  letterSpacing: "0.04em",
  textTransform: "uppercase",
  borderRadius: 4,
  minHeight: 34,
  whiteSpace: "nowrap",
  cursor: "pointer",
  boxShadow: "0 1px 0 rgba(255, 255, 255, 0.22) inset",
};

const ghostButtonStyle: CSSProperties = {
  ...buttonStyle,
  color: "var(--ink)",
  background: "white",
  borderColor: "var(--border2)",
};

const inputStyle: CSSProperties = {
  minHeight: 36,
  flex: 1,
  border: "1px solid var(--border2)",
  background: "white",
  padding: "0 10px",
  fontFamily: "var(--mono)",
  fontSize: 11,
};

const preStyle: CSSProperties = {
  marginTop: 14,
  border: "1px solid var(--border)",
  background: "var(--surface2)",
  padding: 12,
  fontFamily: "var(--mono)",
  fontSize: 11,
  whiteSpace: "pre-wrap",
  overflowX: "auto",
};
