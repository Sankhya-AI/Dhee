import { jsx as _jsx, jsxs as _jsxs } from "react/jsx-runtime";
import { useEffect, useMemo, useState } from "react";
import { api } from "../api";
import { DecayBar } from "../components/ui/DecayBar";
import { SectionHeader } from "../components/ui/SectionHeader";
import { TierBadge } from "../components/ui/TierBadge";
const retentionInfo = {
    canonical: "forever",
    high: "180 days",
    medium: "60 days",
    "short-term": "7 days",
    avoid: "never evict",
};
export function MemoryView({ onMemoryCountChange, }) {
    const [memories, setMemories] = useState([]);
    const [live, setLive] = useState(true);
    const [error, setError] = useState(null);
    const [loading, setLoading] = useState(true);
    const [selectedTier, setSelectedTier] = useState("all");
    const [search, setSearch] = useState("");
    const [expandedId, setExpandedId] = useState(null);
    const [addMode, setAddMode] = useState(false);
    const [newText, setNewText] = useState("");
    const [rechunking, setRechunking] = useState(null);
    const [memoryNow, setMemoryNow] = useState(null);
    const [timeline, setTimeline] = useState([]);
    const reload = async () => {
        setLoading(true);
        try {
            const [res, now, captureTimeline] = await Promise.all([
                api.listMemories(),
                api.memoryNow().catch(() => null),
                api.captureTimeline(18).catch(() => null),
            ]);
            setMemories(res.engrams || []);
            setLive(res.live);
            setError(res.error || null);
            onMemoryCountChange?.(res.engrams?.length || 0);
            setMemoryNow(now);
            setTimeline(captureTimeline?.items || []);
        }
        catch (e) {
            setError(String(e));
            setLive(false);
        }
        finally {
            setLoading(false);
        }
    };
    useEffect(() => {
        reload();
        const timer = window.setInterval(() => {
            void reload();
        }, 3500);
        return () => window.clearInterval(timer);
    }, []);
    const tierCounts = useMemo(() => {
        const c = {};
        memories.forEach((m) => {
            c[m.tier] = (c[m.tier] || 0) + 1;
        });
        return c;
    }, [memories]);
    const filtered = useMemo(() => {
        let m = memories;
        if (selectedTier !== "all")
            m = m.filter((x) => x.tier === selectedTier);
        if (search.trim())
            m = m.filter((x) => x.content.toLowerCase().includes(search.toLowerCase()) ||
                x.tags.some((t) => t.includes(search.toLowerCase())));
        return m;
    }, [memories, selectedTier, search]);
    const triggerRechunk = (id) => {
        setRechunking(id);
        setTimeout(() => setRechunking(null), 1800);
    };
    const saveMemory = async () => {
        if (!newText.trim())
            return;
        try {
            await api.remember(newText.trim(), "short-term", []);
            setNewText("");
            setAddMode(false);
            await reload();
        }
        catch (e) {
            setError(String(e));
        }
    };
    const archive = async (id) => {
        try {
            await api.archiveMemory(id);
            setMemories((m) => m.filter((x) => x.id !== id));
            onMemoryCountChange?.(memories.length - 1);
        }
        catch (e) {
            setError(String(e));
        }
    };
    const tierList = [
        { id: "all", label: "All memories" },
        { id: "canonical", label: "Canonical" },
        { id: "high", label: "High" },
        { id: "medium", label: "Medium" },
        { id: "short-term", label: "Short-term" },
        { id: "avoid", label: "Avoid" },
    ];
    const activeCapture = memoryNow?.activeCapture || [];
    const recentSessions = memoryNow?.sessions?.slice(0, 3) || [];
    const activeSurfaceCount = activeCapture.reduce((sum, entry) => sum + (entry.graph?.surfaces?.length || 0), 0);
    const recentCaptureItems = timeline
        .filter((item) => ["action", "observation", "artifact", "event"].includes(item.kind))
        .slice(0, 8);
    return (_jsxs("div", { style: { display: "flex", flexDirection: "column", height: "100%" }, children: [_jsxs("div", { style: {
                    borderBottom: "1px solid var(--border)",
                    padding: "0 20px",
                    height: 48,
                    display: "flex",
                    alignItems: "center",
                    gap: 12,
                    flexShrink: 0,
                }, children: [_jsx("span", { style: {
                            fontFamily: "var(--mono)",
                            fontSize: 11,
                            fontWeight: 700,
                            letterSpacing: "0.06em",
                        }, children: "MEMORY" }), _jsx("span", { style: {
                            fontFamily: "var(--mono)",
                            fontSize: 10,
                            color: "var(--ink3)",
                        }, children: loading
                            ? "loading…"
                            : `${memories.length} engrams · ${tierCounts.canonical || 0} canonical · ${memories.reduce((a, m) => a + m.tokens, 0)} tokens indexed${activeCapture.length > 0
                                ? ` · ${activeCapture.length} live session${activeCapture.length > 1 ? "s" : ""} · ${activeSurfaceCount} surfaces`
                                : ""}` }), !live && !loading && (_jsx("span", { style: {
                            fontFamily: "var(--mono)",
                            fontSize: 9,
                            color: "var(--rose)",
                            padding: "1px 6px",
                            border: "1px solid var(--rose)",
                        }, title: error || undefined, children: "BACKEND NOT LIVE" })), _jsxs("div", { style: { marginLeft: "auto", display: "flex", gap: 8 }, children: [_jsx("input", { value: search, onChange: (e) => setSearch(e.target.value), placeholder: "dhee recall\u2026", style: {
                                    border: "1px solid var(--border)",
                                    padding: "4px 10px",
                                    fontFamily: "var(--mono)",
                                    fontSize: 11,
                                    width: 180,
                                    color: "var(--ink)",
                                    background: "transparent",
                                } }), _jsx("button", { onClick: () => setAddMode((a) => !a), style: {
                                    padding: "4px 12px",
                                    border: `1px solid ${addMode ? "var(--accent)" : "var(--border)"}`,
                                    fontFamily: "var(--mono)",
                                    fontSize: 10,
                                    color: addMode ? "var(--accent)" : "var(--ink2)",
                                    background: "transparent",
                                    cursor: "pointer",
                                }, children: "+ REMEMBER" })] })] }), addMode && (_jsxs("div", { style: {
                    padding: "12px 20px",
                    borderBottom: "1px solid var(--border)",
                    background: "var(--surface)",
                    display: "flex",
                    gap: 10,
                }, children: [_jsx("textarea", { value: newText, onChange: (e) => setNewText(e.target.value), onKeyDown: (e) => {
                            if (e.key === "Enter" && (e.metaKey || e.ctrlKey))
                                saveMemory();
                        }, placeholder: "What should Dhee remember? (\u2318\u21B5 to save)", rows: 2, style: {
                            flex: 1,
                            border: "1px solid var(--border)",
                            padding: "8px 10px",
                            fontFamily: "var(--font)",
                            fontSize: 13,
                            color: "var(--ink)",
                            background: "white",
                            resize: "none",
                            outline: "none",
                        } }), _jsxs("div", { style: { display: "flex", flexDirection: "column", gap: 5 }, children: [_jsx("button", { onClick: saveMemory, style: {
                                    padding: "6px 14px",
                                    background: "var(--ink)",
                                    color: "var(--bg)",
                                    fontFamily: "var(--mono)",
                                    fontSize: 10,
                                    cursor: "pointer",
                                }, children: "SAVE" }), _jsx("button", { onClick: () => {
                                    setAddMode(false);
                                    setNewText("");
                                }, style: {
                                    padding: "6px 14px",
                                    border: "1px solid var(--border)",
                                    fontFamily: "var(--mono)",
                                    fontSize: 10,
                                    cursor: "pointer",
                                    color: "var(--ink3)",
                                    background: "transparent",
                                }, children: "CANCEL" })] })] })), _jsxs("div", { style: { flex: 1, display: "flex", overflow: "hidden" }, children: [_jsxs("div", { style: {
                            width: 168,
                            borderRight: "1px solid var(--border)",
                            display: "flex",
                            flexDirection: "column",
                            flexShrink: 0,
                            overflowY: "auto",
                        }, children: [_jsx("div", { style: { padding: "10px 0" }, children: tierList.map((t) => {
                                    const count = t.id === "all" ? memories.length : tierCounts[t.id] || 0;
                                    const active = selectedTier === t.id;
                                    return (_jsxs("button", { onClick: () => setSelectedTier(t.id), style: {
                                            width: "100%",
                                            textAlign: "left",
                                            padding: "8px 16px",
                                            background: active ? "var(--surface)" : "transparent",
                                            borderLeft: `3px solid ${active ? "var(--accent)" : "transparent"}`,
                                            display: "flex",
                                            justifyContent: "space-between",
                                            alignItems: "center",
                                            cursor: "pointer",
                                        }, children: [_jsx("span", { style: {
                                                    fontSize: 12.5,
                                                    color: active ? "var(--ink)" : "var(--ink2)",
                                                }, children: t.label }), _jsx("span", { style: {
                                                    fontFamily: "var(--mono)",
                                                    fontSize: 10,
                                                    color: "var(--ink3)",
                                                }, children: count })] }, t.id));
                                }) }), _jsxs("div", { style: {
                                    margin: "0 16px",
                                    borderTop: "1px solid var(--border)",
                                    paddingTop: 14,
                                }, children: [_jsx("div", { style: {
                                            fontFamily: "var(--mono)",
                                            fontSize: 9,
                                            color: "var(--ink3)",
                                            letterSpacing: "0.06em",
                                            marginBottom: 8,
                                        }, children: "RETENTION" }), Object.entries(retentionInfo).map(([t, r]) => (_jsxs("div", { style: {
                                            display: "flex",
                                            justifyContent: "space-between",
                                            marginBottom: 4,
                                        }, children: [_jsx("span", { style: { fontSize: 11, color: "var(--ink3)" }, children: t }), _jsx("span", { style: {
                                                    fontFamily: "var(--mono)",
                                                    fontSize: 10,
                                                    color: "var(--ink2)",
                                                }, children: r })] }, t)))] })] }), _jsxs("div", { style: { flex: 1, overflowY: "auto" }, children: [(activeCapture.length > 0 || recentCaptureItems.length > 0) && (_jsxs("div", { style: {
                                    padding: "18px 20px",
                                    borderBottom: "1px solid var(--border)",
                                    background: "linear-gradient(180deg, oklch(0.98 0.015 80), transparent)",
                                }, children: [_jsx(SectionHeader, { label: "Live Capture", sub: activeCapture.length > 0
                                            ? `${activeCapture.length} active session${activeCapture.length > 1 ? "s" : ""}`
                                            : "recent capture timeline" }), activeCapture.length > 0 && (_jsx("div", { style: {
                                            display: "grid",
                                            gridTemplateColumns: "repeat(auto-fit, minmax(240px, 1fr))",
                                            gap: 12,
                                            marginBottom: recentCaptureItems.length ? 14 : 0,
                                        }, children: activeCapture.map((entry) => (_jsx(ActiveCaptureCard, { entry: entry }, entry.session.id))) })), recentCaptureItems.length > 0 && (_jsxs("div", { children: [_jsx(SectionHeader, { label: "Recent Events" }), _jsx("div", { style: { display: "grid", gap: 8 }, children: recentCaptureItems.map((item, index) => (_jsxs("div", { style: {
                                                        padding: "10px 12px",
                                                        border: "1px solid var(--border)",
                                                        background: "white",
                                                        display: "flex",
                                                        gap: 10,
                                                        alignItems: "baseline",
                                                    }, children: [_jsx("span", { style: {
                                                                fontFamily: "var(--mono)",
                                                                fontSize: 9,
                                                                color: "var(--accent)",
                                                                minWidth: 82,
                                                                textTransform: "uppercase",
                                                            }, children: item.kind }), _jsx("div", { style: {
                                                                flex: 1,
                                                                fontSize: 12.5,
                                                                lineHeight: 1.5,
                                                                color: "var(--ink2)",
                                                            }, children: _timelineSummary(item) }), _jsx("span", { style: {
                                                                fontFamily: "var(--mono)",
                                                                fontSize: 9,
                                                                color: "var(--ink3)",
                                                            }, children: _clock(item.timestamp) })] }, `${item.kind}:${item.timestamp}:${index}`))) })] }))] })), activeCapture.length === 0 && recentSessions.length > 0 && (_jsxs("div", { style: {
                                    padding: "14px 20px",
                                    borderBottom: "1px solid var(--border)",
                                    background: "var(--surface)",
                                }, children: [_jsx(SectionHeader, { label: "Recent Sessions" }), _jsx("div", { style: { display: "flex", gap: 8, flexWrap: "wrap" }, children: recentSessions.map((session) => (_jsxs("span", { style: {
                                                padding: "5px 8px",
                                                border: "1px solid var(--border)",
                                                fontFamily: "var(--mono)",
                                                fontSize: 10,
                                                color: "var(--ink2)",
                                                background: "white",
                                            }, children: [session.source_app, " \u00B7 ", session.status, " \u00B7 ", _clock(session.started_at)] }, session.id))) })] })), !loading && filtered.length === 0 && (_jsx("div", { style: {
                                    padding: "40px 24px",
                                    color: "var(--ink3)",
                                    fontSize: 13,
                                    textAlign: "center",
                                }, children: memories.length === 0
                                    ? live
                                        ? "No engrams yet — remember something to seed Dhee."
                                        : "Backend unreachable — is the Dhee FastAPI bridge running?"
                                    : "No engrams match — try a different filter or query" })), filtered.map((eng) => {
                                const isExpanded = expandedId === eng.id;
                                return (_jsxs("div", { style: { borderBottom: "1px solid var(--border)" }, children: [_jsxs("div", { onClick: () => setExpandedId(isExpanded ? null : eng.id), style: {
                                                padding: "14px 20px",
                                                cursor: "pointer",
                                                display: "flex",
                                                gap: 12,
                                                alignItems: "flex-start",
                                                transition: "background 0.1s",
                                            }, onMouseEnter: (e) => (e.currentTarget.style.background = "var(--surface)"), onMouseLeave: (e) => (e.currentTarget.style.background = "transparent"), children: [_jsx(TierBadge, { tier: eng.tier }), _jsxs("div", { style: { flex: 1, minWidth: 0 }, children: [_jsx("div", { style: {
                                                                fontSize: 13.5,
                                                                lineHeight: 1.55,
                                                                marginBottom: 7,
                                                                color: "var(--ink)",
                                                            }, children: eng.content }), _jsxs("div", { style: {
                                                                display: "flex",
                                                                gap: 14,
                                                                alignItems: "center",
                                                                flexWrap: "wrap",
                                                            }, children: [_jsx("span", { style: {
                                                                        fontFamily: "var(--mono)",
                                                                        fontSize: 9,
                                                                        color: "var(--ink3)",
                                                                    }, children: eng.id }), _jsx("span", { style: {
                                                                        fontFamily: "var(--mono)",
                                                                        fontSize: 9,
                                                                        color: "var(--ink3)",
                                                                    }, children: eng.source }), _jsx("span", { style: {
                                                                        fontFamily: "var(--mono)",
                                                                        fontSize: 9,
                                                                        color: "var(--ink3)",
                                                                    }, children: eng.created }), _jsx(DecayBar, { decay: eng.decay }), eng.reaffirmed > 0 && (_jsxs("span", { style: {
                                                                        fontFamily: "var(--mono)",
                                                                        fontSize: 9,
                                                                        color: "var(--green)",
                                                                    }, children: ["\u2191 \u00D7", eng.reaffirmed] })), _jsxs("span", { style: {
                                                                        fontFamily: "var(--mono)",
                                                                        fontSize: 9,
                                                                        color: "var(--ink3)",
                                                                    }, children: ["~", eng.tokens, "t"] })] }), eng.tags.length > 0 && (_jsx("div", { style: {
                                                                marginTop: 6,
                                                                display: "flex",
                                                                gap: 4,
                                                                flexWrap: "wrap",
                                                            }, children: eng.tags.map((t) => (_jsx("span", { style: {
                                                                    padding: "1px 6px",
                                                                    background: "var(--surface2)",
                                                                    border: "1px solid var(--border)",
                                                                    fontFamily: "var(--mono)",
                                                                    fontSize: 9,
                                                                    color: "var(--ink3)",
                                                                }, children: t }, t))) }))] }), rechunking === eng.id && (_jsx("span", { style: {
                                                        fontFamily: "var(--mono)",
                                                        fontSize: 9,
                                                        color: "var(--accent)",
                                                        flexShrink: 0,
                                                        paddingTop: 2,
                                                    }, children: "re-chunking\u2026" }))] }), isExpanded && (_jsxs("div", { style: {
                                                padding: "0 20px 14px",
                                                borderTop: "1px solid var(--surface2)",
                                            }, children: [_jsx("div", { style: {
                                                        paddingTop: 10,
                                                        display: "flex",
                                                        gap: 6,
                                                        flexWrap: "wrap",
                                                        marginBottom: 10,
                                                    }, children: [
                                                        {
                                                            label: "↻ RE-CHUNK",
                                                            fn: () => triggerRechunk(eng.id),
                                                            color: "var(--ink2)",
                                                        },
                                                        {
                                                            label: "↑ PROMOTE",
                                                            fn: () => { },
                                                            color: "var(--green)",
                                                        },
                                                        {
                                                            label: "⊃ SUPERSEDE",
                                                            fn: () => { },
                                                            color: "var(--indigo)",
                                                        },
                                                        {
                                                            label: "✕ ARCHIVE",
                                                            fn: () => archive(eng.id),
                                                            color: "var(--rose)",
                                                        },
                                                    ].map((btn) => (_jsx("button", { onClick: btn.fn, style: {
                                                            padding: "3px 9px",
                                                            border: `1px solid ${btn.color}`,
                                                            fontFamily: "var(--mono)",
                                                            fontSize: 9,
                                                            color: btn.color,
                                                            background: "transparent",
                                                            cursor: "pointer",
                                                        }, children: btn.label }, btn.label))) }), _jsxs("div", { style: {
                                                        padding: "8px 12px",
                                                        background: "oklch(0.1 0.01 260)",
                                                        fontFamily: "var(--mono)",
                                                        fontSize: 11,
                                                        lineHeight: 1.7,
                                                    }, children: [_jsxs("div", { style: { color: "oklch(0.5 0.01 260)" }, children: ["$ dhee why ", eng.id] }), _jsxs("div", { style: {
                                                                color: "oklch(0.75 0.01 260)",
                                                                marginTop: 4,
                                                            }, children: ["source: ", eng.source, _jsx("br", {}), "ingested \u2192 chunk:", eng.id, " \u2192 tier:", eng.tier, eng.reaffirmed > 0
                                                                    ? ` → reaffirmed ×${eng.reaffirmed}`
                                                                    : "", _jsx("br", {}), "decay: ", Math.round(eng.decay * 100), "% \u00B7 tokens:", " ", eng.tokens] })] })] }))] }, eng.id));
                            })] })] })] }));
}
function ActiveCaptureCard({ entry }) {
    const surfaces = entry.graph?.surfaces || [];
    const observations = entry.graph?.observations || [];
    const artifacts = entry.graph?.artifacts || [];
    return (_jsxs("div", { style: {
            border: "1px solid var(--border)",
            background: "white",
            padding: "12px 14px",
        }, children: [_jsxs("div", { style: {
                    display: "flex",
                    justifyContent: "space-between",
                    gap: 10,
                    marginBottom: 8,
                }, children: [_jsxs("div", { children: [_jsx("div", { style: { fontSize: 13, fontWeight: 600, marginBottom: 2 }, children: entry.session.source_app }), _jsxs("div", { style: {
                                    fontFamily: "var(--mono)",
                                    fontSize: 9,
                                    color: "var(--ink3)",
                                }, children: [_clock(entry.session.started_at), " \u00B7 ", entry.session.namespace] })] }), _jsx("span", { style: {
                            fontFamily: "var(--mono)",
                            fontSize: 9,
                            color: "var(--green)",
                            border: "1px solid var(--green)",
                            padding: "2px 6px",
                            height: "fit-content",
                        }, children: "ACTIVE" })] }), _jsxs("div", { style: {
                    display: "flex",
                    gap: 10,
                    flexWrap: "wrap",
                    marginBottom: 8,
                    fontFamily: "var(--mono)",
                    fontSize: 10,
                    color: "var(--ink2)",
                }, children: [_jsxs("span", { children: [surfaces.length, " surfaces"] }), _jsxs("span", { children: [observations.length, " observations"] }), _jsxs("span", { children: [artifacts.length, " artifacts"] })] }), _jsx("div", { style: { display: "grid", gap: 6 }, children: surfaces.slice(0, 3).map((surface) => (_jsxs("div", { style: {
                        borderLeft: "2px solid var(--accent)",
                        paddingLeft: 8,
                    }, children: [_jsx("div", { style: { fontSize: 12.5, color: "var(--ink)" }, children: surface.title || surface.url || surface.app_path || surface.id }), _jsxs("div", { style: {
                                fontFamily: "var(--mono)",
                                fontSize: 9,
                                color: "var(--ink3)",
                                marginTop: 2,
                            }, children: [surface.surface_type, surface.path_hint?.length ? ` · ${surface.path_hint.join(" / ")}` : ""] })] }, surface.id))) })] }));
}
function _timelineSummary(item) {
    const payload = item.item || {};
    const text = String(payload.text || payload.text_payload || payload.label || "").trim();
    if (text)
        return text.slice(0, 220);
    const actionType = String(payload.action_type || payload.actionType || "").trim();
    const title = String(payload.window_title || payload.title || "").trim();
    const url = String(payload.url || "").trim();
    return [actionType, title, url].filter(Boolean).join(" · ") || item.kind;
}
function _clock(value) {
    if (!value)
        return "—";
    const dt = new Date(value);
    if (Number.isNaN(dt.getTime()))
        return "—";
    return dt.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}
