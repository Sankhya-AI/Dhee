import { jsx as _jsx, jsxs as _jsxs } from "react/jsx-runtime";
import { useEffect, useMemo, useState } from "react";
import { api } from "../api";
import { TierBadge } from "../components/ui/TierBadge";
const sevColor = {
    high: "var(--rose)",
    medium: "var(--accent)",
    low: "var(--indigo)",
};
const ACTIONS = [
    { id: "KEEP A", label: "keep a" },
    { id: "KEEP B", label: "keep b" },
    { id: "MERGE", label: "merge" },
    { id: "ARCHIVE BOTH", label: "archive both" },
];
const EMPTY_SNAPSHOT = {
    live: false,
    supported: false,
    resolutionMode: "unavailable",
    conflicts: [],
};
export function ConflictView() {
    const [snapshot, setSnapshot] = useState(EMPTY_SNAPSHOT);
    const [selected, setSelected] = useState(null);
    const [resolved, setResolved] = useState({});
    const [busyAction, setBusyAction] = useState(null);
    const [mergeContent, setMergeContent] = useState("");
    const [resolutionReason, setResolutionReason] = useState("");
    const [error, setError] = useState(null);
    const loadConflicts = async () => {
        try {
            const response = await api.conflicts();
            setSnapshot(response);
            setSelected((current) => {
                if (!current)
                    return response.conflicts?.[0]?.id || null;
                return response.conflicts.some((item) => item.id === current) ? current : response.conflicts?.[0]?.id || null;
            });
        }
        catch (exc) {
            setError(exc instanceof Error ? exc.message : String(exc));
        }
    };
    useEffect(() => {
        (async () => {
            await loadConflicts();
        })();
    }, []);
    const conflicts = snapshot.conflicts || [];
    const active = selected ? conflicts.find((c) => c.id === selected) : null;
    const unresolvedCount = useMemo(() => conflicts.filter((c) => !resolved[c.id]).length, [conflicts, resolved]);
    const canResolve = snapshot.supported && snapshot.resolutionMode === "native";
    const resolve = async (id, action) => {
        if (!canResolve)
            return;
        if (action === "MERGE" && !mergeContent.trim()) {
            setError("Merged content is required before saving a merge.");
            return;
        }
        setBusyAction(action);
        setError(null);
        try {
            await api.resolveConflictDetailed(id, {
                action,
                merged_content: action === "MERGE" ? mergeContent.trim() : undefined,
                reason: resolutionReason.trim() || undefined,
            });
            setResolved((current) => ({ ...current, [id]: action }));
            setMergeContent("");
            setResolutionReason("");
            await loadConflicts();
        }
        catch (exc) {
            setError(exc instanceof Error ? exc.message : String(exc));
        }
        finally {
            setBusyAction(null);
        }
    };
    return (_jsxs("div", { style: { display: "flex", height: "100%" }, children: [_jsxs("div", { style: {
                    width: 320,
                    borderRight: "1px solid var(--border)",
                    display: "flex",
                    flexDirection: "column",
                    flexShrink: 0,
                    background: "white",
                }, children: [_jsxs("div", { style: {
                            borderBottom: "1px solid var(--border)",
                            padding: "0 16px",
                            height: 48,
                            display: "flex",
                            alignItems: "center",
                            gap: 8,
                        }, children: [_jsx("span", { style: {
                                    fontFamily: "var(--mono)",
                                    fontSize: 11,
                                    fontWeight: 700,
                                }, children: "CONFLICTS" }), unresolvedCount > 0 && (_jsxs("span", { style: {
                                    fontFamily: "var(--mono)",
                                    fontSize: 9,
                                    color: "var(--rose)",
                                    padding: "1px 6px",
                                    border: "1px solid var(--rose)",
                                }, children: [unresolvedCount, " open"] })), _jsx("span", { style: {
                                    marginLeft: "auto",
                                    fontFamily: "var(--mono)",
                                    fontSize: 9,
                                    color: snapshot.live ? "var(--green)" : "var(--ink3)",
                                }, title: snapshot.live ? "live conflict adapter" : "no live adapter", children: snapshot.live ? snapshot.resolutionMode : "offline" })] }), _jsxs("div", { style: { flex: 1, overflowY: "auto" }, children: [conflicts.length === 0 && (_jsx("div", { style: {
                                    padding: "20px",
                                    fontFamily: "var(--mono)",
                                    fontSize: 11,
                                    color: "var(--ink3)",
                                }, children: "No conflicts detected." })), conflicts.map((conflict) => {
                                const color = sevColor[conflict.severity] || "var(--ink3)";
                                const isResolved = Boolean(resolved[conflict.id]);
                                return (_jsxs("div", { onClick: () => {
                                        if (isResolved)
                                            return;
                                        setSelected(selected === conflict.id ? null : conflict.id);
                                        setError(null);
                                    }, style: {
                                        padding: "14px 16px",
                                        borderBottom: "1px solid var(--border)",
                                        cursor: isResolved ? "default" : "pointer",
                                        borderLeft: `3px solid ${isResolved ? "var(--border)" : color}`,
                                        background: selected === conflict.id ? "var(--surface)" : "transparent",
                                        opacity: isResolved ? 0.55 : 1,
                                        transition: "all 0.1s",
                                    }, children: [_jsxs("div", { style: {
                                                display: "flex",
                                                justifyContent: "space-between",
                                                marginBottom: 5,
                                            }, children: [_jsx("span", { style: {
                                                        fontFamily: "var(--mono)",
                                                        fontSize: 9,
                                                        color,
                                                        textTransform: "uppercase",
                                                        letterSpacing: "0.06em",
                                                    }, children: conflict.severity }), isResolved && (_jsxs("span", { style: {
                                                        fontFamily: "var(--mono)",
                                                        fontSize: 9,
                                                        color: "var(--green)",
                                                    }, children: ["\u2713 ", resolved[conflict.id]] }))] }), _jsx("div", { style: {
                                                fontSize: 12.5,
                                                lineHeight: 1.45,
                                                marginBottom: 5,
                                            }, children: conflict.reason }), _jsxs("div", { style: {
                                                fontFamily: "var(--mono)",
                                                fontSize: 9,
                                                color: "var(--ink3)",
                                            }, children: [conflict.belief_a.source, " \u2194 ", conflict.belief_b.source] })] }, conflict.id));
                            })] })] }), _jsxs("div", { style: {
                    flex: 1,
                    display: "flex",
                    flexDirection: "column",
                    overflow: "hidden",
                    background: "var(--bg)",
                }, children: [_jsxs("div", { style: {
                            padding: "16px 24px",
                            borderBottom: "1px solid var(--border)",
                            background: "white",
                        }, children: [_jsx("div", { style: {
                                    fontFamily: "var(--mono)",
                                    fontSize: 10,
                                    color: "var(--ink3)",
                                    marginBottom: 6,
                                }, children: "CONFLICT RESOLUTION" }), !canResolve ? (_jsx("div", { style: {
                                    border: "1px solid var(--accent)",
                                    background: "rgba(224, 107, 63, 0.05)",
                                    color: "var(--ink2)",
                                    padding: "10px 12px",
                                    fontSize: 12,
                                    lineHeight: 1.5,
                                }, children: "This runtime can surface conflicts, but it cannot persist manual resolutions yet. The screen is read-only until Dhee exposes a native conflict resolver for the active memory backend." })) : (_jsx("div", { style: {
                                    border: "1px solid var(--green)",
                                    background: "rgba(29, 128, 52, 0.05)",
                                    color: "var(--ink2)",
                                    padding: "10px 12px",
                                    fontSize: 12,
                                    lineHeight: 1.5,
                                }, children: "Native conflict resolution is available in this runtime. Actions below will be written back to the underlying Dhee memory backend." })), error && (_jsx("div", { style: {
                                    marginTop: 10,
                                    border: "1px solid var(--rose)",
                                    background: "rgba(200, 54, 86, 0.06)",
                                    color: "var(--rose)",
                                    padding: "10px 12px",
                                    fontFamily: "var(--mono)",
                                    fontSize: 10,
                                    lineHeight: 1.5,
                                    whiteSpace: "pre-wrap",
                                }, children: error }))] }), !active ? (_jsxs("div", { style: {
                            flex: 1,
                            display: "flex",
                            flexDirection: "column",
                            alignItems: "center",
                            justifyContent: "center",
                            gap: 8,
                        }, children: [_jsx("div", { style: {
                                    fontFamily: "var(--mono)",
                                    fontSize: 11,
                                    color: "var(--ink3)",
                                }, children: "SELECT A CONFLICT TO REVIEW" }), Object.keys(resolved).length > 0 && (_jsxs("div", { style: { fontSize: 12, color: "var(--green)" }, children: ["\u2713 ", Object.keys(resolved).length, " resolved in this session"] })), unresolvedCount === 0 && conflicts.length > 0 && (_jsx("div", { style: {
                                    fontSize: 13,
                                    color: "var(--green)",
                                    marginTop: 4,
                                }, children: "All conflicts resolved" }))] })) : (_jsxs("div", { style: { flex: 1, overflowY: "auto", padding: "24px" }, children: [_jsxs("div", { style: {
                                    fontFamily: "var(--mono)",
                                    fontSize: 9,
                                    color: "var(--ink3)",
                                    letterSpacing: "0.06em",
                                    marginBottom: 18,
                                    textTransform: "uppercase",
                                }, children: ["Conflict \u00B7 ", active.reason] }), _jsx("div", { style: {
                                    display: "grid",
                                    gridTemplateColumns: "1fr 40px 1fr",
                                    gap: 0,
                                    marginBottom: 22,
                                }, children: [active.belief_a, active.belief_b].map((belief, index) => (_jsxs("span", { style: { display: "contents" }, children: [_jsxs("div", { style: {
                                                border: "1px solid var(--border)",
                                                background: "white",
                                                padding: 18,
                                            }, children: [_jsxs("div", { style: {
                                                        display: "flex",
                                                        justifyContent: "space-between",
                                                        gap: 12,
                                                        marginBottom: 12,
                                                        alignItems: "center",
                                                    }, children: [_jsx(TierBadge, { tier: belief.tier }), _jsxs("span", { style: {
                                                                fontFamily: "var(--mono)",
                                                                fontSize: 9,
                                                                color: "var(--ink3)",
                                                            }, children: [Math.round((belief.confidence || 0) * 100), "%"] })] }), _jsx("div", { style: {
                                                        fontSize: 14,
                                                        lineHeight: 1.7,
                                                        marginBottom: 16,
                                                        whiteSpace: "pre-wrap",
                                                    }, children: belief.content }), (belief.evidence || []).length > 0 && (_jsxs("div", { style: { marginBottom: 12 }, children: [_jsx("div", { style: {
                                                                fontFamily: "var(--mono)",
                                                                fontSize: 9,
                                                                color: "var(--ink3)",
                                                                marginBottom: 6,
                                                            }, children: "EVIDENCE" }), (belief.evidence || []).slice(0, 3).map((item) => (_jsx("div", { style: {
                                                                fontSize: 11,
                                                                color: "var(--ink2)",
                                                                lineHeight: 1.5,
                                                                marginBottom: 6,
                                                            }, children: item.content }, item.id || `${belief.id}:${item.content.slice(0, 12)}`)))] })), _jsxs("div", { style: {
                                                        fontFamily: "var(--mono)",
                                                        fontSize: 9,
                                                        color: "var(--ink3)",
                                                        display: "flex",
                                                        flexDirection: "column",
                                                        gap: 4,
                                                    }, children: [_jsxs("span", { children: ["source \u00B7 ", belief.source] }), belief.domain && _jsxs("span", { children: ["domain \u00B7 ", belief.domain] }), belief.truthStatus && _jsxs("span", { children: ["truth \u00B7 ", belief.truthStatus] }), belief.freshness && _jsxs("span", { children: ["freshness \u00B7 ", belief.freshness] }), _jsxs("span", { children: ["created \u00B7 ", belief.created] })] })] }), index === 0 && (_jsx("div", { style: {
                                                display: "flex",
                                                alignItems: "center",
                                                justifyContent: "center",
                                                fontFamily: "var(--mono)",
                                                fontSize: 10,
                                                color: "var(--ink3)",
                                            }, children: "\u2194" }))] }, belief.id))) }), _jsxs("div", { style: { marginBottom: 18 }, children: [_jsx("div", { style: {
                                            fontFamily: "var(--mono)",
                                            fontSize: 10,
                                            color: "var(--ink3)",
                                            marginBottom: 8,
                                        }, children: "RESOLUTION NOTES" }), _jsx("textarea", { value: resolutionReason, onChange: (e) => setResolutionReason(e.target.value), placeholder: "Why are you choosing this resolution?", rows: 2, style: {
                                            width: "100%",
                                            border: "1px solid var(--border)",
                                            background: "white",
                                            padding: "10px 12px",
                                            fontSize: 13,
                                            lineHeight: 1.5,
                                            marginBottom: 10,
                                        } }), _jsx("textarea", { value: mergeContent, onChange: (e) => setMergeContent(e.target.value), placeholder: "For MERGE, write the canonical merged belief Dhee should keep.", rows: 4, style: {
                                            width: "100%",
                                            border: "1px solid var(--border)",
                                            background: "white",
                                            padding: "10px 12px",
                                            fontSize: 13,
                                            lineHeight: 1.6,
                                        } })] }), _jsx("div", { style: { display: "flex", gap: 10, flexWrap: "wrap" }, children: ACTIONS.map((action) => (_jsx("button", { onClick: () => void resolve(active.id, action.id), disabled: !canResolve || busyAction !== null, style: {
                                        padding: "10px 12px",
                                        border: "1px solid var(--border)",
                                        background: !canResolve
                                            ? "var(--surface)"
                                            : busyAction === action.id
                                                ? "var(--ink)"
                                                : "white",
                                        color: !canResolve
                                            ? "var(--ink3)"
                                            : busyAction === action.id
                                                ? "white"
                                                : "var(--ink)",
                                        fontFamily: "var(--mono)",
                                        fontSize: 10,
                                        cursor: !canResolve || busyAction !== null ? "not-allowed" : "pointer",
                                        opacity: !canResolve ? 0.75 : 1,
                                    }, children: busyAction === action.id ? "saving..." : action.label }, action.id))) }), _jsx("div", { style: { marginTop: 22, display: "grid", gap: 18 }, children: [
                                    { label: "belief a history", belief: active.belief_a },
                                    { label: "belief b history", belief: active.belief_b },
                                ].map((entry) => (_jsxs("div", { style: { border: "1px solid var(--border)", background: "white", padding: 14 }, children: [_jsx("div", { style: {
                                                fontFamily: "var(--mono)",
                                                fontSize: 10,
                                                color: "var(--ink3)",
                                                marginBottom: 10,
                                            }, children: entry.label }), (entry.belief.history || []).length === 0 && (_jsx("div", { style: { fontSize: 11, color: "var(--ink3)" }, children: "No history recorded yet." })), (entry.belief.history || []).slice(0, 6).map((item, index) => (_jsxs("div", { style: { marginBottom: 8 }, children: [_jsx("div", { style: { fontSize: 12, fontWeight: 600 }, children: item.event_type || "event" }), _jsx("div", { style: { fontSize: 11, color: "var(--ink2)", lineHeight: 1.5 }, children: item.reason || "No reason recorded." }), _jsxs("div", { style: { fontFamily: "var(--mono)", fontSize: 9, color: "var(--ink3)", marginTop: 2 }, children: [item.actor || "system", " \u00B7 ", item.created_at || "—"] })] }, `${entry.belief.id}:${index}`)))] }, entry.label))) })] }))] })] }));
}
