import { jsx as _jsx, jsxs as _jsxs, Fragment as _Fragment } from "react/jsx-runtime";
import { useEffect, useRef, useState } from "react";
import { api } from "../api";
function formatCompact(n) {
    if (!n || n <= 0)
        return "0";
    return new Intl.NumberFormat("en", {
        notation: "compact",
        maximumFractionDigits: 1,
    }).format(n);
}
function tokensSavedTotal(stats) {
    if (!stats)
        return 0;
    const session = Number(stats.sessionTokensSaved || 0);
    const enterprise = Number(stats.enterpriseSavedTokens || 0);
    return session + enterprise;
}
function savedPct(stats) {
    if (!stats)
        return 0;
    const ent = Number(stats.enterpriseSavedPct || 0);
    return ent;
}
export function TopBar({ viewer, routerStats, onRefresh, onOpenTweaks, onResetWorkspace, }) {
    const [menuOpen, setMenuOpen] = useState(false);
    const [fallbackStats, setFallbackStats] = useState(null);
    const menuRef = useRef(null);
    useEffect(() => {
        if (!menuOpen)
            return;
        const onClick = (e) => {
            if (!menuRef.current)
                return;
            if (!menuRef.current.contains(e.target))
                setMenuOpen(false);
        };
        window.addEventListener("mousedown", onClick);
        return () => window.removeEventListener("mousedown", onClick);
    }, [menuOpen]);
    useEffect(() => {
        if (routerStats) {
            setFallbackStats(null);
            return;
        }
        let cancelled = false;
        const load = async () => {
            try {
                const stats = await api.routerStats();
                if (!cancelled)
                    setFallbackStats(stats);
            }
            catch { }
        };
        void load();
        const timer = window.setInterval(load, 5000);
        return () => {
            cancelled = true;
            window.clearInterval(timer);
        };
    }, [routerStats]);
    const orgLabel = viewer?.org_id || "default";
    const projectLabel = viewer?.project_id || null;
    const teamLabel = viewer?.team_id || null;
    const breadcrumb = [orgLabel, projectLabel, teamLabel]
        .filter(Boolean)
        .join(" · ");
    const effectiveStats = routerStats || fallbackStats;
    const totalSaved = tokensSavedTotal(effectiveStats);
    const pct = savedPct(effectiveStats);
    const tooltip = (() => {
        if (!effectiveStats)
            return "loading";
        const session = Number(effectiveStats.sessionTokensSaved || 0);
        const ent = Number(effectiveStats.enterpriseSavedTokens || 0);
        const raw = Number(effectiveStats.enterpriseRawTokens || 0);
        const summary = Number(effectiveStats.enterpriseSummaryTokens || 0);
        const fallbacks = Number(effectiveStats.enterpriseRawFallbacks || 0);
        const gates = Number(effectiveStats.enterpriseGateSuggestions || 0);
        return `Session: ${formatCompact(session)} · Repo index: ${formatCompact(ent)} · Raw avoided: ${formatCompact(raw)} -> ${formatCompact(summary)} · Fallbacks: ${fallbacks} · Gates: ${gates}`;
    })();
    return (_jsxs("div", { style: {
            height: 32,
            borderBottom: "1px solid var(--border)",
            background: "var(--bg)",
            display: "flex",
            alignItems: "center",
            padding: "0 12px",
            gap: 10,
            flexShrink: 0,
            zIndex: 15,
        }, children: [_jsxs("div", { className: "workspace-pill", title: breadcrumb, style: {
                    display: "inline-flex",
                    alignItems: "center",
                    gap: 6,
                    padding: "3px 9px",
                    borderRadius: 4,
                    background: "var(--surface)",
                    border: "1px solid var(--border)",
                    fontFamily: "var(--mono)",
                    fontSize: 10,
                    color: "var(--ink2)",
                    letterSpacing: "0.04em",
                }, children: [_jsx("span", { style: {
                            width: 5,
                            height: 5,
                            borderRadius: "50%",
                            background: viewer?.live ? "var(--green)" : "var(--ink3)",
                        } }), _jsx("span", { children: breadcrumb || "no workspace" }), viewer?.role ? (_jsx("span", { style: {
                            marginLeft: 6,
                            padding: "1px 5px",
                            borderRadius: 3,
                            background: "var(--surface2)",
                            color: "var(--ink2)",
                            fontSize: 9,
                        }, children: String(viewer.role).toUpperCase() })) : null] }), _jsx("div", { style: { flex: 1 } }), _jsxs("div", { className: "tokens-chip", title: tooltip, style: {
                    display: "inline-flex",
                    alignItems: "center",
                    gap: 6,
                    padding: "3px 9px",
                    borderRadius: 4,
                    background: "var(--accent-dim)",
                    border: "1px solid var(--accent)",
                    color: "var(--accent)",
                    fontFamily: "var(--mono)",
                    fontSize: 10,
                    letterSpacing: "0.04em",
                }, children: [_jsx("span", { style: { fontSize: 11 }, children: "\u21AF" }), _jsxs("span", { children: [formatCompact(totalSaved), " saved"] }), pct > 0 ? (_jsxs("span", { style: { color: "var(--ink3)" }, children: ["\u00B7 ", pct.toFixed(0), "%"] })) : null] }), _jsxs("div", { ref: menuRef, style: { position: "relative", display: "inline-block" }, children: [_jsx("button", { "aria-label": "Menu", onClick: () => setMenuOpen((v) => !v), style: {
                            width: 22,
                            height: 22,
                            borderRadius: 4,
                            background: menuOpen ? "var(--surface2)" : "var(--surface)",
                            border: "1px solid var(--border)",
                            color: "var(--ink2)",
                            fontSize: 12,
                            lineHeight: 1,
                            display: "inline-flex",
                            alignItems: "center",
                            justifyContent: "center",
                        }, children: "\u22EE" }), menuOpen ? (_jsxs("div", { style: {
                            position: "absolute",
                            top: "calc(100% + 4px)",
                            right: 0,
                            minWidth: 180,
                            background: "var(--bg)",
                            border: "1px solid var(--border)",
                            borderRadius: 4,
                            boxShadow: "0 6px 18px rgba(20,16,10,0.08)",
                            zIndex: 30,
                            padding: 4,
                            fontFamily: "var(--mono)",
                            fontSize: 10,
                            letterSpacing: "0.04em",
                        }, children: [_jsx(MenuItem, { label: "REFRESH", onClick: () => {
                                    setMenuOpen(false);
                                    onRefresh();
                                } }), _jsx(MenuItem, { label: "TWEAKS", hint: "\u2318K", onClick: () => {
                                    setMenuOpen(false);
                                    onOpenTweaks();
                                } }), onResetWorkspace ? (_jsxs(_Fragment, { children: [_jsx("div", { style: {
                                            height: 1,
                                            background: "var(--border)",
                                            margin: "3px 0",
                                        } }), _jsx(MenuItem, { label: "RESET WORKSPACE", onClick: () => {
                                            setMenuOpen(false);
                                            onResetWorkspace();
                                        }, danger: true })] })) : null, _jsx("div", { style: {
                                    height: 1,
                                    background: "var(--border)",
                                    margin: "3px 0",
                                } }), _jsx(MenuItem, { label: "USER ID", hint: viewer?.user_id || "—", onClick: () => setMenuOpen(false), dim: true })] })) : null] })] }));
}
function MenuItem({ label, hint, onClick, dim, danger, }) {
    return (_jsxs("button", { onClick: onClick, style: {
            width: "100%",
            textAlign: "left",
            padding: "5px 8px",
            borderRadius: 3,
            background: "transparent",
            color: danger
                ? "var(--rose)"
                : dim
                    ? "var(--ink3)"
                    : "var(--ink2)",
            display: "flex",
            justifyContent: "space-between",
            alignItems: "center",
            gap: 8,
        }, onMouseEnter: (e) => {
            e.currentTarget.style.background = danger
                ? "var(--rose-dim)"
                : "var(--surface)";
        }, onMouseLeave: (e) => {
            e.currentTarget.style.background = "transparent";
        }, children: [_jsx("span", { children: label }), hint ? (_jsx("span", { style: { color: "var(--ink3)", fontSize: 9 }, children: hint })) : null] }));
}
