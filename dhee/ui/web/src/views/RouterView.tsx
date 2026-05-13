import type { ReactNode } from "react";
import { useEffect, useMemo, useRef, useState } from "react";
import { api } from "../api";
import { FirstRunPanel } from "../components/FirstRunPanel";
import type { RouterSessionRow, RouterSessionsPage } from "../types";

type RangeKey = "day" | "week" | "month" | "year";
type RouterScreen = "live" | "history";

const RANGES: { key: RangeKey; label: string; short: string; ms: number }[] = [
  { key: "day", label: "Daily", short: "24h", ms: 24 * 60 * 60 * 1000 },
  { key: "week", label: "Weekly", short: "7d", ms: 7 * 24 * 60 * 60 * 1000 },
  { key: "month", label: "Monthly", short: "30d", ms: 30 * 24 * 60 * 60 * 1000 },
  { key: "year", label: "Yearly", short: "365d", ms: 365 * 24 * 60 * 60 * 1000 },
];

const EMPTY_PAGE: RouterSessionsPage = {
  items: [],
  next_cursor: null,
  active_only: false,
  totals: {
    tokens_saved: 0,
    estimated_cost_saved_usd: 0,
    router_calls: 0,
    sessions: 0,
  },
};

function formatCompactNumber(value?: number | null) {
  if (value == null) return "0";
  return new Intl.NumberFormat("en", {
    notation: Math.abs(value) >= 10_000 ? "compact" : "standard",
    maximumFractionDigits: Math.abs(value) >= 10_000 ? 1 : 0,
  }).format(value);
}

function formatInteger(value?: number | null) {
  return new Intl.NumberFormat("en-US").format(Math.max(0, Number(value || 0)));
}

function formatMoney(value?: number | null) {
  const dollars = Math.max(0, Number(value || 0));
  if (dollars > 0 && dollars < 0.01) return "<$0.01";
  return new Intl.NumberFormat("en-US", {
    style: "currency",
    currency: "USD",
    maximumFractionDigits: dollars >= 100 ? 0 : 2,
  }).format(dollars);
}

function parseTime(value?: string | null): number {
  if (!value) return 0;
  const t = new Date(value).getTime();
  return Number.isNaN(t) ? 0 : t;
}

function relTime(value?: string | null): string {
  const t = parseTime(value);
  if (!t) return "n/a";
  const delta = Date.now() - t;
  if (delta < 60_000) return "just now";
  if (delta < 3_600_000) return `${Math.floor(delta / 60_000)}m ago`;
  if (delta < 86_400_000) return `${Math.floor(delta / 3_600_000)}h ago`;
  return `${Math.floor(delta / 86_400_000)}d ago`;
}

function shortPath(path?: string | null): string {
  if (!path) return "n/a";
  const parts = path.split("/").filter(Boolean);
  if (parts.length <= 2) return path;
  return ".../" + parts.slice(-2).join("/");
}

function agentKey(row: RouterSessionRow | string): string {
  const raw =
    typeof row === "string"
      ? row
      : row.agent || row.runtime || row.agents?.[0] || "unknown";
  const key = String(raw || "").toLowerCase();
  if (key.includes("codex")) return "codex";
  if (key.includes("claude")) return "claude-code";
  return key || "unknown";
}

function agentLabel(agent?: string | null) {
  const key = agentKey(agent || "");
  if (key === "codex") return "Codex";
  if (key === "claude-code") return "Claude Code";
  return agent || "Unknown";
}

function agentColor(agent?: string | null) {
  const key = agentKey(agent || "");
  if (key === "codex") return "var(--indigo)";
  if (key === "claude-code") return "var(--accent)";
  return "var(--ink3)";
}

function hasOfficialRate(row: RouterSessionRow) {
  return Number(row.pricing?.input_cost_per_million || 0) > 0;
}

function sessionCostLabel(row: RouterSessionRow) {
  if (row.tokens_saved > 0 && !hasOfficialRate(row)) return "unpriced";
  return formatMoney(row.estimated_cost_saved_usd);
}

function isRunningSession(row: RouterSessionRow) {
  const state = String(row.state || "").toLowerCase();
  return Boolean(row.active) && (state === "active" || state === "running" || state === "live");
}

function pricingLabel(row: RouterSessionRow) {
  const pricing = row.pricing;
  if (!pricing || !hasOfficialRate(row)) {
    return pricing?.note || "No official provider/model rate mapped yet.";
  }
  const provider = pricing.provider || row.runtime || row.agent || "provider";
  const model = pricing.model_family || row.model || "model";
  return `${provider} ${model}: $${pricing.input_cost_per_million}/1M input tokens`;
}

