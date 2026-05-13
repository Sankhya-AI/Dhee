import { useEffect, useMemo, useState } from "react";
import type { CSSProperties, MouseEvent, ReactNode } from "react";
import { api } from "../api";
import type { Finding, InboxSnapshot, Proposal, Viewer } from "../types";

interface ConflictViewProps {
  viewer?: Viewer | null;
  onChanged?: () => Promise<void> | void;
}

const EMPTY: InboxSnapshot = {
  live: false,
  proposals: [],
  findings: [],
  conflicts: [],
  totals: { proposals: 0, findings: 0, conflicts: 0 },
};

function severityColor(severity?: string): string {
  if (severity === "high") return "var(--rose)";
  if (severity === "medium") return "var(--accent)";
  return "var(--indigo)";
}

function proposalSnippet(proposal: Proposal): string {
  const content = proposal.summary || proposal.content || "";
  return content.length > 260 ? `${content.slice(0, 260)}...` : content;
}

export function ConflictView({ viewer, onChanged }: ConflictViewProps) {
  const [snapshot, setSnapshot] = useState<InboxSnapshot>(EMPTY);
  const [selected, setSelected] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const loadInbox = async () => {
    try {
      const box = await api.inbox(
        viewer?.team_id ? { team: viewer.team_id, user: viewer.user_id } : { user: viewer?.user_id }
      );
      setSnapshot(box);
      setSelected((current) => {
        if (current) return current;
        return (
          box.proposals?.[0]?.context_id ||
          box.findings?.[0]?.finding_id ||
          String((box.conflicts?.[0] as { id?: string })?.id || "") ||
          null
        );
      });
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : String(exc));
    }
  };

  useEffect(() => {
    void loadInbox();
    const timer = window.setInterval(() => void loadInbox(), 6000);
    return () => window.clearInterval(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [viewer?.team_id, viewer?.user_id]);

  const totalOpen = useMemo(
    () =>
      (snapshot.totals?.proposals || 0) +
      (snapshot.totals?.findings || 0) +
      (snapshot.totals?.conflicts || 0),
    [snapshot.totals]
  );

  const decideProposal = async (proposal: Proposal, decision: "approve" | "reject") => {
    setBusy(`${decision}:${proposal.context_id}`);
    setError(null);
    try {
      if (decision === "approve") {
        await api.approveProposal(proposal.context_id, viewer?.user_id || "manager");
      } else {
        await api.rejectProposal(proposal.context_id, viewer?.user_id || "manager");
      }
      await loadInbox();
      await onChanged?.();
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : String(exc));
    } finally {
      setBusy(null);
    }
  };

  const resolveFinding = async (finding: Finding) => {
    setBusy(`finding:${finding.finding_id}`);
    setError(null);
    try {
      await api.resolveFinding(finding.finding_id, viewer?.user_id || "manager");
      await loadInbox();
      await onChanged?.();
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : String(exc));
    } finally {
      setBusy(null);
    }
  };

  const resolveConflict = async (conflict: Record<string, unknown>, action: string) => {
    const id = String(conflict.id || "");
    if (!id) return;
    setBusy(`conflict:${id}:${action}`);
    setError(null);
    try {
      await api.resolveConflictDetailed(id, { action });
      await loadInbox();
      await onChanged?.();
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : String(exc));
    } finally {
      setBusy(null);
    }
  };

  return (
    <div className="inbox-shell" style={{ display: "flex", height: "100%", minHeight: 0 }}>
      <aside
        className="inbox-sidebar"
        style={{
          width: 300,
          borderRight: "1px solid var(--border)",
          background: "white",
          padding: 16,
          overflowY: "auto",
          flexShrink: 0,
        }}
      >
        <div
          style={{
            fontFamily: "var(--mono)",
            fontSize: 10,
            color: "var(--ink3)",
            letterSpacing: "0.08em",
            textTransform: "uppercase",
          }}
        >
          Inbox
        </div>
        <div style={{ fontSize: 22, fontWeight: 650, marginTop: 6 }}>
          {totalOpen} open
        </div>
        <div
          style={{
            marginTop: 10,
            display: "grid",
            gap: 8,
            fontFamily: "var(--mono)",
            fontSize: 10,
          }}
        >
          <SummaryRow label="Proposals" value={snapshot.totals?.proposals || 0} />
          <SummaryRow label="Findings" value={snapshot.totals?.findings || 0} />
          <SummaryRow label="Conflicts" value={snapshot.totals?.conflicts || 0} />
        </div>
        <div
          style={{
            marginTop: 18,
            padding: 12,
            border: "1px solid var(--border)",
            background: "var(--surface)",
            fontSize: 12,
            lineHeight: 1.5,
            color: "var(--ink2)",
          }}
        >
          Review context changes, stale-context findings, and memory conflicts
          from one queue. Approvals activate context used by routing.
        </div>
        {error ? (
          <div
            style={{
              marginTop: 12,
              padding: 10,
              border: "1px solid var(--rose)",
              background: "var(--rose-dim)",
              color: "var(--rose)",
              fontFamily: "var(--mono)",
              fontSize: 10,
              lineHeight: 1.5,
            }}
          >
            {error}
          </div>
        ) : null}
      </aside>

      <main
        className="inbox-main"
        style={{
          flex: 1,
          minWidth: 0,
          overflowY: "auto",
          background: "var(--bg)",
          padding: 18,
          display: "grid",
          gap: 16,
          alignContent: "start",
        }}
      >
        <InboxSection
          title="Pending Proposals"
          count={snapshot.proposals.length}
          empty="No context edits are waiting for approval."
        >
          {snapshot.proposals.map((proposal) => (
            <article
              key={proposal.context_id}
              onClick={() => setSelected(proposal.context_id)}
              style={rowStyle(selected === proposal.context_id)}
            >
              <div style={rowHeaderStyle}>
                <div>
                  <div style={rowTitleStyle}>{proposal.title}</div>
                  <div style={rowMetaStyle}>
                    {proposal.proposed_by_user_id || "developer"} · {proposal.team_id || proposal.project_id || proposal.scope}
                  </div>
                </div>
                <Badge color="var(--accent)">pending</Badge>
              </div>
              <p style={snippetStyle}>{proposalSnippet(proposal) || "No preview available."}</p>
              <div
                className="inbox-actions"
                style={{ display: "flex", gap: 8, justifyContent: "flex-end", flexWrap: "wrap" }}
              >
                <QueueButton
                  label="Open in Context"
                  onClick={(e) => {
                    e.stopPropagation();
                    window.location.hash = `#vault/item/${proposal.context_id}`;
                    window.history.replaceState(null, "", `?view=context${window.location.hash}`);
                    window.dispatchEvent(new PopStateEvent("popstate"));
                  }}
                />
                <QueueButton
                  label="Reject"
                  color="var(--rose)"
                  busy={busy === `reject:${proposal.context_id}`}
                  onClick={(e) => {
                    e.stopPropagation();
                    void decideProposal(proposal, "reject");
                  }}
                />
                <QueueButton
                  label="Approve"
                  color="var(--green)"
                  busy={busy === `approve:${proposal.context_id}`}
                  onClick={(e) => {
                    e.stopPropagation();
                    void decideProposal(proposal, "approve");
                  }}
                />
              </div>
            </article>
          ))}
        </InboxSection>

        <InboxSection
          title="Manager Findings"
          count={snapshot.findings.length}
          empty="No stale, low-quality, or duplicate context findings."
        >
          {snapshot.findings.map((finding) => (
            <article
              key={finding.finding_id}
              onClick={() => setSelected(finding.finding_id)}
              style={rowStyle(selected === finding.finding_id)}
            >
              <div style={rowHeaderStyle}>
                <div>
                  <div style={rowTitleStyle}>{finding.title}</div>
                  <div style={rowMetaStyle}>
                    {finding.team_id} · {finding.finding_type}
                  </div>
                </div>
                <Badge color={severityColor(finding.severity)}>
                  {finding.severity}
                </Badge>
              </div>
              <p style={snippetStyle}>{finding.detail}</p>
              <div
                className="inbox-actions"
                style={{ display: "flex", justifyContent: "flex-end", flexWrap: "wrap" }}
              >
                <QueueButton
                  label="Resolve"
                  color="var(--green)"
                  busy={busy === `finding:${finding.finding_id}`}
                  onClick={(e) => {
                    e.stopPropagation();
                    void resolveFinding(finding);
                  }}
                />
              </div>
            </article>
          ))}
        </InboxSection>

        <InboxSection
          title="Memory Conflicts"
          count={snapshot.conflicts.length}
          empty="No memory contradictions detected."
        >
          {snapshot.conflicts.map((conflict) => {
            const c = conflict as {
              id?: string;
              reason?: string;
              severity?: string;
              belief_a?: { content?: string };
              belief_b?: { content?: string };
            };
            const id = String(c.id || Math.random());
            return (
              <article
                key={id}
                onClick={() => setSelected(id)}
                style={rowStyle(selected === id)}
              >
                <div style={rowHeaderStyle}>
                  <div>
                    <div style={rowTitleStyle}>Memory conflict</div>
                    <div style={rowMetaStyle}>{c.reason || "Contradiction"}</div>
                  </div>
                  <Badge color={severityColor(c.severity)}>{c.severity || "open"}</Badge>
                </div>
                <div style={{ display: "grid", gap: 6, marginTop: 10 }}>
                  <ConflictQuote label="A" text={c.belief_a?.content} />
                  <ConflictQuote label="B" text={c.belief_b?.content} />
                </div>
                <div
                  className="inbox-actions"
                  style={{ display: "flex", gap: 8, justifyContent: "flex-end", flexWrap: "wrap", marginTop: 10 }}
                >
                  {["KEEP A", "KEEP B", "MERGE"].map((action) => (
                    <QueueButton
                      key={action}
                      label={action}
                      busy={busy === `conflict:${id}:${action}`}
                      onClick={(e) => {
                        e.stopPropagation();
                        void resolveConflict(conflict, action);
                      }}
                    />
                  ))}
                </div>
              </article>
            );
          })}
        </InboxSection>
      </main>
    </div>
  );
}

