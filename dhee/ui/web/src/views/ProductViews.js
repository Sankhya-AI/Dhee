import { jsx as _jsx, jsxs as _jsxs, Fragment as _Fragment } from "react/jsx-runtime";
import { useEffect, useState } from "react";
import { api } from "../api";
function asRows(value) {
    return Array.isArray(value) ? value.filter(Boolean) : [];
}
function get(obj, key, fallback = undefined) {
    if (!obj || typeof obj !== "object")
        return fallback;
    const row = obj;
    return row[key] ?? fallback;
}
function compact(value) {
    const n = Number(value || 0);
    return new Intl.NumberFormat("en", {
        notation: Math.abs(n) >= 10000 ? "compact" : "standard",
        maximumFractionDigits: Math.abs(n) >= 10000 ? 1 : 0,
    }).format(n);
}
function money(value) {
    const n = Number(value || 0);
    return new Intl.NumberFormat("en-US", {
        style: "currency",
        currency: "USD",
        maximumFractionDigits: n >= 100 ? 0 : 2,
    }).format(n);
}
function shortPath(value) {
    const raw = String(value || "");
    if (!raw)
        return "not linked";
    const parts = raw.split("/").filter(Boolean);
    return parts.length > 3 ? `.../${parts.slice(-3).join("/")}` : raw;
}
function timeLabel(value) {
    if (!value)
        return "no timestamp";
    let t;
    if (typeof value === "number") {
        t = value < 10000000000 ? value * 1000 : value;
    }
    else {
        const raw = String(value).trim();
        const numeric = Number(raw);
        t = raw && !Number.isNaN(numeric) ? (numeric < 10000000000 ? numeric * 1000 : numeric) : new Date(raw).getTime();
    }
    if (Number.isNaN(t))
        return String(value);
    const delta = Date.now() - t;
    if (delta < 60000)
        return "just now";
    if (delta < 3600000)
        return `${Math.floor(delta / 60000)}m ago`;
    if (delta < 86400000)
        return `${Math.floor(delta / 3600000)}h ago`;
    return `${Math.floor(delta / 86400000)}d ago`;
}
function learningPreview(row) {
    const preview = String(row.preview || row.body || "").replace(/\s+/g, " ").trim();
    return preview || "No evidence preview captured yet.";
}
function learningMeta(row) {
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
function toneFor(value) {
    const raw = String(value || "").toLowerCase();
    if (raw.includes("reject") || raw.includes("fail") || raw.includes("stale"))
        return "var(--rose)";
    if (raw.includes("pending") || raw.includes("candidate") || raw.includes("derived"))
        return "var(--accent)";
    if (raw.includes("promoted") || raw.includes("active") || raw.includes("ok"))
        return "var(--green)";
    if (raw.includes("evidence") || raw.includes("digest"))
        return "var(--indigo)";
    return "var(--ink3)";
}
function useScreenData(loader) {
    const [data, setData] = useState(null);
    const [error, setError] = useState("");
    const [loading, setLoading] = useState(true);
    const refresh = async () => {
        setLoading(true);
        setError("");
        try {
            setData(await loader());
        }
        catch (e) {
            setError(String(e));
        }
        finally {
            setLoading(false);
        }
    };
    useEffect(() => {
        void refresh();
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, []);
    return { data, error, loading, refresh };
}
function Screen({ eyebrow, title, subtitle, children, action, }) {
    return (_jsx("div", { className: "product-screen", children: _jsxs("div", { className: "product-content", children: [_jsxs("div", { className: "product-hero", children: [_jsxs("div", { className: "product-hero-copy", children: [_jsx("div", { className: "product-eyebrow", children: eyebrow }), _jsx("h1", { className: "product-title", children: title }), _jsx("p", { className: "product-subtitle", children: subtitle })] }), action ? _jsx("div", { className: "product-hero-action", children: action }) : null] }), children] }) }));
}
function Panel({ label, children, style, }) {
    return (_jsxs("section", { className: "product-panel", style: {
            ...style,
        }, children: [label ? _jsx("div", { className: "product-panel-label", children: label }) : null, children] }));
}
function Metric({ label, value, tone }) {
    return (_jsxs(Panel, { style: { minHeight: 96 }, children: [_jsx("div", { className: "product-metric-value", style: { color: tone || "var(--ink)" }, children: value }), _jsx("div", { className: "product-metric-label", children: label })] }));
}
function Pill({ children, tone }) {
    return (_jsx("span", { className: "product-pill", style: {
            color: tone || "var(--ink2)",
        }, children: children }));
}
function RowList({ rows, empty, render, }) {
    if (!rows.length) {
        return _jsx("div", { className: "product-empty", children: empty });
    }
    return _jsx("div", { className: "product-list", children: rows.map(render) });
}
function LoadingState({ loading, error }) {
    if (loading)
        return _jsx(Panel, { children: "Loading Dhee state..." });
    if (error)
        return _jsx(Panel, { children: _jsx("span", { style: { color: "var(--rose)" }, children: error }) });
    return null;
}
export function CommandCenterView({ onNavigate }) {
    const { data, error, loading, refresh } = useScreenData(api.commandCenter);
    const router = get(data, "router", {});
    const context = get(data, "context", {});
    const learnings = get(data, "learnings", {});
    const inbox = get(data, "inbox", {});
    const activeTask = get(data, "active_task", null);
    const sessions = asRows(get(data, "router_sessions", []));
    const learningTotals = get(learnings, "totals", {});
    const inboxTotals = get(inbox, "totals", {});
    const aliases = (get(data, "dhee_aliases", []) || []).filter(Boolean);
    return (_jsxs(Screen, { eyebrow: "COMMAND CENTER", title: "The current truth before the agent sees anything.", subtitle: "Start here to see task continuity, context health, routed savings, review queues, and the next best action for this repo.", action: _jsx("button", { onClick: refresh, style: buttonStyle, children: "refresh" }), children: [_jsx(LoadingState, { loading: loading, error: error }), data ? (_jsxs(_Fragment, { children: [_jsxs("div", { className: "product-metric-grid", children: [_jsx(Metric, { label: "tokens avoided", value: compact(get(router, "sessionTokensSaved", 0)), tone: "var(--green)" }), _jsx(Metric, { label: "router calls", value: compact(get(router, "totalCalls", 0)), tone: "var(--accent)" }), _jsx(Metric, { label: "repo context", value: compact(get(get(context, "totals", {}), "repo_entries", 0)), tone: "var(--indigo)" }), _jsx(Metric, { label: "learning candidates", value: compact(get(learningTotals, "candidate", 0)), tone: "var(--accent)" })] }), _jsxs("div", { className: "product-grid product-grid--two", children: [_jsx(Panel, { label: "ACTIVE WORK", children: activeTask ? (_jsxs("div", { children: [_jsx("div", { style: { fontSize: 22, lineHeight: 1.2, fontWeight: 700 }, children: String(get(activeTask, "title", "Active task")) }), _jsxs("div", { style: { marginTop: 8, display: "flex", gap: 8, flexWrap: "wrap" }, children: [_jsx(Pill, { tone: toneFor(get(activeTask, "status")), children: String(get(activeTask, "status", "active")) }), _jsx(Pill, { children: String(get(activeTask, "harness", "agent")) })] })] })) : (_jsx("div", { style: { color: "var(--ink3)" }, children: "No active task yet. Start from a linked repo to let Dhee compile state." })) }), _jsxs(Panel, { label: "NEXT ACTION", children: [_jsx("div", { style: { fontSize: 18, fontWeight: 650, lineHeight: 1.35 }, children: String(get(data, "next_action", "Start a routed agent task")) }), _jsxs("div", { style: { marginTop: 14, display: "flex", gap: 8, flexWrap: "wrap" }, children: [_jsx("button", { onClick: () => onNavigate("handoff"), style: buttonStyle, children: "handoff" }), _jsx("button", { onClick: () => onNavigate("router"), style: ghostButtonStyle, children: "firewall" }), _jsx("button", { onClick: () => onNavigate("learnings"), style: ghostButtonStyle, children: "learnings" })] })] })] }), _jsxs("div", { className: "product-grid product-grid--three", children: [_jsx(Panel, { label: "LIVE SESSIONS", children: _jsx(RowList, { rows: sessions.slice(0, 5), empty: "No routed sessions yet.", render: (row) => (_jsx(SmallRow, { title: String(row.title || row.session_id || "session"), meta: `${row.agent || row.runtime || "agent"} - ${compact(row.tokens_saved)} tokens`, tone: toneFor(row.state) }, String(row.session_id))) }) }), _jsxs(Panel, { label: "REVIEW QUEUE", children: [_jsx(SmallRow, { title: "proposals", meta: compact(get(inboxTotals, "proposals", 0)), tone: "var(--accent)" }), _jsx(SmallRow, { title: "findings", meta: compact(get(inboxTotals, "findings", 0)), tone: "var(--rose)" }), _jsx(SmallRow, { title: "conflicts", meta: compact(get(inboxTotals, "conflicts", 0)), tone: "var(--indigo)" })] }), _jsx(Panel, { label: "ADDRESSABLE CONTEXT", children: aliases.length ? (aliases.map((alias) => (_jsx("div", { style: { fontFamily: "var(--mono)", fontSize: 11, padding: "5px 0", color: "var(--ink2)" }, children: alias }, alias)))) : (_jsx("div", { className: "product-empty", children: "No dhee:// aliases exposed yet." })) })] })] })) : null] }));
}
export function HandoffHubView() {
    const { data, error, loading, refresh } = useScreenData(api.handoffUi);
    const continuity = get(data, "continuity", {});
    const last = get(continuity, "last_session", {}) || {};
    const tasks = asRows(get(data, "tasks", []));
    const sessions = asRows(get(data, "sessions", []));
    const files = asRows(get(last, "files_touched", get(last, "filesTouched", [])));
    const decisions = asRows(get(last, "decisions", []));
    const todos = asRows(get(last, "todos", []));
    return (_jsxs(Screen, { eyebrow: "HANDOFF HUB", title: "Resume without replaying the transcript.", subtitle: "Dhee turns the latest work into task state: decisions, files, blockers, commands, tests, resume confidence, and the next step.", action: _jsx("button", { onClick: refresh, style: buttonStyle, children: "refresh" }), children: [_jsx(LoadingState, { loading: loading, error: error }), data ? (_jsxs("div", { className: "product-grid product-grid--two", children: [_jsxs(Panel, { label: "LATEST HANDOFF", children: [_jsx("div", { style: { fontSize: 24, lineHeight: 1.15, fontWeight: 700 }, children: String(get(last, "task_summary", "No handoff saved yet")) }), _jsxs("div", { style: { marginTop: 12, display: "flex", gap: 8, flexWrap: "wrap" }, children: [_jsxs(Pill, { tone: "var(--green)", children: ["confidence ", Math.round(Number(get(data, "resume_confidence", 0)) * 100), "%"] }), _jsx(Pill, { children: timeLabel(get(last, "updated") || get(last, "ended_at")) }), _jsx(Pill, { children: String(get(last, "agent_id", get(last, "source", "dhee"))) })] }), _jsx("pre", { style: preStyle, children: String(get(data, "command", "")) })] }), _jsx(Panel, { label: "RESUME INVENTORY", children: _jsx(MetricStack, { rows: [
                                ["tasks", tasks.length],
                                ["sessions", sessions.length],
                                ["files", files.length],
                                ["decisions", decisions.length],
                                ["todos", todos.length],
                            ] }) }), _jsx(Panel, { label: "DECISIONS", style: { gridColumn: "span 1" }, children: _jsx(TextList, { rows: decisions, empty: "No decisions captured yet." }) }), _jsx(Panel, { label: "FILES TOUCHED", children: _jsx(TextList, { rows: files.map((path) => shortPath(String(path))), empty: "No files in the latest handoff." }) })] })) : null] }));
}
export function ProofReplayView() {
    const { data, error, loading, refresh } = useScreenData(() => api.proofReplay(120));
    const rows = asRows(get(data, "items", []));
    const totals = get(data, "totals", {});
    return (_jsxs(Screen, { eyebrow: "PROOF REPLAY", title: "Replay the context decisions, not just the chat.", subtitle: "See the expansion trace: what Dhee digested, hid, expanded, injected, promoted, rejected, or derived from local records.", action: _jsx("button", { onClick: refresh, style: buttonStyle, children: "refresh" }), children: [_jsx(LoadingState, { loading: loading, error: error }), _jsxs("div", { className: "product-metric-grid", children: [_jsx(Metric, { label: "events", value: compact(get(totals, "events", rows.length)) }), _jsx(Metric, { label: "digests", value: compact(get(totals, "digests", 0)), tone: "var(--green)" }), _jsx(Metric, { label: "expansion trace", value: compact(get(totals, "expansions", 0)), tone: "var(--accent)" }), _jsx(Metric, { label: "evidence", value: compact(get(totals, "evidence", 0)), tone: "var(--indigo)" }), _jsx(Metric, { label: "derived rows", value: compact(get(totals, "derived", 0)) })] }), _jsx(Panel, { label: "DECISION TIMELINE", children: _jsx(RowList, { rows: rows, empty: "No context decisions recorded yet.", render: (row, index) => (_jsx(TimelineRow, { index: index, title: String(row.title || "Decision"), meta: `${row.source || "dhee"} - ${timeLabel(row.time)}`, detail: String(row.detail || ""), kind: String(row.kind || "event"), derived: Boolean(row.derived) }, String(row.id || index))) }) })] }));
}
export function LearningInboxView() {
    const { data, error, loading, refresh } = useScreenData(() => api.learningsUi(160));
    const [busy, setBusy] = useState("");
    const rows = asRows(get(data, "items", []));
    const totals = get(data, "totals", {});
    const act = async (id, action) => {
        setBusy(id);
        try {
            if (action === "promote")
                await api.promoteLearning(id, { approved_by: "dhee-ui" });
            else
                await api.rejectLearning(id, { reason: "rejected in Dhee UI" });
            await refresh();
        }
        finally {
            setBusy("");
        }
    };
    return (_jsxs(Screen, { eyebrow: "LEARNING INBOX", title: "Only evidence-backed learnings get promoted.", subtitle: "Clear pending review candidates from agent work. Dhee should learn from success, avoided failure, repeated utility, or explicit approval.", action: _jsx("button", { onClick: refresh, style: buttonStyle, children: "refresh" }), children: [_jsx(LoadingState, { loading: loading, error: error }), _jsxs("div", { className: "product-metric-grid", children: [_jsx(Metric, { label: "candidates", value: compact(get(totals, "candidate", 0)), tone: "var(--accent)" }), _jsx(Metric, { label: "promoted", value: compact(get(totals, "promoted", 0)), tone: "var(--green)" }), _jsx(Metric, { label: "rejected", value: compact(get(totals, "rejected", 0)), tone: "var(--rose)" }), _jsx(Metric, { label: "all learnings", value: compact(get(totals, "all", rows.length)) })] }), _jsx(Panel, { label: "LEARNING REVIEW", children: _jsx(RowList, { rows: rows, empty: "No learning candidates yet.", render: (row) => {
                        const id = String(row.id || "");
                        const status = String(row.status || "candidate");
                        const preview = learningPreview(row);
                        const source = String(row.source_harness || row.source_agent_id || "agent");
                        const sourceModel = String(row.source_model || "");
                        return (_jsxs("div", { className: "product-learning-row", children: [_jsxs("div", { style: { minWidth: 0 }, children: [_jsxs("div", { style: { display: "flex", gap: 8, flexWrap: "wrap", marginBottom: 8 }, children: [_jsx(Pill, { tone: toneFor(status), children: status }), _jsx(Pill, { children: String(row.evidence_gate || "needs approval") }), row.needs_distillation ? _jsx(Pill, { tone: "var(--rose)", children: "needs distillation" }) : null, _jsx(Pill, { children: source }), sourceModel ? _jsx(Pill, { children: sourceModel }) : null] }), _jsx("div", { className: "product-learning-title", children: String(row.title || id) }), _jsx("div", { className: "product-learning-meta", children: learningMeta(row) }), _jsx("div", { className: "product-learning-body", title: preview, children: preview })] }), _jsxs("div", { className: "product-learning-actions", children: [_jsx("button", { "aria-label": `Promote ${id || "learning"}`, disabled: !id || busy === id || status === "promoted", onClick: () => act(id, "promote"), style: buttonStyle, children: "promote" }), _jsx("button", { "aria-label": `Reject ${id || "learning"}`, disabled: !id || busy === id || status === "rejected", onClick: () => act(id, "reject"), style: ghostButtonStyle, children: "reject" })] })] }, id));
                    } }) })] }));
}
export function PortabilityTrustView() {
    const { data, error, loading, refresh } = useScreenData(api.portabilityUi);
    const [exporting, setExporting] = useState(false);
    const [packPath, setPackPath] = useState("");
    const [dryRun, setDryRun] = useState(null);
    const [actionError, setActionError] = useState("");
    const counts = get(data, "counts", {});
    const packs = asRows(get(data, "packs", []));
    const contract = (get(data, "contract", []) || []).filter(Boolean);
    const doExport = async () => {
        setExporting(true);
        setActionError("");
        try {
            await api.exportPackUi({});
            await refresh();
        }
        catch (e) {
            setActionError(String(e));
        }
        finally {
            setExporting(false);
        }
    };
    const doDryRun = async () => {
        setActionError("");
        setDryRun(null);
        try {
            setDryRun(await api.importPackDryRunUi({ input_path: packPath }));
        }
        catch (e) {
            setActionError(String(e));
        }
    };
    return (_jsxs(Screen, { eyebrow: "PORTABILITY & TRUST", title: "Local memory should be inspectable, signed, and movable.", subtitle: "Dhee keeps export/import as a product surface, not an afterthought. No lock-in tricks, no hidden hosted dependency.", action: _jsx("button", { onClick: refresh, style: buttonStyle, children: "refresh" }), children: [_jsx(LoadingState, { loading: loading, error: error }), _jsxs("div", { className: "product-metric-grid", children: [_jsx(Metric, { label: "memories", value: compact(get(counts, "memories", 0)) }), _jsx(Metric, { label: "artifacts", value: compact(get(counts, "artifacts", 0)), tone: "var(--indigo)" }), _jsx(Metric, { label: "repo context", value: compact(get(counts, "repo_context_entries", 0)), tone: "var(--green)" }), _jsx(Metric, { label: "packs found", value: compact(packs.length), tone: "var(--accent)" })] }), actionError ? _jsx(Panel, { children: _jsx("span", { style: { color: "var(--rose)" }, children: actionError }) }) : null, _jsxs("div", { className: "product-grid product-grid--split", children: [_jsxs(Panel, { label: "PORTABLE SUBSTRATE", children: [_jsx("div", { style: { display: "flex", gap: 8, flexWrap: "wrap" }, children: contract.map((item) => _jsx(Pill, { tone: "var(--green)", children: item }, item)) }), _jsx("button", { disabled: exporting, onClick: doExport, style: { ...buttonStyle, marginTop: 16 }, children: exporting ? "exporting..." : "export .dheemem" })] }), _jsxs(Panel, { label: "IMPORT DRY RUN", children: [_jsxs("div", { style: { display: "flex", gap: 10 }, children: [_jsx("input", { value: packPath, onChange: (e) => setPackPath(e.target.value), placeholder: "/path/to/backup.dheemem", style: inputStyle }), _jsx("button", { disabled: !packPath.trim(), onClick: doDryRun, style: buttonStyle, children: "dry run" })] }), dryRun ? _jsx("pre", { style: preStyle, children: JSON.stringify(get(dryRun, "result", dryRun), null, 2) }) : null] })] }), _jsx(Panel, { label: "RECENT PACKS", children: _jsx(RowList, { rows: packs, empty: "No .dheemem packs found yet.", render: (row) => (_jsx(SmallRow, { title: String(row.name || row.path), meta: `${row.verified ? "verified" : "unverified"} - ${compact(Number(row.size_bytes || 0))} bytes - ${timeLabel(row.updated_at)}`, tone: row.verified ? "var(--green)" : "var(--accent)" }, String(row.path))) }) })] }));
}
export function RepoBrainHeader({ onOpenContext }) {
    return (_jsxs("div", { className: "repo-brain-header", children: [_jsx(Pill, { tone: "var(--green)", children: "REPO BRAIN" }), _jsx(Pill, { children: "dhee://state/current" }), _jsx(Pill, { children: "dhee://handoff/latest" }), onOpenContext ? _jsx("button", { onClick: onOpenContext, style: ghostButtonStyle, children: "context vault" }) : null] }));
}
function TextList({ rows, empty }) {
    if (!rows.length)
        return _jsx("div", { className: "product-empty", children: empty });
    return (_jsx("div", { className: "product-list product-list--tight", children: rows.map((row, index) => (_jsx("div", { className: "product-text-row", children: String(row) }, index))) }));
}
function MetricStack({ rows }) {
    return (_jsx("div", { style: { display: "grid", gap: 8 }, children: rows.map(([label, value]) => (_jsxs("div", { style: { display: "flex", justifyContent: "space-between", gap: 20 }, children: [_jsx("span", { style: { color: "var(--ink3)" }, children: label }), _jsx("strong", { children: compact(value) })] }, label))) }));
}
function SmallRow({ title, meta, tone }) {
    return (_jsxs("div", { className: "product-small-row", children: [_jsx("span", { className: "product-row-dot", style: { background: tone || "var(--ink3)" } }), _jsxs("div", { style: { minWidth: 0 }, children: [_jsx("div", { className: "product-small-title", children: title }), _jsx("div", { className: "product-small-meta", children: meta })] })] }));
}
function TimelineRow({ index, title, meta, detail, kind, derived, }) {
    return (_jsxs("div", { className: "product-timeline-row", children: [_jsx("div", { className: "product-timeline-index", children: String(index + 1).padStart(2, "0") }), _jsxs("div", { children: [_jsxs("div", { style: { display: "flex", gap: 8, flexWrap: "wrap", marginBottom: 6 }, children: [_jsx(Pill, { tone: toneFor(kind), children: kind }), derived ? _jsx(Pill, { tone: "var(--accent)", children: "derived" }) : _jsx(Pill, { tone: "var(--green)", children: "recorded" }), _jsx(Pill, { children: meta })] }), _jsx("div", { className: "product-timeline-title", children: title }), detail ? _jsx("div", { className: "product-timeline-detail", children: detail }) : null] })] }));
}
const buttonStyle = {
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
const ghostButtonStyle = {
    ...buttonStyle,
    color: "var(--ink)",
    background: "white",
    borderColor: "var(--border2)",
};
const inputStyle = {
    minHeight: 36,
    flex: 1,
    border: "1px solid var(--border2)",
    background: "white",
    padding: "0 10px",
    fontFamily: "var(--mono)",
    fontSize: 11,
};
const preStyle = {
    marginTop: 14,
    border: "1px solid var(--border)",
    background: "var(--surface2)",
    padding: 12,
    fontFamily: "var(--mono)",
    fontSize: 11,
    whiteSpace: "pre-wrap",
    overflowX: "auto",
};