function budgetCapForRange(
  budget: RouterSessionsPage["budget"] | undefined,
  range?: RangeKey,
) {
  if (!budget) return Number.POSITIVE_INFINITY;
  const key:
    | "daily_budget_usd"
    | "weekly_budget_usd"
    | "monthly_budget_usd"
    | "yearly_budget_usd" =
    range === "day"
      ? "daily_budget_usd"
      : range === "week"
        ? "weekly_budget_usd"
        : range === "year"
          ? "yearly_budget_usd"
          : "monthly_budget_usd";
  const cap = Number(budget[key] || 0);
  return cap > 0 ? cap : Number.POSITIVE_INFINITY;
}

function summarize(
  rows: RouterSessionRow[],
  budget?: RouterSessionsPage["budget"],
  range?: RangeKey,
) {
  return rows.reduce(
    (acc, row) => {
      acc.tokens += row.tokens_saved || 0;
      acc.apiValue += Number(row.estimated_cost_saved_usd || 0);
      acc.calls += row.router_calls || 0;
      acc.sessions += 1;
      acc.cost = Math.min(acc.apiValue, budgetCapForRange(budget, range));
      return acc;
    },
    { tokens: 0, apiValue: 0, cost: 0, calls: 0, sessions: 0 }
  );
}

function routerScreenFromLocation(): RouterScreen {
  const params = new URLSearchParams(window.location.search);
  const view = String(params.get("view") || "").toLowerCase();
  const path = window.location.pathname.replace(/^\/+|\/+$/g, "").toLowerCase();
  if (
    view === "router/sessionshistory" ||
    view === "router/session-history" ||
    view === "router/history" ||
    path === "router/sessionshistory"
  )
    return "history";
  return "live";
}

function pushRouterScreen(screen: RouterScreen) {
  const params = new URLSearchParams(window.location.search);
  params.set("view", screen === "history" ? "router/sessionshistory" : "router");
  const query = params.toString();
  const next = `${window.location.pathname}${query ? `?${query}` : ""}${window.location.hash || ""}`;
  window.history.pushState({}, "", next);
  window.dispatchEvent(new Event("popstate"));
}

export function RouterView({
  onOpenFolders,
  onOpenSetup,
}: {
  onOpenFolders?: () => void;
  onOpenSetup?: () => void;
}) {
  return (
    <div
      style={{
        height: "100%",
        overflowY: "auto",
        background: "var(--surface)",
      }}
    >
      <RouterSavingsDashboard onOpenFolders={onOpenFolders} onOpenSetup={onOpenSetup} />
    </div>
  );
}