function SummaryRow({ label, value }: { label: string; value: number }) {
  return (
    <div style={{ display: "flex", justifyContent: "space-between" }}>
      <span style={{ color: "var(--ink3)" }}>{label}</span>
      <span style={{ color: value ? "var(--accent)" : "var(--ink2)" }}>{value}</span>
    </div>
  );
}

function InboxSection({
  title,
  count,
  empty,
  children,
}: {
  title: string;
  count: number;
  empty: string;
  children: ReactNode;
}) {
  return (
    <section style={{ display: "grid", gap: 10 }}>
      <div
        style={{
          display: "flex",
          alignItems: "center",
          gap: 8,
          fontFamily: "var(--mono)",
          fontSize: 10,
          letterSpacing: "0.08em",
          color: "var(--ink3)",
          textTransform: "uppercase",
        }}
      >
        <span>{title}</span>
        <Badge>{count}</Badge>
      </div>
      {count === 0 ? (
        <div
          style={{
            border: "1px dashed var(--border)",
            background: "white",
            color: "var(--ink3)",
            padding: 16,
            fontSize: 12,
          }}
        >
          {empty}
        </div>
      ) : (
        children
      )}
    </section>
  );
}

function Badge({
  children,
  color = "var(--ink3)",
}: {
  children: ReactNode;
  color?: string;
}) {
  return (
    <span
      style={{
        display: "inline-flex",
        alignItems: "center",
        padding: "2px 7px",
        border: `1px solid ${color}`,
        color,
        background: "white",
        borderRadius: 3,
        fontFamily: "var(--mono)",
        fontSize: 9,
      }}
    >
      {children}
    </span>
  );
}

