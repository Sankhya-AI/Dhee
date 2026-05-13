import { jsx as _jsx, jsxs as _jsxs } from "react/jsx-runtime";
import { useEffect, useState } from "react";
import { api } from "../api";
import { SectionHeader } from "../components/ui/SectionHeader";
const typeIcon = {
    tune: "◈",
    commit: "✓",
    rollback: "↺",
    nididhyasana: "≡",
    promotion: "↑",
};
const typeColor = {
    tune: "var(--accent)",
    commit: "var(--green)",
    rollback: "var(--rose)",
    nididhyasana: "var(--indigo)",
    promotion: "var(--green)",
};
const typeBg = {
    tune: "oklch(0.97 0.04 36)",
    commit: "oklch(0.96 0.06 145)",
    rollback: "oklch(0.97 0.04 10)",
    nididhyasana: "oklch(0.96 0.04 265)",
    promotion: "oklch(0.96 0.06 145)",
};
export function EvolutionView() {
    const [events, setEvents] = useState([]);
    const [eventsLive, setEventsLive] = useState(false);
    const [meta, setMeta] = useState(null);
    const [policies, setPolicies] = useState([]);
    const [selectedEvt, setSelectedEvt] = useState(null);
    useEffect(() => {
        (async () => {
            try {
                const [e, m, p] = await Promise.all([
                    api.evolution(),
                    api.metaBuddhi(),
                    api.routerPolicy(),
                ]);
                setEvents(e.events || []);
                setEventsLive(e.live);
                setMeta(m);
                setPolicies(p.policies || []);
            }
            catch { }
        })();
    }, []);
    return (_jsxs("div", { style: {
            display: "flex",
            flexDirection: "column",
            height: "100%",
            overflowY: "auto",
        }, children: [_jsxs("div", { style: {
                    borderBottom: "1px solid var(--border)",
                    padding: "0 24px",
                    height: 48,
                    display: "flex",
                    alignItems: "center",
                    gap: 10,
                    flexShrink: 0,
                }, children: [_jsx("span", { style: {
                            fontFamily: "var(--mono)",
                            fontSize: 11,
                            fontWeight: 700,
                            letterSpacing: "0.06em",
                        }, children: "EVOLUTION" }), _jsxs("div", { style: { display: "flex", alignItems: "center", gap: 5 }, children: [_jsx("div", { style: {
                                    width: 6,
                                    height: 6,
                                    borderRadius: "50%",
                                    background: meta?.status === "active" ? "var(--green)" : "var(--ink3)",
                                } }), _jsxs("span", { style: {
                                    fontFamily: "var(--mono)",
                                    fontSize: 10,
                                    color: meta?.status === "active" ? "var(--green)" : "var(--ink3)",
                                }, children: ["MetaBuddhi ", meta?.status ?? "unknown"] })] }), _jsxs("span", { style: {
                            fontFamily: "var(--mono)",
                            fontSize: 10,
                            color: "var(--ink3)",
                            marginLeft: 4,
                        }, children: ["\u00B7 strategy: ", meta?.strategy ?? "—"] })] }), _jsxs("div", { style: { padding: "24px", maxWidth: 900 }, children: [_jsxs("div", { style: {
                            display: "grid",
                            gridTemplateColumns: "1fr 1fr",
                            gap: 16,
                            marginBottom: 28,
                        }, children: [_jsxs("div", { style: {
                                    border: "1px solid var(--border)",
                                    padding: "18px",
                                    background: "white",
                                }, children: [_jsx("div", { style: {
                                            fontFamily: "var(--mono)",
                                            fontSize: 9,
                                            color: "var(--ink3)",
                                            letterSpacing: "0.08em",
                                            marginBottom: 14,
                                        }, children: "METABUDDHI \u2014 COGNITIVE ENGINE" }), _jsxs("div", { style: { display: "flex", gap: 20, marginBottom: 16 }, children: [_jsxs("div", { children: [_jsx("div", { style: {
                                                            fontFamily: "var(--mono)",
                                                            fontSize: 28,
                                                            fontWeight: 700,
                                                            color: "var(--ink)",
                                                            lineHeight: 1,
                                                        }, children: meta?.totalInsights ?? 0 }), _jsx("div", { style: {
                                                            fontSize: 11,
                                                            color: "var(--ink3)",
                                                            marginTop: 3,
                                                        }, children: "total insights" })] }), _jsxs("div", { children: [_jsx("div", { style: {
                                                            fontFamily: "var(--mono)",
                                                            fontSize: 28,
                                                            fontWeight: 700,
                                                            color: "var(--accent)",
                                                            lineHeight: 1,
                                                        }, children: meta?.sessionInsights ?? 0 }), _jsx("div", { style: {
                                                            fontSize: 11,
                                                            color: "var(--ink3)",
                                                            marginTop: 3,
                                                        }, children: "this session" })] }), meta && meta.pendingProposals > 0 && (_jsxs("div", { children: [_jsx("div", { style: {
                                                            fontFamily: "var(--mono)",
                                                            fontSize: 28,
                                                            fontWeight: 700,
                                                            color: "var(--indigo)",
                                                            lineHeight: 1,
                                                        }, children: meta.pendingProposals }), _jsx("div", { style: {
                                                            fontSize: 11,
                                                            color: "var(--ink3)",
                                                            marginTop: 3,
                                                        }, children: "pending" })] }))] }), _jsx("div", { style: {
                                            fontSize: 12,
                                            color: "var(--ink2)",
                                            marginBottom: 14,
                                        }, children: "Watches expansion events \u2192 self-tunes router policy. No config to maintain." }), _jsx("div", { style: {
                                            fontFamily: "var(--mono)",
                                            fontSize: 9,
                                            color: "var(--ink3)",
                                            marginBottom: 6,
                                        }, children: "CURRENT CYCLE" }), _jsxs("div", { style: { display: "flex", alignItems: "center", gap: 0 }, children: [["PROPOSE", "ASSESS", "COMMIT"].map((s, i) => (_jsxs("span", { style: { display: "flex", alignItems: "center" }, children: [_jsx("div", { style: {
                                                            padding: "5px 10px",
                                                            background: i === 1 ? "var(--indigo)" : "var(--surface2)",
                                                            color: i === 1 ? "white" : "var(--ink3)",
                                                            fontFamily: "var(--mono)",
                                                            fontSize: 9,
                                                            fontWeight: i === 1 ? 700 : 400,
                                                        }, children: s }), i < 2 && (_jsx("div", { style: {
                                                            width: 20,
                                                            height: 1,
                                                            background: i === 0 ? "var(--indigo)" : "var(--border)",
                                                        } }))] }, s))), _jsx("div", { style: {
                                                    width: 1,
                                                    height: 28,
                                                    background: "var(--border)",
                                                    margin: "0 0 0 8px",
                                                } }), _jsx("div", { style: {
                                                    padding: "5px 10px",
                                                    background: "var(--surface2)",
                                                    color: "var(--ink3)",
                                                    fontFamily: "var(--mono)",
                                                    fontSize: 9,
                                                    marginLeft: 8,
                                                }, children: "ROLLBACK" })] }), _jsx("div", { style: {
                                            marginTop: 10,
                                            fontFamily: "var(--mono)",
                                            fontSize: 9,
                                            color: "var(--ink3)",
                                        }, children: "Guardrail: single-group regression threshold \u22120.06" })] }), _jsxs("div", { style: {
                                    border: "1px solid var(--border)",
                                    padding: "18px",
                                    background: "white",
                                }, children: [_jsx("div", { style: {
                                            fontFamily: "var(--mono)",
                                            fontSize: 9,
                                            color: "var(--ink3)",
                                            letterSpacing: "0.08em",
                                            marginBottom: 14,
                                        }, children: "NIDIDHYASANA \u2014 TRAINING GATE" }), _jsx("div", { style: {
                                            fontSize: 13,
                                            color: "var(--ink)",
                                            lineHeight: 1.6,
                                            marginBottom: 14,
                                        }, children: "Gates strategy training at session boundaries. A candidate only promotes when it beats the incumbent by \u22650.02 on the held-out corpus." }), _jsxs("div", { style: {
                                            fontFamily: "var(--mono)",
                                            fontSize: 11,
                                            color: "var(--ink2)",
                                            marginBottom: 12,
                                        }, children: ["Last gate:", " ", _jsx("span", { style: { color: "var(--ink)" }, children: meta?.lastGate || "—" })] }), _jsx("div", { style: {
                                            fontFamily: "var(--mono)",
                                            fontSize: 9,
                                            color: "var(--ink3)",
                                            marginBottom: 8,
                                        }, children: "CONFIDENCE BY INTENT CLASS" }), (meta?.confidenceGroups || []).map((g) => (_jsxs("div", { style: {
                                            display: "flex",
                                            gap: 10,
                                            alignItems: "center",
                                            marginBottom: 6,
                                        }, children: [_jsx("span", { style: {
                                                    width: 88,
                                                    fontFamily: "var(--mono)",
                                                    fontSize: 10,
                                                    color: "var(--ink2)",
                                                }, children: g.group }), _jsx("div", { style: {
                                                    flex: 1,
                                                    height: 4,
                                                    background: "var(--surface2)",
                                                }, children: _jsx("div", { style: {
                                                        height: "100%",
                                                        width: `${g.confidence * 100}%`,
                                                        background: g.confidence > 0.8 ? "var(--green)" : "var(--accent)",
                                                    } }) }), _jsxs("span", { style: {
                                                    fontFamily: "var(--mono)",
                                                    fontSize: 9,
                                                    color: "var(--ink3)",
                                                    width: 32,
                                                }, children: [Math.round(g.confidence * 100), "%"] }), _jsx("span", { style: {
                                                    fontSize: 10,
                                                    color: g.trend === "up" ? "var(--green)" : "var(--ink3)",
                                                }, children: g.trend === "up" ? "↑" : "—" })] }, g.group)))] })] }), _jsxs("div", { style: { marginBottom: 28 }, children: [_jsx(SectionHeader, { label: "Evolution Timeline", sub: eventsLive ? "samskara log" : "no log yet" }), _jsxs("div", { style: { border: "1px solid var(--border)", background: "white" }, children: [events.length === 0 && (_jsx("div", { style: {
                                            padding: "20px",
                                            fontFamily: "var(--mono)",
                                            fontSize: 11,
                                            color: "var(--ink3)",
                                        }, children: "No evolution events recorded yet." })), events.map((ev, i) => {
                                        const ic = typeIcon[ev.type] || "·";
                                        const tc = typeColor[ev.type] || "var(--ink3)";
                                        const bg = typeBg[ev.type] || "transparent";
                                        const isSelected = selectedEvt === ev.id;
                                        return (_jsxs("div", { onClick: () => setSelectedEvt(isSelected ? null : ev.id), style: {
                                                display: "flex",
                                                gap: 14,
                                                padding: "12px 18px",
                                                borderBottom: i < events.length - 1
                                                    ? "1px solid var(--surface2)"
                                                    : "none",
                                                cursor: "pointer",
                                                background: isSelected ? bg : "transparent",
                                                transition: "background 0.12s",
                                            }, children: [_jsx("span", { style: {
                                                        fontFamily: "var(--mono)",
                                                        fontSize: 9,
                                                        color: "var(--ink3)",
                                                        width: 120,
                                                        flexShrink: 0,
                                                        paddingTop: 2,
                                                    }, children: ev.time }), _jsx("span", { style: {
                                                        width: 16,
                                                        textAlign: "center",
                                                        color: tc,
                                                        fontWeight: 700,
                                                        flexShrink: 0,
                                                    }, children: ic }), _jsxs("div", { style: { flex: 1 }, children: [_jsx("div", { style: {
                                                                fontSize: 13,
                                                                fontWeight: 500,
                                                                color: tc,
                                                                marginBottom: 3,
                                                            }, children: ev.label }), _jsx("div", { style: {
                                                                fontSize: 12,
                                                                color: "var(--ink3)",
                                                                lineHeight: 1.5,
                                                            }, children: ev.detail })] }), _jsx("div", { style: {
                                                        width: 7,
                                                        height: 7,
                                                        borderRadius: "50%",
                                                        background: ev.impact === "positive"
                                                            ? "var(--green)"
                                                            : ev.impact === "negative"
                                                                ? "var(--rose)"
                                                                : "var(--border2)",
                                                        marginTop: 5,
                                                        flexShrink: 0,
                                                    } })] }, ev.id));
                                    })] })] }), policies.length > 0 && (_jsxs("div", { children: [_jsx(SectionHeader, { label: "Intent Class \u00B7 Expansion Rate \u2192 Depth", sub: "orange = auto-tuned this session" }), _jsx("div", { style: {
                                    display: "grid",
                                    gridTemplateColumns: `repeat(${Math.min(6, policies.length)}, 1fr)`,
                                    gap: 10,
                                }, children: policies.slice(0, 6).map((p) => {
                                    const hi = p.expansionRate > 0.3;
                                    const lo = p.expansionRate < 0.05;
                                    return (_jsxs("div", { style: {
                                            border: `1.5px solid ${p.tuned ? "var(--accent)" : "var(--border)"}`,
                                            padding: "14px 10px",
                                            background: "white",
                                            textAlign: "center",
                                        }, children: [_jsx("div", { style: {
                                                    fontFamily: "var(--mono)",
                                                    fontSize: 9,
                                                    color: p.tuned ? "var(--accent)" : "var(--ink3)",
                                                    marginBottom: 10,
                                                    letterSpacing: "0.04em",
                                                }, children: p.label.toUpperCase() }), _jsx("div", { style: {
                                                    height: 64,
                                                    display: "flex",
                                                    alignItems: "flex-end",
                                                    justifyContent: "center",
                                                    gap: 3,
                                                    marginBottom: 10,
                                                }, children: [1, 2, 3].map((d) => (_jsx("div", { style: {
                                                        width: 10,
                                                        height: `${(d / 3) * 60}px`,
                                                        background: d <= p.depth
                                                            ? p.tuned
                                                                ? "var(--accent)"
                                                                : "var(--ink2)"
                                                            : "var(--surface2)",
                                                    } }, d))) }), _jsxs("div", { style: {
                                                    fontFamily: "var(--mono)",
                                                    fontSize: 14,
                                                    fontWeight: 700,
                                                    color: hi
                                                        ? "var(--rose)"
                                                        : lo
                                                            ? "var(--green)"
                                                            : "var(--ink)",
                                                    marginBottom: 2,
                                                }, children: [Math.round(p.expansionRate * 100), "%"] }), _jsxs("div", { style: {
                                                    fontFamily: "var(--mono)",
                                                    fontSize: 9,
                                                    color: "var(--ink3)",
                                                }, children: ["depth ", p.depth] }), p.tuned && p.depth !== p.prevDepth && (_jsxs("div", { style: {
                                                    fontFamily: "var(--mono)",
                                                    fontSize: 8,
                                                    color: "var(--accent)",
                                                    marginTop: 4,
                                                }, children: [p.prevDepth, "\u2192", p.depth] }))] }, `${p.tool}-${p.intent}`));
                                }) })] }))] })] }));
}
