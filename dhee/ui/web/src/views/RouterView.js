import { jsx as _jsx, jsxs as _jsxs, Fragment as _Fragment } from "react/jsx-runtime";
import { useEffect, useMemo, useRef, useState } from "react";
import { api } from "../api";
import { FirstRunPanel } from "../components/FirstRunPanel";
const RANGES = [
    { key: "day", label: "Daily", short: "24h", ms: 24 * 60 * 60 * 1000 },
    { key: "week", label: "Weekly", short: "7d", ms: 7 * 24 * 60 * 60 * 1000 },
    { key: "month", label: "Monthly", short: "30d", ms: 30 * 24 * 60 * 60 * 1000 },
    { key: "year", label: "Yearly", short: "365d", ms: 365 * 24 * 60 * 60 * 1000 },
];
const EMPTY_PAGE = {
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
function formatCompactNumber(value) {
    if (value == null)
        return "0";
    return new Intl.NumberFormat("en", {
        notation: Math.abs(value) >= 10000 ? "compact" : "standard",
        maximumFractionDigits: Math.abs(value) >= 10000 ? 1 : 0,
    }).format(value);
}
function formatInteger(value) {
    return new Intl.NumberFormat("en-US").format(Math.max(0, Number(value || 0)));
}
function formatMoney(value) {
    const dollars = Math.max(0, Number(value || 0));
    if (dollars > 0 && dollars < 0.01)
        return "<$0.01";
    return new Intl.NumberFormat("en-US", {
        style: "currency",
        currency: "USD",
        maximumFractionDigits: dollars >= 100 ? 0 : 2,
    }).format(dollars);
}
function parseTime(value) {
    if (!value)
        return 0;
    const t = new Date(value).getTime();
    return Number.isNaN(t) ? 0 : t;
}
function relTime(value) {
    const t = parseTime(value);
    if (!t)
        return "n/a";
    const delta = Date.now() - t;
    if (delta < 60000)
        return "just now";
    if (delta < 3600000)
        return `${Math.floor(delta / 60000)}m ago`;
    if (delta < 86400000)
        return `${Math.floor(delta / 3600000)}h ago`;
    return `${Math.floor(delta / 86400000)}d ago`;
}
function shortPath(path) {
    if (!path)
        return "n/a";
    const parts = path.split("/").filter(Boolean);
    if (parts.length <= 2)
        return path;
    return ".../" + parts.slice(-2).join("/");
}
function agentKey(row) {
    const raw = typeof row === "string"
        ? row
        : row.agent || row.runtime || row.agents?.[0] || "unknown";
    const key = String(raw || "").toLowerCase();
    if (key.includes("codex"))
        return "codex";
    if (key.includes("claude"))
        return "claude-code";
    return key || "unknown";
}
function agentLabel(agent) {
    const key = agentKey(agent || "");
    if (key === "codex")
        return "Codex";
    if (key === "claude-code")
        return "Claude Code";
    return agent || "Unknown";
}
function agentColor(agent) {
    const key = agentKey(agent || "");
    if (key === "codex")
        return "var(--indigo)";
    if (key === "claude-code")
        return "var(--accent)";
    return "var(--ink3)";
}
function hasOfficialRate(row) {
    return Number(row.pricing?.input_cost_per_million || 0) > 0;
}
function sessionCostLabel(row) {
    if (row.tokens_saved > 0 && !hasOfficialRate(row))
        return "unpriced";
    return formatMoney(row.estimated_cost_saved_usd);
}
function isRunningSession(row) {
    const state = String(row.state || "").toLowerCase();
    return Boolean(row.active) && (state === "active" || state === "running" || state === "live");
}
function pricingLabel(row) {
    const pricing = row.pricing;
    if (!pricing || !hasOfficialRate(row)) {
        return pricing?.note || "No official provider/model rate mapped yet.";
    }
    const provider = pricing.provider || row.runtime || row.agent || "provider";
    const model = pricing.model_family || row.model || "model";
    return `${provider} ${model}: $${pricing.input_cost_per_million}/1M input tokens`;
}
function budgetCapForRange(budget, range) {
    if (!budget)
        return Number.POSITIVE_INFINITY;
    const key = range === "day"
        ? "daily_budget_usd"
        : range === "week"
            ? "weekly_budget_usd"
            : range === "year"
                ? "yearly_budget_usd"
                : "monthly_budget_usd";
    const cap = Number(budget[key] || 0);
    return cap > 0 ? cap : Number.POSITIVE_INFINITY;
}
function summarize(rows, budget, range) {
    return rows.reduce((acc, row) => {
        acc.tokens += row.tokens_saved || 0;
        acc.apiValue += Number(row.estimated_cost_saved_usd || 0);
        acc.calls += row.router_calls || 0;
        acc.sessions += 1;
        acc.cost = Math.min(acc.apiValue, budgetCapForRange(budget, range));
        return acc;
    }, { tokens: 0, apiValue: 0, cost: 0, calls: 0, sessions: 0 });
}
function routerScreenFromLocation() {
    const params = new URLSearchParams(window.location.search);
    const view = String(params.get("view") || "").toLowerCase();
    const path = window.location.pathname.replace(/^\/+|\/+$/g, "").toLowerCase();
    if (view === "router/sessionshistory" ||
        view === "router/session-history" ||
        view === "router/history" ||
        path === "router/sessionshistory")
        return "history";
    return "live";
}
function pushRouterScreen(screen) {
    const params = new URLSearchParams(window.location.search);
    params.set("view", screen === "history" ? "router/sessionshistory" : "router");
    const query = params.toString();
    const next = `${window.location.pathname}${query ? `?${query}` : ""}${window.location.hash || ""}`;
    window.history.pushState({}, "", next);
    window.dispatchEvent(new Event("popstate"));
}
export function RouterView({ onOpenFolders, onOpenSetup, }) {
    return (_jsx("div", { style: {
            height: "100%",
            overflowY: "auto",
            background: "var(--surface)",
        }, children: _jsx(RouterSavingsDashboard, { onOpenFolders: onOpenFolders, onOpenSetup: onOpenSetup }) }));
}
function RouterSavingsDashboard({ onOpenFolders, onOpenSetup, }) {
    const rootRef = useRef(null);
    const [historyPage, setHistoryPage] = useState(EMPTY_PAGE);
    const [activePage, setActivePage] = useState(EMPTY_PAGE);
    const [range, setRange] = useState("week");
    const [screen, setScreen] = useState(() => routerScreenFromLocation());
    const [selectedId, setSelectedId] = useState("");
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState(null);
    const load = async (silent = false) => {
        if (!silent)
            setLoading(true);
        setError(null);
        const activeRequest = api.routerSessions({ active: true, limit: 50 }).then((page) => ({ ok: true, page }), (error) => ({ ok: false, error }));
        const historyRequest = api.routerSessions({ active: false, limit: 100 }).then((page) => ({ ok: true, page }), (error) => ({ ok: false, error }));
        const errors = [];
        try {
            const active = await activeRequest;
            if (active.ok)
                setActivePage(active.page);
            else
                errors.push(String(active.error));
            const history = await historyRequest;
            if (history.ok)
                setHistoryPage(history.page);
            else
                errors.push(String(history.error));
            if (errors.length)
                setError(errors.join("; "));
        }
        finally {
            if (!silent)
                setLoading(false);
        }
    };
    useEffect(() => {
        void load(false);
        const timer = window.setInterval(() => void load(true), 15000);
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
    const activeRows = useMemo(() => [...activePage.items].filter(isRunningSession).sort((a, b) => parseTime(b.updated_at) - parseTime(a.updated_at)), [activePage.items]);
    const rangeRows = useMemo(() => {
        const selected = RANGES.find((item) => item.key === range) || RANGES[1];
        const floor = Date.now() - selected.ms;
        return [...historyPage.items]
            .filter((row) => parseTime(row.updated_at || row.started_at) >= floor)
            .sort((a, b) => parseTime(b.updated_at) - parseTime(a.updated_at));
    }, [historyPage.items, range]);
    const selected = screen === "history"
        ? rangeRows.find((row) => row.session_id === selectedId) || null
        : activeRows.find((row) => row.session_id === selectedId) || null;
    const budget = historyPage.budget || activePage.budget;
    const rangeTotals = summarize(rangeRows, budget, range);
    const activeTotals = summarize(activeRows, budget, "day");
    const hasAnySessions = activePage.items.length > 0 || historyPage.items.length > 0;
    const selectedRange = RANGES.find((item) => item.key === range) || RANGES[1];
    const budgetCap = budgetCapForRange(budget, range);
    const cappedByBudget = Number.isFinite(budgetCap) && rangeTotals.apiValue > budgetCap;
    const navigateScreen = (next) => {
        setScreen(next);
        pushRouterScreen(next);
    };
    return (_jsxs("div", { ref: rootRef, style: {
            padding: "clamp(10px, 3vw, 18px)",
            display: "grid",
            gap: 14,
            minWidth: 0,
            width: "100%",
            boxSizing: "border-box",
        }, children: [_jsxs("section", { style: {
                    border: "1px solid var(--border)",
                    background: "var(--bg)",
                    borderRadius: 8,
                    padding: 16,
                    minWidth: 0,
                    boxSizing: "border-box",
                }, children: [_jsxs("div", { style: {
                            display: "flex",
                            flexWrap: "wrap",
                            gap: 14,
                            alignItems: "start",
                            justifyContent: "space-between",
                            marginBottom: 14,
                        }, children: [_jsxs("div", { style: { flex: "1 1 240px", minWidth: 0 }, children: [_jsx(Eyebrow, { children: "Context Firewall" }), _jsx("h1", { style: {
                                            margin: "2px 0 0",
                                            fontSize: 26,
                                            lineHeight: 1.1,
                                            color: "var(--ink)",
                                            letterSpacing: 0,
                                        }, children: screen === "history" ? "Firewall session history" : "Live context firewall" }), _jsx("p", { style: {
                                            margin: "6px 0 0",
                                            color: "var(--ink3)",
                                            fontSize: 12,
                                            maxWidth: 780,
                                        }, children: screen === "history"
                                            ? "Every completed and recent local agent session, with pointer-backed evidence and avoided raw context."
                                            : "Running Claude Code and Codex sessions, with raw output kept behind digests until the agent asks to expand." })] }), _jsxs("div", { style: {
                                    display: "flex",
                                    gap: 8,
                                    justifyContent: "flex-start",
                                    flexWrap: "wrap",
                                    flex: "0 1 auto",
                                }, children: [_jsx("button", { onClick: () => navigateScreen(screen === "history" ? "live" : "history"), style: {
                                            border: "1px solid var(--accent)",
                                            background: screen === "history" ? "white" : "var(--accent-dim)",
                                            borderRadius: 5,
                                            color: "var(--accent)",
                                            cursor: "pointer",
                                            fontFamily: "var(--mono)",
                                            fontSize: 10,
                                            padding: "8px 11px",
                                        }, children: screen === "history" ? "LIVE FIREWALL" : "SESSION HISTORY" }), _jsx("button", { onClick: () => load(false), disabled: loading, style: {
                                            border: "1px solid var(--border)",
                                            background: "white",
                                            borderRadius: 5,
                                            color: loading ? "var(--ink3)" : "var(--accent)",
                                            cursor: loading ? "wait" : "pointer",
                                            fontFamily: "var(--mono)",
                                            fontSize: 10,
                                            padding: "8px 11px",
                                        }, children: loading ? "SYNCING" : "REFRESH" })] })] }), _jsx("div", { style: {
                            display: "flex",
                            gap: 7,
                            flexWrap: "wrap",
                            marginBottom: 12,
                        }, children: RANGES.map((item) => {
                            const active = item.key === range;
                            return (_jsxs("button", { onClick: () => setRange(item.key), style: {
                                    border: `1px solid ${active ? "var(--accent)" : "var(--border)"}`,
                                    background: active ? "var(--accent-dim)" : "white",
                                    color: active ? "var(--accent)" : "var(--ink2)",
                                    borderRadius: 5,
                                    padding: "6px 10px",
                                    fontFamily: "var(--mono)",
                                    fontSize: 10,
                                    cursor: "pointer",
                                }, children: [item.label, _jsx("span", { style: { color: "var(--ink3)", marginLeft: 6 }, children: item.short })] }, item.key));
                        }) }), _jsxs("div", { style: {
                            display: "grid",
                            gridTemplateColumns: "repeat(auto-fit, minmax(118px, 1fr))",
                            gap: 10,
                        }, children: [_jsx(MetricCard, { label: `${selectedRange.label} API value`, value: formatMoney(rangeTotals.apiValue), sub: "official input-rate estimate", accent: "var(--green)" }), _jsx(MetricCard, { label: "Budget-capped savings", value: formatMoney(rangeTotals.cost), sub: cappedByBudget ? `capped at ${formatMoney(budgetCap)}` : "same as API value for this range", accent: "var(--green)" }), _jsx(MetricCard, { label: `${selectedRange.label} raw tokens avoided`, value: formatCompactNumber(rangeTotals.tokens), sub: `${formatInteger(rangeTotals.tokens)} avoided input tokens`, accent: "var(--green)" }), _jsx(MetricCard, { label: "Live governed sessions", value: formatInteger(activeTotals.sessions), sub: `${formatCompactNumber(activeTotals.tokens)} active-session savings`, accent: "var(--accent)" })] })] }), error ? (_jsxs("div", { style: {
                    border: "1px solid var(--rose)",
                    background: "white",
                    color: "var(--rose)",
                    padding: "10px 12px",
                    borderRadius: 6,
                    fontFamily: "var(--mono)",
                    fontSize: 11,
                }, children: ["context firewall data unavailable: ", error] })) : null, !loading && !error && !hasAnySessions ? (_jsx(FirstRunPanel, { body: "Point Dhee at a repo folder, then start an agent task from that folder. The context firewall will fill with digests, evidence pointers, and expansions after the first mirrored Codex or Claude Code run.", actions: [
                    ...(onOpenFolders
                        ? [{ label: "ADD REPO FOLDER", onClick: onOpenFolders, tone: "primary" }]
                        : []),
                    ...(onOpenSetup
                        ? [{ label: "START TASK", onClick: onOpenSetup }]
                        : []),
                ] })) : null, screen === "history" ? (_jsx(Panel, { title: "Session history", sub: `${rangeRows.length} sessions in the last ${selectedRange.short} · ${formatCompactNumber(rangeTotals.tokens)} tokens · ${formatMoney(rangeTotals.apiValue)} API value`, action: _jsx("button", { onClick: () => navigateScreen("live"), style: {
                        border: "1px solid var(--border)",
                        background: "white",
                        borderRadius: 5,
                        color: "var(--accent)",
                        cursor: "pointer",
                        fontFamily: "var(--mono)",
                        fontSize: 10,
                        padding: "7px 10px",
                        whiteSpace: "nowrap",
                    }, children: "LIVE FIREWALL" }), children: _jsx(SessionTable, { rows: rangeRows, selectedId: selected?.session_id || "", onSelect: setSelectedId, loading: loading }) })) : (_jsx(Panel, { title: "Live governed sessions", sub: `${activeRows.length} active local agent session${activeRows.length === 1 ? "" : "s"} · click a session to inspect routing, evidence, and savings`, action: _jsx("button", { onClick: () => navigateScreen("history"), style: {
                        border: "1px solid var(--border)",
                        background: "white",
                        borderRadius: 5,
                        color: "var(--accent)",
                        cursor: "pointer",
                        fontFamily: "var(--mono)",
                        fontSize: 10,
                        padding: "7px 10px",
                        whiteSpace: "nowrap",
                    }, children: "HISTORY" }), children: activeRows.length === 0 ? (_jsx(EmptyState, { children: loading ? "Loading active Claude Code and Codex sessions..." : "No active Claude Code or Codex sessions detected." })) : (_jsx("div", { style: { display: "grid", gap: 8 }, children: activeRows.map((row) => (_jsx(ActiveSessionCard, { row: row, selected: selected?.session_id === row.session_id, onSelect: () => setSelectedId((current) => current === row.session_id ? "" : row.session_id) }, row.session_id))) })) }))] }));
}
function ActiveSessionCard({ row, selected, onSelect, }) {
    const color = agentColor(row.agent || row.runtime);
    const live = row.live_usage;
    return (_jsxs("div", { className: "router-active-card", style: {
            width: "100%",
            border: `1px solid ${selected ? color : "var(--border)"}`,
            background: selected ? "var(--surface)" : "white",
            borderRadius: 6,
            overflow: "hidden",
        }, children: [_jsx("button", { type: "button", className: "router-active-card__button", "aria-expanded": selected, onClick: onSelect, style: {
                    width: "100%",
                    textAlign: "left",
                    background: "transparent",
                    border: 0,
                    padding: 12,
                    cursor: "pointer",
                }, children: _jsxs("div", { className: "router-active-card__grid", children: [_jsxs("div", { className: "router-active-card__main", children: [_jsx(AgentBadge, { agent: row.agent || row.runtime || "unknown" }), _jsx("div", { className: "router-active-card__title", style: {
                                        fontSize: 15,
                                        fontWeight: 600,
                                        color: "var(--ink)",
                                        marginTop: 4,
                                    }, title: row.title, children: row.title || row.session_id }), _jsxs("div", { className: "router-active-card__meta", style: {
                                        fontFamily: "var(--mono)",
                                        fontSize: 10,
                                        color: "var(--ink3)",
                                        marginTop: 3,
                                    }, title: row.cwd || row.repo_root, children: [shortPath(row.repo_root || row.cwd), " - updated ", relTime(row.updated_at)] })] }), _jsxs("div", { className: "router-active-card__stats", children: [_jsx(MiniStat, { label: "saved", value: formatCompactNumber(row.tokens_saved) }), _jsx(MiniStat, { label: "API value", value: sessionCostLabel(row) }), _jsx(MiniStat, { label: "live tokens", value: live?.available ? formatCompactNumber(live.total_tokens) : "n/a" })] }), _jsx("div", { className: "router-active-card__toggle", style: {
                                fontFamily: "var(--mono)",
                                fontSize: 18,
                                lineHeight: 1,
                                color: selected ? color : "var(--ink3)",
                                textAlign: "right",
                            }, "aria-hidden": "true", children: selected ? "-" : "+" })] }) }), selected ? (_jsx("div", { style: {
                    borderTop: "1px solid var(--border)",
                    padding: "12px 12px 14px",
                    background: "white",
                }, children: _jsx(SelectedSession, { row: row, showHeader: false }) })) : null] }));
}
function SessionTable({ rows, selectedId, onSelect, loading, }) {
    if (rows.length === 0) {
        return _jsx(EmptyState, { children: loading ? "Loading sessions..." : "No sessions in this range." });
    }
    return (_jsxs(_Fragment, { children: [_jsx("div", { className: "router-session-table", style: {
                    border: "1px solid var(--border)",
                    borderRadius: 6,
                    overflowX: "auto",
                    background: "white",
                }, children: _jsxs("table", { style: {
                        width: "100%",
                        borderCollapse: "collapse",
                        fontFamily: "var(--mono)",
                        fontSize: 11,
                    }, children: [_jsx("thead", { children: _jsxs("tr", { style: { background: "var(--surface)" }, children: [_jsx(Th, { children: "Session" }), _jsx(Th, { children: "Agent" }), _jsx(Th, { children: "State" }), _jsx(Th, { children: "Updated" }), _jsx(Th, { align: "right", children: "Tokens saved" }), _jsx(Th, { align: "right", children: "API value" }), _jsx(Th, { align: "right", children: "Calls" })] }) }), _jsx("tbody", { children: rows.map((row) => {
                                const selected = selectedId === row.session_id;
                                return (_jsxs("tr", { onClick: () => onSelect(row.session_id), style: {
                                        borderTop: "1px solid var(--border)",
                                        background: selected ? "oklch(0.98 0.02 262)" : "white",
                                        cursor: "pointer",
                                    }, children: [_jsxs(Td, { title: row.title || row.session_id, children: [_jsx("div", { style: {
                                                        color: "var(--ink)",
                                                        fontWeight: selected ? 700 : 500,
                                                        maxWidth: 420,
                                                        overflow: "hidden",
                                                        textOverflow: "ellipsis",
                                                        whiteSpace: "nowrap",
                                                    }, children: row.title || row.session_id }), _jsx("div", { style: {
                                                        color: "var(--ink3)",
                                                        marginTop: 2,
                                                        maxWidth: 420,
                                                        overflow: "hidden",
                                                        textOverflow: "ellipsis",
                                                        whiteSpace: "nowrap",
                                                    }, title: row.cwd || row.repo_root, children: shortPath(row.repo_root || row.cwd) })] }), _jsx(Td, { children: _jsx(AgentBadge, { agent: row.agent || row.runtime || "unknown" }) }), _jsx(Td, { children: _jsx(StateBadge, { state: row.state, active: row.active }) }), _jsx(Td, { children: relTime(row.updated_at) }), _jsx(Td, { align: "right", children: formatInteger(row.tokens_saved) }), _jsx(Td, { align: "right", title: pricingLabel(row), children: sessionCostLabel(row) }), _jsx(Td, { align: "right", children: formatInteger(row.router_calls) })] }, row.session_id));
                            }) })] }) }), _jsx("div", { className: "router-session-cards", "aria-label": "Session history cards", children: rows.map((row) => {
                    const selected = selectedId === row.session_id;
                    return (_jsxs("button", { type: "button", className: `router-session-card${selected ? " router-session-card--active" : ""}`, onClick: () => onSelect(row.session_id), "aria-pressed": selected, children: [_jsxs("div", { className: "router-session-card__head", children: [_jsx("div", { className: "router-session-card__title", title: row.title || row.session_id, children: row.title || row.session_id }), _jsx(StateBadge, { state: row.state, active: row.active })] }), _jsxs("div", { className: "router-session-card__meta", children: [_jsx(AgentBadge, { agent: row.agent || row.runtime || "unknown" }), _jsx("span", { children: relTime(row.updated_at) })] }), _jsx("div", { className: "router-session-card__path", title: row.cwd || row.repo_root || undefined, children: shortPath(row.repo_root || row.cwd) }), _jsxs("div", { className: "router-session-card__stats", children: [_jsx(MiniStat, { label: "saved", value: formatCompactNumber(row.tokens_saved) }), _jsx(MiniStat, { label: "API value", value: sessionCostLabel(row) }), _jsx(MiniStat, { label: "calls", value: formatInteger(row.router_calls) })] })] }, row.session_id));
                }) })] }));
}
function SelectedSession({ row, showHeader = true, }) {
    const live = row.live_usage;
    const toolEntries = Object.entries(row.tool_breakdown || {}).sort((a, b) => b[1] - a[1]);
    return (_jsxs("div", { style: { display: "grid", gap: 10 }, children: [showHeader ? (_jsxs("div", { children: [_jsx(AgentBadge, { agent: row.agent || row.runtime || "unknown" }), _jsx("h2", { style: {
                            margin: "6px 0 4px",
                            fontSize: 18,
                            lineHeight: 1.25,
                            color: "var(--ink)",
                            letterSpacing: 0,
                            display: "-webkit-box",
                            WebkitLineClamp: 3,
                            WebkitBoxOrient: "vertical",
                            overflow: "hidden",
                        }, title: row.title || row.session_id, children: row.title || row.session_id }), _jsxs("div", { style: {
                            fontFamily: "var(--mono)",
                            fontSize: 10,
                            color: "var(--ink3)",
                            overflow: "hidden",
                            textOverflow: "ellipsis",
                            whiteSpace: "nowrap",
                        }, title: row.cwd || row.repo_root || undefined, children: [row.model || "model unavailable", " \u00B7 ", shortPath(row.cwd || row.repo_root)] })] })) : null, _jsxs("div", { style: {
                    display: "grid",
                    gridTemplateColumns: "repeat(auto-fit, minmax(150px, 1fr))",
                    gap: 8,
                }, children: [_jsx(MetricCard, { label: "tokens saved", value: formatCompactNumber(row.tokens_saved), sub: `${formatInteger(row.tokens_saved)} avoided`, accent: "var(--green)" }), _jsx(MetricCard, { label: "API value", value: sessionCostLabel(row), sub: hasOfficialRate(row) ? "API value" : "model unpriced", accent: "var(--green)" }), _jsx(MetricCard, { label: "router calls", value: formatInteger(row.router_calls), sub: "cached reads", accent: "var(--ink2)" }), _jsx(MetricCard, { label: "live tokens", value: live?.available ? formatCompactNumber(live.total_tokens) : "n/a", sub: live?.available ? "native telemetry" : "not captured", accent: agentColor(row.agent || row.runtime) })] }), _jsxs("div", { style: {
                    border: "1px solid var(--border)",
                    borderRadius: 6,
                    padding: 10,
                    background: "white",
                }, children: [_jsx(Eyebrow, { children: "Pricing" }), _jsx("div", { style: { fontSize: 12, color: "var(--ink)", marginTop: 5 }, children: pricingLabel(row) }), row.pricing?.source ? (_jsx("a", { href: row.pricing.source, target: "_blank", rel: "noreferrer", style: {
                            display: "inline-block",
                            marginTop: 7,
                            fontFamily: "var(--mono)",
                            fontSize: 10,
                            color: "var(--accent)",
                        }, children: "official pricing source" })) : null] }), live?.available ? _jsx(LiveUsagePanel, { row: row }) : null, _jsxs("div", { style: {
                    border: "1px solid var(--border)",
                    borderRadius: 6,
                    padding: 10,
                    background: "white",
                }, children: [_jsx(Eyebrow, { children: "Read savings by tool" }), toolEntries.length === 0 ? (_jsx("div", { style: { color: "var(--ink3)", fontSize: 12, marginTop: 7 }, children: "No cached reads yet." })) : (_jsx("div", { style: { display: "grid", gap: 6, marginTop: 8 }, children: toolEntries.map(([tool, calls]) => (_jsxs("div", { style: {
                                display: "flex",
                                justifyContent: "space-between",
                                gap: 10,
                                fontFamily: "var(--mono)",
                                fontSize: 11,
                            }, children: [_jsx("span", { style: { color: "var(--ink2)" }, children: tool }), _jsxs("span", { style: { color: "var(--ink)" }, children: [formatInteger(calls), " calls"] })] }, tool))) }))] })] }));
}
function LiveUsagePanel({ row }) {
    const live = row.live_usage;
    if (!live?.available) {
        return (_jsxs("div", { style: {
                border: "1px solid var(--border)",
                borderRadius: 6,
                padding: 12,
                background: "white",
            }, children: [_jsx(Eyebrow, { children: "Live token usage" }), _jsx("div", { style: { color: "var(--ink3)", fontSize: 12, marginTop: 8 }, children: "No exact live token report captured for this session yet." })] }));
    }
    const values = [
        ["Input", live.input_tokens],
        ["Cached input", live.cached_input_tokens],
        ["Output", live.output_tokens],
        ["Reasoning", live.reasoning_output_tokens],
        ["Last turn", live.last_turn_tokens],
        ["Context", live.context_window],
    ];
    return (_jsxs("div", { style: {
            border: "1px solid var(--border)",
            borderRadius: 6,
            padding: 12,
            background: "white",
        }, children: [_jsxs("div", { style: { display: "flex", justifyContent: "space-between", gap: 12 }, children: [_jsx(Eyebrow, { children: "Live token usage" }), _jsx("span", { style: {
                            fontFamily: "var(--mono)",
                            fontSize: 10,
                            color: "var(--green)",
                            whiteSpace: "nowrap",
                        }, children: "exact" })] }), _jsx("div", { style: {
                    display: "grid",
                    gridTemplateColumns: "repeat(2, minmax(0, 1fr))",
                    gap: 9,
                    marginTop: 10,
                }, children: values.map(([label, value]) => (_jsx(MiniStat, { label: label, value: formatCompactNumber(value) }, label))) }), _jsxs("div", { style: {
                    fontFamily: "var(--mono)",
                    fontSize: 10,
                    color: "var(--ink3)",
                    marginTop: 10,
                }, children: [live.source || "native telemetry", " - updated ", relTime(live.updated_at || row.updated_at)] })] }));
}
function Panel({ title, sub, action, children, }) {
    return (_jsxs("section", { style: {
            border: "1px solid var(--border)",
            background: "var(--bg)",
            borderRadius: 8,
            padding: 16,
            minWidth: 0,
            boxSizing: "border-box",
        }, children: [_jsxs("div", { style: {
                    display: "flex",
                    justifyContent: "space-between",
                    gap: 12,
                    alignItems: "baseline",
                    marginBottom: 12,
                }, children: [_jsxs("div", { style: { minWidth: 0 }, children: [_jsx(Eyebrow, { children: title }), sub ? (_jsx("div", { style: {
                                    marginTop: 4,
                                    color: "var(--ink3)",
                                    fontSize: 12,
                                    overflow: "hidden",
                                    textOverflow: "ellipsis",
                                    whiteSpace: "nowrap",
                                }, title: sub, children: sub })) : null] }), action ? _jsx("div", { style: { flexShrink: 0 }, children: action }) : null] }), children] }));
}
function MetricCard({ label, value, sub, accent, }) {
    return (_jsxs("div", { style: {
            border: "1px solid var(--border)",
            background: "white",
            borderRadius: 6,
            padding: 11,
            minWidth: 0,
        }, children: [_jsx(Eyebrow, { children: label }), _jsx("div", { style: {
                    marginTop: 7,
                    fontFamily: "var(--mono)",
                    fontSize: 22,
                    lineHeight: 1.05,
                    fontWeight: 700,
                    color: accent,
                    overflow: "hidden",
                    textOverflow: "ellipsis",
                }, children: value }), sub ? (_jsx("div", { style: { color: "var(--ink3)", fontSize: 11, marginTop: 4 }, children: sub })) : null] }));
}
function MiniStat({ label, value }) {
    return (_jsxs("div", { className: "router-mini-stat", style: { minWidth: 0 }, children: [_jsx("div", { style: {
                    fontFamily: "var(--mono)",
                    fontSize: 9,
                    color: "var(--ink3)",
                    textTransform: "uppercase",
                    letterSpacing: "0.06em",
                    marginBottom: 3,
                }, children: label }), _jsx("div", { style: {
                    fontFamily: "var(--mono)",
                    fontSize: 14,
                    fontWeight: 700,
                    color: "var(--ink)",
                    whiteSpace: "nowrap",
                    overflow: "hidden",
                    textOverflow: "ellipsis",
                }, title: value, children: value })] }));
}
function AgentBadge({ agent }) {
    const color = agentColor(agent);
    return (_jsxs("span", { style: {
            display: "inline-flex",
            alignItems: "center",
            gap: 6,
            fontFamily: "var(--mono)",
            fontSize: 10,
            color,
            textTransform: "uppercase",
            letterSpacing: "0.06em",
        }, children: [_jsx("span", { style: {
                    width: 7,
                    height: 7,
                    borderRadius: 999,
                    background: color,
                    flexShrink: 0,
                } }), agentLabel(agent)] }));
}
function StateBadge({ state, active }) {
    const color = active ? "var(--green)" : "var(--ink3)";
    return (_jsx("span", { style: {
            border: `1px solid ${color}`,
            color,
            borderRadius: 4,
            padding: "1px 6px",
            fontSize: 10,
        }, children: active ? "active" : state || "n/a" }));
}
function EmptyState({ children }) {
    return (_jsx("div", { style: {
            border: "1px dashed var(--border)",
            color: "var(--ink3)",
            background: "white",
            borderRadius: 6,
            padding: 18,
            textAlign: "center",
            fontSize: 12,
        }, children: children }));
}
function Eyebrow({ children }) {
    return (_jsx("div", { style: {
            fontFamily: "var(--mono)",
            fontSize: 10,
            color: "var(--ink3)",
            letterSpacing: "0.08em",
            textTransform: "uppercase",
            fontWeight: 700,
        }, children: children }));
}
function Th({ children, align, }) {
    return (_jsx("th", { style: {
            padding: "8px 10px",
            textAlign: align || "left",
            color: "var(--ink2)",
            fontWeight: 700,
            letterSpacing: "0.04em",
            borderBottom: "1px solid var(--border)",
            whiteSpace: "nowrap",
        }, children: children }));
}
function Td({ children, align, title, }) {
    return (_jsx("td", { title: title, style: {
            padding: "8px 10px",
            textAlign: align || "left",
            color: "var(--ink2)",
            verticalAlign: "middle",
        }, children: children }));
}