function QueueButton({
  label,
  onClick,
  color = "var(--ink2)",
  busy,
}: {
  label: string;
  onClick: (event: MouseEvent<HTMLButtonElement>) => void;
  color?: string;
  busy?: boolean;
}) {
  return (
    <button
      onClick={onClick}
      disabled={busy}
      style={{
        padding: "6px 9px",
        border: `1px solid ${color}`,
        color,
        background: "white",
        fontFamily: "var(--mono)",
        fontSize: 9,
        borderRadius: 3,
        cursor: busy ? "wait" : "pointer",
      }}
    >
      {busy ? "..." : label}
    </button>
  );
}

function ConflictQuote({ label, text }: { label: string; text?: string }) {
  return (
    <div
      style={{
        border: "1px solid var(--border)",
        background: "var(--surface)",
        padding: 10,
        display: "grid",
        gridTemplateColumns: "20px minmax(0, 1fr)",
        gap: 8,
      }}
    >
      <span style={{ fontFamily: "var(--mono)", color: "var(--ink3)" }}>
        {label}
      </span>
      <span style={{ color: "var(--ink2)", fontSize: 12, lineHeight: 1.5 }}>
        {text || "No content"}
      </span>
    </div>
  );
}

const rowHeaderStyle: CSSProperties = {
  display: "flex",
  justifyContent: "space-between",
  alignItems: "flex-start",
  gap: 12,
};

const rowTitleStyle: CSSProperties = {
  fontSize: 15,
  fontWeight: 650,
  color: "var(--ink)",
};

const rowMetaStyle: CSSProperties = {
  fontFamily: "var(--mono)",
  fontSize: 10,
  color: "var(--ink3)",
  marginTop: 3,
};

const snippetStyle: CSSProperties = {
  margin: "10px 0",
  color: "var(--ink2)",
  fontSize: 12,
  lineHeight: 1.55,
};

function rowStyle(active: boolean): CSSProperties {
  return {
    border: `1px solid ${active ? "var(--accent)" : "var(--border)"}`,
    background: "white",
    padding: 14,
    boxShadow: active ? "0 10px 24px rgba(20,16,10,0.06)" : "none",
    cursor: "pointer",
  };
}
