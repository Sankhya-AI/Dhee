import type { ReactNode } from "react";
import { useEffect, useState } from "react";
import { api } from "../api";
import { SectionHeader } from "../components/ui/SectionHeader";
import type {
  ContextEntriesSnapshot,
  LocalWorkspace,
  RepoContextEntry,
} from "../types";

/**
 * Context Management screen.
 *
 * One repo at a time. Top: repo picker (linked workspaces). Body: three
 * columns — repo entries (shared via git), promoted in (personal → repo),
 * demoted out (repo → personal) — with provenance. Right rail: share
 * matrix (which other linked repos exist + entry counts).
 *
 * Promote/demote happen inline. The endpoints already update the
 * personal store and append to <repo>/.dhee/context/entries.jsonl, so
 * the user only needs to commit + push to share with teammates.
 */
export function ContextView() {
  const [workspaces, setWorkspaces] = useState<LocalWorkspace[]>([]);
  const [repos, setRepos] = useState<string[]>([]);
  const [selectedRepo, setSelectedRepo] = useState<string>("");
  const [snapshot, setSnapshot] = useState<ContextEntriesSnapshot | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [demoteId, setDemoteId] = useState<string>("");

  // ── Load linked workspaces and pick a default repo ─────────────────
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const ws = await api.localWorkspaces();
        if (cancelled) return;
        const list = ws.workspaces || [];
        setWorkspaces(list);
        const allRepos = list.flatMap((w) => w.folders || []);
        setRepos(allRepos);
        if (allRepos.length && !selectedRepo) setSelectedRepo(allRepos[0]);
      } catch (e) {
        setError(String(e));
      }
    })();
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // ── Load entries for the selected repo ─────────────────────────────
  useEffect(() => {
    if (!selectedRepo) {
      setSnapshot(null);
      return;
    }
    let cancelled = false;
    (async () => {
      try {
        const snap = await api.contextEntries(selectedRepo, 200);
        if (!cancelled) setSnapshot(snap);
      } catch (e) {
        if (!cancelled) setError(String(e));
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [selectedRepo]);

  const reload = async () => {
    if (!selectedRepo) return;
    try {
      const snap = await api.contextEntries(selectedRepo, 200);
      setSnapshot(snap);
    } catch (e) {
      setError(String(e));
    }
  };

  const handleDemote = async (entry: RepoContextEntry) => {
    setBusy(`demote:${entry.id}`);
    try {
      await api.contextDemote({ entry_id: entry.id, repo: selectedRepo });
      await reload();
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(null);
    }
  };

  const handleManualDemote = async () => {
    const id = demoteId.trim();
    if (!id) return;
    setBusy("demote:manual");
    try {
      await api.contextDemote({ entry_id: id, repo: selectedRepo });
      setDemoteId("");
      await reload();
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(null);
    }
  };

  const totals = snapshot?.totals;

  return (
    <div
      style={{
        display: "flex",
        flexDirection: "column",
        height: "100%",
        padding: "16px 20px",
        gap: 16,
        overflow: "auto",
      }}
    >
      <SectionHeader
        label="CONTEXT"
        sub="Per-folder shared context · personal vs repo · share matrix"
      />

      {/* ── Repo picker ─────────────────────────────────────────── */}
      <div
        style={{
          display: "flex",
          alignItems: "center",
          gap: 12,
          padding: "10px 12px",
          border: "1px solid var(--border)",
          background: "var(--surface)",
          borderRadius: 4,
        }}
      >
        <span
          style={{
            fontFamily: "var(--mono)",
            fontSize: 11,
            color: "var(--ink3)",
          }}
        >
          REPO
        </span>
        <select
          value={selectedRepo}
          onChange={(e) => setSelectedRepo(e.target.value)}
          style={{
            flex: 1,
            padding: "6px 8px",
            background: "var(--bg)",
            border: "1px solid var(--border)",
            color: "var(--ink1)",
            fontFamily: "var(--mono)",
            fontSize: 12,
          }}
        >
          {repos.length === 0 ? (
            <option value="">(no repos linked — run `dhee link` or add a folder)</option>
          ) : (
            repos.map((r) => (
              <option key={r} value={r}>
                {r}
              </option>
            ))
          )}
        </select>
        {totals ? (
          <span
            style={{
              fontFamily: "var(--mono)",
              fontSize: 11,
              color: "var(--ink2)",
            }}
          >
            {totals.repo_entries} entries · {totals.promoted_in} promoted in ·{" "}
            {totals.demoted_out} demoted out · {totals.linked_peers} peers
          </span>
        ) : null}
      </div>

      {error ? (
        <div
          style={{
            border: "1px solid var(--rose)",
            color: "var(--rose)",
            padding: "8px 10px",
            fontFamily: "var(--mono)",
            fontSize: 11,
          }}
        >
          {error}
        </div>
      ) : null}

      {/* ── Three columns: repo · promoted in · demoted out ──────── */}
      <div
        style={{
          display: "grid",
          gridTemplateColumns: "1.4fr 1fr 1fr 0.8fr",
          gap: 14,
          alignItems: "stretch",
        }}
      >
        <RepoEntriesPanel
          entries={snapshot?.repo_entries || []}
          onDemote={handleDemote}
          busy={busy}
        />
        <ProvenancePanel
          title="PROMOTED IN"
          subtitle="Personal memories shared into this repo"
          rows={snapshot?.promoted_in || []}
          dateField="promoted_at"
        />
        <ProvenancePanel
          title="DEMOTED OUT"
          subtitle="Repo entries copied into your personal store"
          rows={snapshot?.demoted_out || []}
          dateField="demoted_at"
        />
        <SharePanel
          peers={snapshot?.share_matrix || []}
          activeRepo={selectedRepo}
          onSwitch={(p) => setSelectedRepo(p)}
        />
      </div>

      {/* ── Manual demote (paste an entry id) ─────────────────────── */}
      <div
        style={{
          display: "flex",
          gap: 8,
          alignItems: "center",
          padding: "8px 10px",
          border: "1px dashed var(--border)",
          color: "var(--ink2)",
          fontSize: 11,
        }}
      >
        <span style={{ fontFamily: "var(--mono)" }}>DEMOTE BY ID</span>
        <input
          value={demoteId}
          onChange={(e) => setDemoteId(e.target.value)}
          placeholder="entry id from the repo"
          style={{
            flex: 1,
            padding: "5px 7px",
            background: "var(--bg)",
            border: "1px solid var(--border)",
            color: "var(--ink1)",
            fontFamily: "var(--mono)",
            fontSize: 11,
          }}
        />
        <button
          disabled={!demoteId.trim() || busy === "demote:manual"}
          onClick={handleManualDemote}
          style={{
            padding: "5px 10px",
            background: "var(--accent)",
            color: "white",
            border: 0,
            fontFamily: "var(--mono)",
            fontSize: 11,
            cursor: "pointer",
          }}
        >
          {busy === "demote:manual" ? "…" : "demote"}
        </button>
      </div>
    </div>
  );
}

function RepoEntriesPanel({
  entries,
  onDemote,
  busy,
}: {
  entries: RepoContextEntry[];
  onDemote: (e: RepoContextEntry) => void;
  busy: string | null;
}) {
  return (
    <Panel
      title="REPO ENTRIES"
      subtitle={`${entries.length} shared via git`}
    >
      {entries.length === 0 ? (
        <Empty hint="No repo entries yet. Promote a personal memory or push a teammate's commits." />
      ) : (
        entries.map((e) => (
          <div
            key={e.id}
            style={{
              padding: "8px 0",
              borderBottom: "1px solid var(--border)",
              display: "flex",
              gap: 10,
              alignItems: "flex-start",
            }}
          >
            <div style={{ flex: 1, minWidth: 0 }}>
              <div
                style={{
                  fontFamily: "var(--mono)",
                  fontSize: 10,
                  color: "var(--ink3)",
                  display: "flex",
                  gap: 8,
                }}
              >
                <span>{e.kind}</span>
                <span>·</span>
                <span>{e.created_by}</span>
                <span>·</span>
                <span>{relTime(e.created_at)}</span>
              </div>
              <div
                style={{
                  fontSize: 12,
                  color: "var(--ink1)",
                  fontWeight: 500,
                  margin: "2px 0",
                  textOverflow: "ellipsis",
                  whiteSpace: "nowrap",
                  overflow: "hidden",
                }}
                title={e.title}
              >
                {e.title || "(untitled)"}
              </div>
              <div
                style={{
                  fontSize: 11,
                  color: "var(--ink2)",
                  display: "-webkit-box",
                  WebkitLineClamp: 2,
                  WebkitBoxOrient: "vertical",
                  overflow: "hidden",
                }}
              >
                {e.content}
              </div>
            </div>
            <button
              disabled={busy === `demote:${e.id}`}
              onClick={() => onDemote(e)}
              title="Copy this repo entry into your personal memory"
              style={{
                padding: "4px 8px",
                background: "transparent",
                border: "1px solid var(--border)",
                color: "var(--ink2)",
                fontFamily: "var(--mono)",
                fontSize: 10,
                cursor: "pointer",
              }}
            >
              {busy === `demote:${e.id}` ? "…" : "↓ keep"}
            </button>
          </div>
        ))
      )}
    </Panel>
  );
}

function ProvenancePanel({
  title,
  subtitle,
  rows,
  dateField,
}: {
  title: string;
  subtitle: string;
  rows: { memory_id: string; memory: string; entry_id?: string; promoted_at?: string; demoted_at?: string }[];
  dateField: "promoted_at" | "demoted_at";
}) {
  return (
    <Panel title={title} subtitle={subtitle}>
      {rows.length === 0 ? (
        <Empty hint="Nothing here yet." />
      ) : (
        rows.map((r) => (
          <div
            key={`${r.memory_id}-${r.entry_id || ""}`}
            style={{
              padding: "8px 0",
              borderBottom: "1px solid var(--border)",
            }}
          >
            <div
              style={{
                fontFamily: "var(--mono)",
                fontSize: 10,
                color: "var(--ink3)",
              }}
            >
              {r.memory_id.slice(0, 12)} · {relTime(r[dateField])}
            </div>
            <div
              style={{
                fontSize: 11,
                color: "var(--ink2)",
                display: "-webkit-box",
                WebkitLineClamp: 3,
                WebkitBoxOrient: "vertical",
                overflow: "hidden",
              }}
            >
              {r.memory}
            </div>
          </div>
        ))
      )}
    </Panel>
  );
}

function SharePanel({
  peers,
  activeRepo,
  onSwitch,
}: {
  peers: { repo_root: string; label: string; entry_count: number }[];
  activeRepo: string;
  onSwitch: (p: string) => void;
}) {
  return (
    <Panel title="SHARES WITH" subtitle={`${peers.length} other linked repos`}>
      {peers.length === 0 ? (
        <Empty hint="No other linked repos. Run `dhee link` in another repo to compare context." />
      ) : (
        peers.map((p) => (
          <div
            key={p.repo_root}
            onClick={() => onSwitch(p.repo_root)}
            style={{
              padding: "8px 0",
              borderBottom: "1px solid var(--border)",
              cursor: "pointer",
              opacity: activeRepo === p.repo_root ? 0.6 : 1,
            }}
            title="Switch to this repo"
          >
            <div
              style={{
                fontFamily: "var(--mono)",
                fontSize: 11,
                color: "var(--ink1)",
                fontWeight: 500,
              }}
            >
              {p.label}
            </div>
            <div
              style={{
                fontFamily: "var(--mono)",
                fontSize: 10,
                color: "var(--ink3)",
                textOverflow: "ellipsis",
                overflow: "hidden",
                whiteSpace: "nowrap",
              }}
            >
              {p.repo_root}
            </div>
            <div
              style={{
                fontFamily: "var(--mono)",
                fontSize: 10,
                color: "var(--ink2)",
                marginTop: 2,
              }}
            >
              {p.entry_count} entries
            </div>
          </div>
        ))
      )}
    </Panel>
  );
}

function Panel({
  title,
  subtitle,
  children,
}: {
  title: string;
  subtitle?: string;
  children: ReactNode;
}) {
  return (
    <div
      style={{
        border: "1px solid var(--border)",
        background: "var(--surface)",
        borderRadius: 4,
        padding: "10px 12px",
        display: "flex",
        flexDirection: "column",
        minHeight: 280,
      }}
    >
      <div
        style={{
          display: "flex",
          alignItems: "baseline",
          justifyContent: "space-between",
          marginBottom: 6,
        }}
      >
        <div
          style={{
            fontFamily: "var(--mono)",
            fontSize: 11,
            letterSpacing: "0.06em",
            color: "var(--ink1)",
            fontWeight: 600,
          }}
        >
          {title}
        </div>
        {subtitle ? (
          <div
            style={{
              fontFamily: "var(--mono)",
              fontSize: 10,
              color: "var(--ink3)",
            }}
          >
            {subtitle}
          </div>
        ) : null}
      </div>
      <div style={{ overflow: "auto", flex: 1 }}>{children}</div>
    </div>
  );
}

function Empty({ hint }: { hint: string }) {
  return (
    <div
      style={{
        fontFamily: "var(--mono)",
        fontSize: 11,
        color: "var(--ink3)",
        padding: "16px 0",
      }}
    >
      {hint}
    </div>
  );
}

function relTime(value?: string | null): string {
  if (!value) return "—";
  const t = new Date(value).getTime();
  if (Number.isNaN(t)) return "—";
  const delta = Date.now() - t;
  if (delta < 60_000) return "just now";
  if (delta < 3_600_000) return `${Math.floor(delta / 60_000)}m ago`;
  if (delta < 86_400_000) return `${Math.floor(delta / 3_600_000)}h ago`;
  return `${Math.floor(delta / 86_400_000)}d ago`;
}