function RouterSavingsDashboard({
  onOpenFolders,
  onOpenSetup,
}: {
  onOpenFolders?: () => void;
  onOpenSetup?: () => void;
}) {
  const rootRef = useRef<HTMLDivElement | null>(null);
  const [historyPage, setHistoryPage] = useState<RouterSessionsPage>(EMPTY_PAGE);
  const [activePage, setActivePage] = useState<RouterSessionsPage>(EMPTY_PAGE);
  const [range, setRange] = useState<RangeKey>("week");
  const [screen, setScreen] = useState<RouterScreen>(() => routerScreenFromLocation());
  const [selectedId, setSelectedId] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = async (silent = false) => {
    if (!silent) setLoading(true);
    setError(null);
    const activeRequest = api.routerSessions({ active: true, limit: 50 }).then(
      (page) => ({ ok: true as const, page }),
      (error) => ({ ok: false as const, error })
    );
    const historyRequest = api.routerSessions({ active: false, limit: 100 }).then(
      (page) => ({ ok: true as const, page }),
      (error) => ({ ok: false as const, error })
    );
    const errors: string[] = [];
    try {
      const active = await activeRequest;
      if (active.ok) setActivePage(active.page);
      else errors.push(String(active.error));

      const history = await historyRequest;
      if (history.ok) setHistoryPage(history.page);
      else errors.push(String(history.error));

      if (errors.length) setError(errors.join("; "));
    } finally {
      if (!silent) setLoading(false);
    }
  };

  useEffect(() => {
    void load(false);
    const timer = window.setInterval(() => void load(true), 15_000);
    return () => window.clearInterval(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    const onPop = () => setScreen(routerScreenFromLocation());
    window.addEventListener("popstate", onPop);
    return () => window.removeEventListener("popstate", onPop);
  }, []);

  useEffect(() => {
    rootRef.current?.parentElement?.scrollTo({ top: 0, behavior: "auto" });
  }, [screen]);

  const activeRows = useMemo(
    () =>
      [...activePage.items].filter(isRunningSession).sort(
        (a, b) => parseTime(b.updated_at) - parseTime(a.updated_at)
      ),
    [activePage.items]
  );

  const rangeRows = useMemo(() => {
    const selected = RANGES.find((item) => item.key === range) || RANGES[1];
    const floor = Date.now() - selected.ms;
    return [...historyPage.items]
      .filter((row) => parseTime(row.updated_at || row.started_at) >= floor)
      .sort((a, b) => parseTime(b.updated_at) - parseTime(a.updated_at));
  }, [historyPage.items, range]);

  const selected =
    screen === "history"
      ? rangeRows.find((row) => row.session_id === selectedId) || null
      : activeRows.find((row) => row.session_id === selectedId) || null;
  const budget = historyPage.budget || activePage.budget;
  const rangeTotals = summarize(rangeRows, budget, range);
  const activeTotals = summarize(activeRows, budget, "day");
  const hasAnySessions = activePage.items.length > 0 || historyPage.items.length > 0;
  const selectedRange = RANGES.find((item) => item.key === range) || RANGES[1];
  const budgetCap = budgetCapForRange(budget, range);
  const cappedByBudget = Number.isFinite(budgetCap) && rangeTotals.apiValue > budgetCap;
  const navigateScreen = (next: RouterScreen) => {
    setScreen(next);
    pushRouterScreen(next);
  };

  return (
    <div
      ref={rootRef}
      style={{
        padding: "clamp(10px, 3vw, 18px)",
        display: "grid",
        gap: 14,
        minWidth: 0,
        width: "100%",
        boxSizing: "border-box",
      }}
    >
      <section
        style={{
          border: "1px solid var(--border)",
          background: "var(--bg)",
          borderRadius: 8,
          padding: 16,
          minWidth: 0,
          boxSizing: "border-box",
        }}
      >
        <div
          style={{
            display: "flex",
            flexWrap: "wrap",
            gap: 14,
            alignItems: "start",
            justifyContent: "space-between",
            marginBottom: 14,
          }}
        >
          <div style={{ flex: "1 1 240px", minWidth: 0 }}>
            <Eyebrow>Context Firewall</Eyebrow>
            <h1
              style={{
                margin: "2px 0 0",
                fontSize: 26,
                lineHeight: 1.1,
                color: "var(--ink)",
                letterSpacing: 0,
              }}
            >
              {screen === "history" ? "Firewall session history" : "Live context firewall"}
            </h1>
            <p
              style={{
                margin: "6px 0 0",
                color: "var(--ink3)",
                fontSize: 12,
                maxWidth: 780,
              }}
            >
              {screen === "history"
                ? "Every completed and recent local agent session, with pointer-backed evidence and avoided raw context."
                : "Running Claude Code and Codex sessions, with raw output kept behind digests until the agent asks to expand."}
            </p>
          </div>
          <div
            style={{
              display: "flex",
              gap: 8,
              justifyContent: "flex-start",
              flexWrap: "wrap",
              flex: "0 1 auto",
            }}
          >
            <button
              onClick={() => navigateScreen(screen === "history" ? "live" : "history")}
              style={{
                border: "1px solid var(--accent)",
                background: screen === "history" ? "white" : "var(--accent-dim)",
                borderRadius: 5,
                color: "var(--accent)",
                cursor: "pointer",
                fontFamily: "var(--mono)",
                fontSize: 10,
                padding: "8px 11px",
              }}
            >
              {screen === "history" ? "LIVE FIREWALL" : "SESSION HISTORY"}
            </button>
            <button
              onClick={() => load(false)}
              disabled={loading}
              style={{
                border: "1px solid var(--border)",
                background: "white",
                borderRadius: 5,
                color: loading ? "var(--ink3)" : "var(--accent)",
                cursor: loading ? "wait" : "pointer",
                fontFamily: "var(--mono)",
                fontSize: 10,
                padding: "8px 11px",
              }}
            >
              {loading ? "SYNCING" : "REFRESH"}
            </button>
          </div>
        </div>

        <div
          style={{
            display: "flex",
            gap: 7,
            flexWrap: "wrap",
            marginBottom: 12,
          }}
        >
          {RANGES.map((item) => {
            const active = item.key === range;
            return (
              <button
                key={item.key}
                onClick={() => setRange(item.key)}
                style={{
                  border: `1px solid ${active ? "var(--accent)" : "var(--border)"}`,
                  background: active ? "var(--accent-dim)" : "white",
                  color: active ? "var(--accent)" : "var(--ink2)",
                  borderRadius: 5,
                  padding: "6px 10px",
                  fontFamily: "var(--mono)",
                  fontSize: 10,
                  cursor: "pointer",
                }}
              >
                {item.label}
                <span style={{ color: "var(--ink3)", marginLeft: 6 }}>
                  {item.short}
                </span>
              </button>
            );
          })}
        </div>

        <div
          style={{
            display: "grid",
            gridTemplateColumns: "repeat(auto-fit, minmax(118px, 1fr))",
            gap: 10,
          }}
        >
          <MetricCard
            label={`${selectedRange.label} API value`}
            value={formatMoney(rangeTotals.apiValue)}
            sub="official input-rate estimate"
            accent="var(--green)"
          />
          <MetricCard
            label="Budget-capped savings"
            value={formatMoney(rangeTotals.cost)}
            sub={cappedByBudget ? `capped at ${formatMoney(budgetCap)}` : "same as API value for this range"}
            accent="var(--green)"
          />
          <MetricCard
            label={`${selectedRange.label} raw tokens avoided`}
            value={formatCompactNumber(rangeTotals.tokens)}
            sub={`${formatInteger(rangeTotals.tokens)} avoided input tokens`}
            accent="var(--green)"
          />
          <MetricCard
            label="Live governed sessions"
            value={formatInteger(activeTotals.sessions)}
            sub={`${formatCompactNumber(activeTotals.tokens)} active-session savings`}
            accent="var(--accent)"
          />
        </div>
      </section>

      {error ? (
        <div
          style={{
            border: "1px solid var(--rose)",
            background: "white",
            color: "var(--rose)",
            padding: "10px 12px",
            borderRadius: 6,
            fontFamily: "var(--mono)",
            fontSize: 11,
          }}
        >
          context firewall data unavailable: {error}
        </div>
      ) : null}

      {!loading && !error && !hasAnySessions ? (
        <FirstRunPanel
          body="Point Dhee at a repo folder, then start an agent task from that folder. The context firewall will fill with digests, evidence pointers, and expansions after the first mirrored Codex or Claude Code run."
          actions={[
            ...(onOpenFolders
              ? [{ label: "ADD REPO FOLDER", onClick: onOpenFolders, tone: "primary" as const }]
              : []),
            ...(onOpenSetup
              ? [{ label: "START TASK", onClick: onOpenSetup }]
              : []),
          ]}
        />
      ) : null}

      {screen === "history" ? (
        <Panel
          title="Session history"
          sub={`${rangeRows.length} sessions in the last ${selectedRange.short} · ${formatCompactNumber(rangeTotals.tokens)} tokens · ${formatMoney(rangeTotals.apiValue)} API value`}
          action={
            <button
              onClick={() => navigateScreen("live")}
              style={{
                border: "1px solid var(--border)",
                background: "white",
                borderRadius: 5,
                color: "var(--accent)",
                cursor: "pointer",
                fontFamily: "var(--mono)",
                fontSize: 10,
                padding: "7px 10px",
                whiteSpace: "nowrap",
              }}
            >
              LIVE FIREWALL
            </button>
          }
        >
          <SessionTable
            rows={rangeRows}
            selectedId={selected?.session_id || ""}
            onSelect={setSelectedId}
            loading={loading}
          />
        </Panel>
      ) : (
        <Panel
          title="Live governed sessions"
          sub={`${activeRows.length} active local agent session${activeRows.length === 1 ? "" : "s"} · click a session to inspect routing, evidence, and savings`}
          action={
            <button
              onClick={() => navigateScreen("history")}
              style={{
                border: "1px solid var(--border)",
                background: "white",
                borderRadius: 5,
                color: "var(--accent)",
                cursor: "pointer",
                fontFamily: "var(--mono)",
                fontSize: 10,
                padding: "7px 10px",
                whiteSpace: "nowrap",
              }}
            >
              HISTORY
            </button>
          }
        >
          {activeRows.length === 0 ? (
            <EmptyState>
              {loading ? "Loading active Claude Code and Codex sessions..." : "No active Claude Code or Codex sessions detected."}
            </EmptyState>
          ) : (
            <div style={{ display: "grid", gap: 8 }}>
              {activeRows.map((row) => (
                <ActiveSessionCard
                  key={row.session_id}
                  row={row}
                  selected={selected?.session_id === row.session_id}
                  onSelect={() =>
                    setSelectedId((current) =>
                      current === row.session_id ? "" : row.session_id
                    )
                  }
                />
              ))}
            </div>
          )}
        </Panel>
      )}
    </div>
  );
}

function ActiveSessionCard({
  row,
  selected,
  onSelect,
}: {
  row: RouterSessionRow;
  selected: boolean;
  onSelect: () => void;
}) {
  const color = agentColor(row.agent || row.runtime);
  const live = row.live_usage;
  return (
    <div
      className="router-active-card"
      style={{
        width: "100%",
        border: `1px solid ${selected ? color : "var(--border)"}`,
        background: selected ? "var(--surface)" : "white",
        borderRadius: 6,
        overflow: "hidden",
      }}
    >
      <button
        type="button"
        className="router-active-card__button"
        aria-expanded={selected}
        onClick={onSelect}
        style={{
          width: "100%",
          textAlign: "left",
          background: "transparent",
          border: 0,
          padding: 12,
          cursor: "pointer",
        }}
      >
        <div className="router-active-card__grid">
          <div className="router-active-card__main">
            <AgentBadge agent={row.agent || row.runtime || "unknown"} />
            <div
              className="router-active-card__title"
              style={{
                fontSize: 15,
                fontWeight: 600,
                color: "var(--ink)",
                marginTop: 4,
              }}
              title={row.title}
            >
              {row.title || row.session_id}
            </div>
            <div
              className="router-active-card__meta"
              style={{
                fontFamily: "var(--mono)",
                fontSize: 10,
                color: "var(--ink3)",
                marginTop: 3,
              }}
              title={row.cwd || row.repo_root}
            >
              {shortPath(row.repo_root || row.cwd)} - updated {relTime(row.updated_at)}
            </div>
          </div>
          <div className="router-active-card__stats">
            <MiniStat label="saved" value={formatCompactNumber(row.tokens_saved)} />
            <MiniStat label="API value" value={sessionCostLabel(row)} />
            <MiniStat
              label="live tokens"
              value={live?.available ? formatCompactNumber(live.total_tokens) : "n/a"}
            />
          </div>
          <div
            className="router-active-card__toggle"
            style={{
              fontFamily: "var(--mono)",
              fontSize: 18,
              lineHeight: 1,
              color: selected ? color : "var(--ink3)",
              textAlign: "right",
            }}
            aria-hidden="true"
          >
            {selected ? "-" : "+"}
          </div>
        </div>
      </button>
      {selected ? (
        <div
          style={{
            borderTop: "1px solid var(--border)",
            padding: "12px 12px 14px",
            background: "white",
          }}
        >
          <SelectedSession row={row} showHeader={false} />
        </div>
      ) : null}
    </div>
  );
}

function SessionTable({
  rows,
  selectedId,
  onSelect,
  loading,
}: {
  rows: RouterSessionRow[];
  selectedId: string;
  onSelect: (id: string) => void;
  loading: boolean;
}) {
  if (rows.length === 0) {
    return <EmptyState>{loading ? "Loading sessions..." : "No sessions in this range."}</EmptyState>;
  }
  return (
    <>
      <div
        className="router-session-table"
        style={{
          border: "1px solid var(--border)",
          borderRadius: 6,
          overflowX: "auto",
          background: "white",
        }}
      >
        <table
          style={{
            width: "100%",
            borderCollapse: "collapse",
            fontFamily: "var(--mono)",
            fontSize: 11,
          }}
        >
          <thead>
            <tr style={{ background: "var(--surface)" }}>
              <Th>Session</Th>
              <Th>Agent</Th>
              <Th>State</Th>
              <Th>Updated</Th>
              <Th align="right">Tokens saved</Th>
              <Th align="right">API value</Th>
              <Th align="right">Calls</Th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => {
              const selected = selectedId === row.session_id;
              return (
                <tr
                  key={row.session_id}
                  onClick={() => onSelect(row.session_id)}
                  style={{
                    borderTop: "1px solid var(--border)",
                    background: selected ? "oklch(0.98 0.02 262)" : "white",
                    cursor: "pointer",
                  }}
                >
                  <Td title={row.title || row.session_id}>
                    <div
                      style={{
                        color: "var(--ink)",
                        fontWeight: selected ? 700 : 500,
                        maxWidth: 420,
                        overflow: "hidden",
                        textOverflow: "ellipsis",
                        whiteSpace: "nowrap",
                      }}
                    >
                      {row.title || row.session_id}
                    </div>
                    <div
                      style={{
                        color: "var(--ink3)",
                        marginTop: 2,
                        maxWidth: 420,
                        overflow: "hidden",
                        textOverflow: "ellipsis",
                        whiteSpace: "nowrap",
                      }}
                      title={row.cwd || row.repo_root}
                    >
                      {shortPath(row.repo_root || row.cwd)}
                    </div>
                  </Td>
                  <Td>
                    <AgentBadge agent={row.agent || row.runtime || "unknown"} />
                  </Td>
                  <Td>
                    <StateBadge state={row.state} active={row.active} />
                  </Td>
                  <Td>{relTime(row.updated_at)}</Td>
                  <Td align="right">{formatInteger(row.tokens_saved)}</Td>
                  <Td align="right" title={pricingLabel(row)}>
                    {sessionCostLabel(row)}
                  </Td>
                  <Td align="right">{formatInteger(row.router_calls)}</Td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      <div className="router-session-cards" aria-label="Session history cards">
        {rows.map((row) => {
          const selected = selectedId === row.session_id;
          return (
            <button
              key={row.session_id}
              type="button"
              className={`router-session-card${selected ? " router-session-card--active" : ""}`}
              onClick={() => onSelect(row.session_id)}
              aria-pressed={selected}
            >
              <div className="router-session-card__head">
                <div className="router-session-card__title" title={row.title || row.session_id}>
                  {row.title || row.session_id}
                </div>
                <StateBadge state={row.state} active={row.active} />
              </div>
              <div className="router-session-card__meta">
                <AgentBadge agent={row.agent || row.runtime || "unknown"} />
                <span>{relTime(row.updated_at)}</span>
              </div>
              <div
                className="router-session-card__path"
                title={row.cwd || row.repo_root || undefined}
              >
                {shortPath(row.repo_root || row.cwd)}
              </div>
              <div className="router-session-card__stats">
                <MiniStat label="saved" value={formatCompactNumber(row.tokens_saved)} />
                <MiniStat label="API value" value={sessionCostLabel(row)} />
                <MiniStat label="calls" value={formatInteger(row.router_calls)} />
              </div>
            </button>
          );
        })}
      </div>
    </>
  );
}

function SelectedSession({
  row,
  showHeader = true,
}: {
  row: RouterSessionRow;
  showHeader?: boolean;
}) {
  const live = row.live_usage;
  const toolEntries = Object.entries(row.tool_breakdown || {}).sort((a, b) => b[1] - a[1]);
  return (
    <div style={{ display: "grid", gap: 10 }}>
      {showHeader ? (
        <div>
          <AgentBadge agent={row.agent || row.runtime || "unknown"} />
          <h2
            style={{
              margin: "6px 0 4px",
              fontSize: 18,
              lineHeight: 1.25,
              color: "var(--ink)",
              letterSpacing: 0,
              display: "-webkit-box",
              WebkitLineClamp: 3,
              WebkitBoxOrient: "vertical",
              overflow: "hidden",
            }}
            title={row.title || row.session_id}
          >
            {row.title || row.session_id}
          </h2>
          <div
            style={{
              fontFamily: "var(--mono)",
              fontSize: 10,
              color: "var(--ink3)",
              overflow: "hidden",
              textOverflow: "ellipsis",
              whiteSpace: "nowrap",
            }}
            title={row.cwd || row.repo_root || undefined}
          >
            {row.model || "model unavailable"} · {shortPath(row.cwd || row.repo_root)}
          </div>
        </div>
      ) : null}

      <div
        style={{
          display: "grid",
          gridTemplateColumns: "repeat(auto-fit, minmax(150px, 1fr))",
          gap: 8,
        }}
      >
        <MetricCard
          label="tokens saved"
          value={formatCompactNumber(row.tokens_saved)}
          sub={`${formatInteger(row.tokens_saved)} avoided`}
          accent="var(--green)"
        />
        <MetricCard
          label="API value"
          value={sessionCostLabel(row)}
          sub={hasOfficialRate(row) ? "API value" : "model unpriced"}
          accent="var(--green)"
        />
        <MetricCard
          label="router calls"
          value={formatInteger(row.router_calls)}
          sub="cached reads"
          accent="var(--ink2)"
        />
        <MetricCard
          label="live tokens"
          value={live?.available ? formatCompactNumber(live.total_tokens) : "n/a"}
          sub={live?.available ? "native telemetry" : "not captured"}
          accent={agentColor(row.agent || row.runtime)}
        />
      </div>

      <div
        style={{
          border: "1px solid var(--border)",
          borderRadius: 6,
          padding: 10,
          background: "white",
        }}
      >
        <Eyebrow>Pricing</Eyebrow>
        <div style={{ fontSize: 12, color: "var(--ink)", marginTop: 5 }}>
          {pricingLabel(row)}
        </div>
        {row.pricing?.source ? (
          <a
            href={row.pricing.source}
            target="_blank"
            rel="noreferrer"
            style={{
              display: "inline-block",
              marginTop: 7,
              fontFamily: "var(--mono)",
              fontSize: 10,
              color: "var(--accent)",
            }}
          >
            official pricing source
          </a>
        ) : null}
      </div>

      {live?.available ? <LiveUsagePanel row={row} /> : null}

      <div
        style={{
          border: "1px solid var(--border)",
          borderRadius: 6,
          padding: 10,
          background: "white",
        }}
      >
        <Eyebrow>Read savings by tool</Eyebrow>
        {toolEntries.length === 0 ? (
          <div style={{ color: "var(--ink3)", fontSize: 12, marginTop: 7 }}>
            No cached reads yet.
          </div>
        ) : (
          <div style={{ display: "grid", gap: 6, marginTop: 8 }}>
            {toolEntries.map(([tool, calls]) => (
              <div
                key={tool}
                style={{
                  display: "flex",
                  justifyContent: "space-between",
                  gap: 10,
                  fontFamily: "var(--mono)",
                  fontSize: 11,
                }}
              >
                <span style={{ color: "var(--ink2)" }}>{tool}</span>
                <span style={{ color: "var(--ink)" }}>{formatInteger(calls)} calls</span>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

function LiveUsagePanel({ row }: { row: RouterSessionRow }) {
  const live = row.live_usage;
  if (!live?.available) {
    return (
      <div
        style={{
          border: "1px solid var(--border)",
          borderRadius: 6,
          padding: 12,
          background: "white",
        }}
      >
        <Eyebrow>Live token usage</Eyebrow>
        <div style={{ color: "var(--ink3)", fontSize: 12, marginTop: 8 }}>
          No exact live token report captured for this session yet.
        </div>
      </div>
    );
  }

  const values = [
    ["Input", live.input_tokens],
    ["Cached input", live.cached_input_tokens],
    ["Output", live.output_tokens],
    ["Reasoning", live.reasoning_output_tokens],
    ["Last turn", live.last_turn_tokens],
    ["Context", live.context_window],
  ] as const;

  return (
    <div
      style={{
        border: "1px solid var(--border)",
        borderRadius: 6,
        padding: 12,
        background: "white",
      }}
    >
      <div style={{ display: "flex", justifyContent: "space-between", gap: 12 }}>
        <Eyebrow>Live token usage</Eyebrow>
        <span
          style={{
            fontFamily: "var(--mono)",
            fontSize: 10,
            color: "var(--green)",
            whiteSpace: "nowrap",
          }}
        >
          exact
        </span>
      </div>
      <div
        style={{
          display: "grid",
          gridTemplateColumns: "repeat(2, minmax(0, 1fr))",
          gap: 9,
          marginTop: 10,
        }}
      >
        {values.map(([label, value]) => (
          <MiniStat key={label} label={label} value={formatCompactNumber(value)} />
        ))}
      </div>
      <div
        style={{
          fontFamily: "var(--mono)",
          fontSize: 10,
          color: "var(--ink3)",
          marginTop: 10,
        }}
      >
        {live.source || "native telemetry"} - updated {relTime(live.updated_at || row.updated_at)}
      </div>
    </div>
  );
}

function Panel({
  title,
  sub,
  action,
  children,
}: {
  title: string;
  sub?: string;
  action?: ReactNode;
  children: ReactNode;
}) {
  return (
    <section
      style={{
        border: "1px solid var(--border)",
        background: "var(--bg)",
        borderRadius: 8,
        padding: 16,
        minWidth: 0,
        boxSizing: "border-box",
      }}
    >
      <div
        style={{
          display: "flex",
          justifyContent: "space-between",
          gap: 12,
          alignItems: "baseline",
          marginBottom: 12,
        }}
      >
        <div style={{ minWidth: 0 }}>
          <Eyebrow>{title}</Eyebrow>
          {sub ? (
            <div
              style={{
                marginTop: 4,
                color: "var(--ink3)",
                fontSize: 12,
                overflow: "hidden",
                textOverflow: "ellipsis",
                whiteSpace: "nowrap",
              }}
              title={sub}
            >
              {sub}
            </div>
          ) : null}
        </div>
        {action ? <div style={{ flexShrink: 0 }}>{action}</div> : null}
      </div>
      {children}
    </section>
  );
}

function MetricCard({
  label,
  value,
  sub,
  accent,
}: {
  label: string;
  value: string;
  sub?: string;
  accent: string;
}) {
  return (
    <div
      style={{
        border: "1px solid var(--border)",
        background: "white",
        borderRadius: 6,
        padding: 11,
        minWidth: 0,
      }}
    >
      <Eyebrow>{label}</Eyebrow>
      <div
        style={{
          marginTop: 7,
          fontFamily: "var(--mono)",
          fontSize: 22,
          lineHeight: 1.05,
          fontWeight: 700,
          color: accent,
          overflow: "hidden",
          textOverflow: "ellipsis",
        }}
      >
        {value}
      </div>
      {sub ? (
        <div style={{ color: "var(--ink3)", fontSize: 11, marginTop: 4 }}>{sub}</div>
      ) : null}
    </div>
  );
}

function MiniStat({ label, value }: { label: string; value: string }) {
  return (
    <div className="router-mini-stat" style={{ minWidth: 0 }}>
      <div
        style={{
          fontFamily: "var(--mono)",
          fontSize: 9,
          color: "var(--ink3)",
          textTransform: "uppercase",
          letterSpacing: "0.06em",
          marginBottom: 3,
        }}
      >
        {label}
      </div>
      <div
        style={{
          fontFamily: "var(--mono)",
        fontSize: 14,
          fontWeight: 700,
          color: "var(--ink)",
          whiteSpace: "nowrap",
          overflow: "hidden",
          textOverflow: "ellipsis",
        }}
        title={value}
      >
        {value}
      </div>
    </div>
  );
}

function AgentBadge({ agent }: { agent: string }) {
  const color = agentColor(agent);
  return (
    <span
      style={{
        display: "inline-flex",
        alignItems: "center",
        gap: 6,
        fontFamily: "var(--mono)",
        fontSize: 10,
        color,
        textTransform: "uppercase",
        letterSpacing: "0.06em",
      }}
    >
      <span
        style={{
          width: 7,
          height: 7,
          borderRadius: 999,
          background: color,
          flexShrink: 0,
        }}
      />
      {agentLabel(agent)}
    </span>
  );
}

function StateBadge({ state, active }: { state: string; active: boolean }) {
  const color = active ? "var(--green)" : "var(--ink3)";
  return (
    <span
      style={{
        border: `1px solid ${color}`,
        color,
        borderRadius: 4,
        padding: "1px 6px",
        fontSize: 10,
      }}
    >
      {active ? "active" : state || "n/a"}
    </span>
  );
}

function EmptyState({ children }: { children: ReactNode }) {
  return (
    <div
      style={{
        border: "1px dashed var(--border)",
        color: "var(--ink3)",
        background: "white",
        borderRadius: 6,
        padding: 18,
        textAlign: "center",
        fontSize: 12,
      }}
    >
      {children}
    </div>
  );
}

function Eyebrow({ children }: { children: ReactNode }) {
  return (
    <div
      style={{
        fontFamily: "var(--mono)",
        fontSize: 10,
        color: "var(--ink3)",
        letterSpacing: "0.08em",
        textTransform: "uppercase",
        fontWeight: 700,
      }}
    >
      {children}
    </div>
  );
}

function Th({
  children,
  align,
}: {
  children: ReactNode;
  align?: "left" | "right";
}) {
  return (
    <th
      style={{
        padding: "8px 10px",
        textAlign: align || "left",
        color: "var(--ink2)",
        fontWeight: 700,
        letterSpacing: "0.04em",
        borderBottom: "1px solid var(--border)",
        whiteSpace: "nowrap",
      }}
    >
      {children}
    </th>
  );
}

function Td({
  children,
  align,
  title,
}: {
  children: ReactNode;
  align?: "left" | "right";
  title?: string;
}) {
  return (
    <td
      title={title}
      style={{
        padding: "8px 10px",
        textAlign: align || "left",
        color: "var(--ink2)",
        verticalAlign: "middle",
      }}
    >
      {children}
    </td>
  );
}
