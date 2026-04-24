import { jsxs as _jsxs, jsx as _jsx } from "react/jsx-runtime";
import { useEffect, useState } from "react";
import { api } from "../api";
import { SectionHeader } from "../components/ui/SectionHeader";
function formatCompactNumber(value) {
    if (value == null)
        return "—";
    return new Intl.NumberFormat("en", { notation: "compact", maximumFractionDigits: 1 }).format(value);
}
function formatTimestamp(value) {
    if (!value)
        return "—";
    const d = new Date(value);
    if (Number.isNaN(d.getTime()))
        return "—";
    return d.toLocaleString();
}
export function RouterView() {
    const [stats, setStats] = useState(null);
    const [policies, setPolicies] = useState([]);
    const [providers, setProviders] = useState([]);
    const [agentFilter, setAgentFilter] = useState("all");
    const [draftKeys, setDraftKeys] = useState({});
    const [tuning, setTuning] = useState(false);
    const [tuned, setTuned] = useState(false);
    const [savingProvider, setSavingProvider] = useState(null);
    const [saveMessage, setSaveMessage] = useState(null);
    const [tuneLog, setTuneLog] = useState([]);
    const [error, setError] = useState(null);
    const reload = async (selectedAgent = agentFilter) => {
        try {
            const [s, p, keyState] = await Promise.all([
                api.routerStats(selectedAgent === "all" ? undefined : selectedAgent),
                api.routerPolicy(),
                api.apiKeys(),
            ]);
            setStats(s);
            setPolicies(p.policies || []);
            setProviders(keyState.providers || []);
            setError(s.error || p.error || keyState.error || null);
        }
        catch (e) {
            setError(String(e));
        }
    };
    useEffect(() => {
        reload(agentFilter);
    }, [agentFilter]);
    const runTune = async () => {
        setTuning(true);
        setTuned(false);
        setTuneLog(["Reading expansion ledger…"]);
        try {
            const res = await api.routerTune();
            const lines = [
                "Reading expansion ledger…",
                `Analysing ${stats?.totalCalls || 0} calls…`,
                ...(res.suggestions || []).map((s) => `${s.intent}: ${s.from} → ${s.to} · ${s.reason}`),
                `✓ Applied ${res.applied} change${res.applied === 1 ? "" : "s"}.`,
            ];
            setTuneLog(lines);
            setTuned(true);
            reload(agentFilter);
        }
        catch (e) {
            setTuneLog((l) => [...l, `✗ ${String(e)}`]);
        }
        finally {
            setTuning(false);
        }
    };
    const handleKeySave = async (provider) => {
        const next = (draftKeys[provider.provider] || "").trim();
        if (!next)
            return;
        setSavingProvider(provider.provider);
        setSaveMessage(null);
        try {
            if (provider.hasStoredKey) {
                await api.rotateApiKey(provider.provider, next);
                setSaveMessage(`${provider.label} key rotated.`);
            }
            else {
                await api.storeApiKey(provider.provider, next);
                setSaveMessage(`${provider.label} key stored.`);
            }
            setDraftKeys((prev) => ({ ...prev, [provider.provider]: "" }));
            await reload(agentFilter);
        }
        catch (e) {
            setSaveMessage(String(e));
        }
        finally {
            setSavingProvider(null);
        }
    };
    if (!stats) {
        return (_jsxs("div", { style: {
                padding: 24,
                fontFamily: "var(--mono)",
                fontSize: 11,
                color: "var(--ink3)",
            }, children: ["loading router\u2026", error ? ` — ${error}` : ""] }));
    }
    const agentOptions = [
        {
            id: "all",
            label: "all",
            calls: stats.totalCalls,
            tokensSaved: stats.sessionTokensSaved,
            bytesStored: stats.bytesStored,
            expansionRate: stats.expansionRate,
            sessions: stats.sessions,
        },
        ...(stats.agents || []),
    ];
    const codexNative = stats.codexNative;
    const showCodexNative = (agentFilter === "codex" || agentFilter === "all") && codexNative?.available;
    const routerCards = [
        {
            label: "Tokens Saved",
            value: stats.sessionTokensSaved.toLocaleString(),
            sub: stats.selectedAgent === "all" ? "dhee savings across agents" : `${stats.selectedAgent} dhee savings`,
            big: true,
            accent: "var(--accent)",
            bg: "oklch(0.97 0.04 36)",
        },
        ...(showCodexNative
            ? [
                {
                    label: "Codex Native Total",
                    value: formatCompactNumber(codexNative?.totalTokens),
                    sub: codexNative?.cachedInputTokens != null
                        ? `${formatCompactNumber(codexNative.cachedInputTokens)} cached input`
                        : codexNative?.model || "active codex thread",
                    big: false,
                    accent: "var(--indigo)",
                    bg: "oklch(0.98 0.02 262)",
                },
                {
                    label: "Codex Last Turn",
                    value: formatCompactNumber(codexNative?.lastTurnTokens),
                    sub: codexNative?.primaryUsedPercent != null
                        ? `${Math.round(codexNative.primaryUsedPercent)}% primary limit used`
                        : "latest native token_count",
                    big: false,
                    accent: "var(--indigo)",
                    bg: "white",
                },
            ]
            : []),
        {
            label: "Expansion Rate",
            value: `${Math.round(stats.expansionRate * 100)}%`,
            sub: "of digests expanded",
            big: false,
            accent: "var(--ink)",
            bg: "white",
        },
        {
            label: "Total Calls",
            value: stats.totalCalls,
            sub: "Read + Bash + Agent",
            big: false,
            accent: "var(--ink)",
            bg: "white",
        },
    ];
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
                        }, children: "ROUTER" }), _jsx("span", { style: {
                            fontFamily: "var(--mono)",
                            fontSize: 10,
                            color: "var(--ink3)",
                        }, children: "token savings + digest policy + key vault" }), !stats.live && (_jsx("span", { style: {
                            fontFamily: "var(--mono)",
                            fontSize: 9,
                            color: "var(--rose)",
                            padding: "1px 6px",
                            border: "1px solid var(--rose)",
                        }, children: "NOT LIVE" })), _jsx("div", { style: { marginLeft: "auto" }, children: _jsx("button", { onClick: runTune, disabled: tuning, style: {
                                padding: "5px 14px",
                                border: `1px solid ${tuned ? "var(--green)" : "var(--border)"}`,
                                fontFamily: "var(--mono)",
                                fontSize: 10,
                                color: tuning
                                    ? "var(--ink3)"
                                    : tuned
                                        ? "var(--green)"
                                        : "var(--accent)",
                                background: "transparent",
                                cursor: tuning ? "wait" : "pointer",
                            }, children: tuning ? "tuning…" : tuned ? "✓ tuned" : "dhee router tune" }) })] }), _jsxs("div", { style: { padding: "24px", maxWidth: 980 }, children: [tuneLog.length > 0 && (_jsx("div", { style: {
                            marginBottom: 24,
                            padding: "12px 16px",
                            background: "oklch(0.1 0.01 260)",
                            fontFamily: "var(--mono)",
                            fontSize: 11,
                            lineHeight: 1.8,
                        }, children: tuneLog.map((l, i) => (_jsx("div", { style: {
                                color: l.startsWith("✓")
                                    ? "var(--green)"
                                    : l.includes("→")
                                        ? "var(--accent)"
                                        : "oklch(0.65 0.01 260)",
                            }, children: l }, i))) })), _jsxs("div", { style: { marginBottom: 28 }, children: [_jsx(SectionHeader, { label: "Agent Filter", sub: "all, claude-code, codex, or any discovered agent" }), _jsx("div", { style: {
                                    display: "flex",
                                    gap: 10,
                                    flexWrap: "wrap",
                                }, children: agentOptions.map((agent) => {
                                    const active = agentFilter === agent.id;
                                    return (_jsxs("button", { onClick: () => setAgentFilter(agent.id), style: {
                                            padding: "10px 12px",
                                            border: `1px solid ${active ? "var(--accent)" : "var(--border)"}`,
                                            background: active ? "oklch(0.97 0.04 36)" : "white",
                                            cursor: "pointer",
                                            minWidth: 150,
                                            textAlign: "left",
                                        }, children: [_jsx("div", { style: {
                                                    fontFamily: "var(--mono)",
                                                    fontSize: 10,
                                                    color: active ? "var(--accent)" : "var(--ink2)",
                                                    marginBottom: 4,
                                                }, children: agent.label.toUpperCase() }), _jsx("div", { style: {
                                                    fontFamily: "var(--mono)",
                                                    fontSize: 16,
                                                    fontWeight: 700,
                                                    color: "var(--ink)",
                                                    marginBottom: 2,
                                                }, children: agent.tokensSaved.toLocaleString() }), _jsxs("div", { style: { fontSize: 11, color: "var(--ink3)" }, children: [agent.calls, " calls"] })] }, agent.id));
                                }) })] }), _jsx("div", { style: {
                            display: "grid",
                            gridTemplateColumns: `repeat(${Math.min(4, routerCards.length)}, 1fr)`,
                            gap: 14,
                            marginBottom: 28,
                        }, children: routerCards.map((s) => (_jsxs("div", { style: {
                                padding: "16px",
                                border: `1px solid ${s.big ? s.accent : "var(--border)"}`,
                                background: s.bg,
                            }, children: [_jsx("div", { style: {
                                        fontFamily: "var(--mono)",
                                        fontSize: 9,
                                        color: "var(--ink3)",
                                        letterSpacing: "0.08em",
                                        marginBottom: 6,
                                    }, children: s.label.toUpperCase() }), _jsx("div", { style: {
                                        fontFamily: "var(--mono)",
                                        fontSize: s.big ? 26 : 20,
                                        fontWeight: 700,
                                        color: s.big ? s.accent : s.accent,
                                        lineHeight: 1.1,
                                        marginBottom: 4,
                                    }, children: s.value }), _jsx("div", { style: { fontSize: 11, color: "var(--ink3)" }, children: s.sub })] }, s.label))) }), showCodexNative && (_jsxs("div", { style: { marginBottom: 28 }, children: [_jsx(SectionHeader, { label: "Live Codex Usage", sub: "native rollout telemetry from the active codex thread" }), _jsxs("div", { style: {
                                    padding: "16px",
                                    border: "1px solid var(--border)",
                                    background: "white",
                                }, children: [_jsx("div", { style: {
                                            display: "grid",
                                            gridTemplateColumns: "repeat(4, 1fr)",
                                            gap: 12,
                                            marginBottom: 12,
                                        }, children: [
                                            {
                                                label: "Input",
                                                value: formatCompactNumber(codexNative?.inputTokens),
                                                sub: codexNative?.cachedInputTokens != null
                                                    ? `${formatCompactNumber(codexNative.cachedInputTokens)} cached`
                                                    : "prompt tokens",
                                            },
                                            {
                                                label: "Output",
                                                value: formatCompactNumber(codexNative?.outputTokens),
                                                sub: codexNative?.reasoningOutputTokens != null
                                                    ? `${formatCompactNumber(codexNative.reasoningOutputTokens)} reasoning`
                                                    : "assistant tokens",
                                            },
                                            {
                                                label: "Primary Limit",
                                                value: codexNative?.primaryUsedPercent != null
                                                    ? `${Math.round(codexNative.primaryUsedPercent)}%`
                                                    : "—",
                                                sub: `reset ${formatTimestamp(codexNative?.resetAt || undefined)}`,
                                            },
                                            {
                                                label: "Updated",
                                                value: formatTimestamp(codexNative?.updatedAt || undefined),
                                                sub: codexNative?.model || "active thread",
                                            },
                                        ].map((item) => (_jsxs("div", { children: [_jsx("div", { style: {
                                                        fontFamily: "var(--mono)",
                                                        fontSize: 9,
                                                        color: "var(--ink3)",
                                                        letterSpacing: "0.08em",
                                                        marginBottom: 6,
                                                    }, children: item.label.toUpperCase() }), _jsx("div", { style: {
                                                        fontFamily: "var(--mono)",
                                                        fontSize: 20,
                                                        fontWeight: 700,
                                                        color: "var(--indigo)",
                                                        marginBottom: 4,
                                                    }, children: item.value }), _jsx("div", { style: { fontSize: 11, color: "var(--ink3)" }, children: item.sub })] }, item.label))) }), _jsx("div", { style: {
                                            fontSize: 12,
                                            color: "var(--ink2)",
                                            borderTop: "1px solid var(--border)",
                                            paddingTop: 10,
                                        }, children: codexNative?.title || "Codex thread" })] })] })), stats.tools.length > 0 && (_jsxs("div", { style: { marginBottom: 28 }, children: [_jsx(SectionHeader, { label: "By Tool" }), _jsx("div", { style: {
                                    display: "grid",
                                    gridTemplateColumns: "repeat(3, 1fr)",
                                    gap: 12,
                                }, children: stats.tools.map((tool) => {
                                    const expRate = tool.expansions / Math.max(1, tool.calls);
                                    return (_jsxs("div", { style: {
                                            padding: "14px 16px",
                                            border: "1px solid var(--border)",
                                            background: "white",
                                        }, children: [_jsxs("div", { style: {
                                                    display: "flex",
                                                    justifyContent: "space-between",
                                                    marginBottom: 10,
                                                }, children: [_jsx("span", { style: {
                                                            fontFamily: "var(--mono)",
                                                            fontSize: 13,
                                                            fontWeight: 700,
                                                        }, children: tool.name }), _jsxs("span", { style: {
                                                            fontFamily: "var(--mono)",
                                                            fontSize: 10,
                                                            color: "var(--ink3)",
                                                        }, children: [tool.calls, " calls"] })] }), _jsx("div", { style: {
                                                    fontFamily: "var(--mono)",
                                                    fontSize: 22,
                                                    fontWeight: 700,
                                                    color: "var(--accent)",
                                                    marginBottom: 2,
                                                }, children: tool.tokensSaved.toLocaleString() }), _jsx("div", { style: {
                                                    fontSize: 11,
                                                    color: "var(--ink3)",
                                                    marginBottom: 12,
                                                }, children: "tokens saved" }), _jsxs("div", { style: {
                                                    fontFamily: "var(--mono)",
                                                    fontSize: 10,
                                                    color: "var(--ink2)",
                                                    marginBottom: 8,
                                                }, children: ["digest ~", tool.avgDigest, "t \u2192 raw ~", tool.avgRaw, "t"] }), _jsxs("div", { children: [_jsxs("div", { style: {
                                                            display: "flex",
                                                            justifyContent: "space-between",
                                                            marginBottom: 3,
                                                        }, children: [_jsx("span", { style: {
                                                                    fontFamily: "var(--mono)",
                                                                    fontSize: 9,
                                                                    color: "var(--ink3)",
                                                                }, children: "EXPANSION RATE" }), _jsxs("span", { style: {
                                                                    fontFamily: "var(--mono)",
                                                                    fontSize: 9,
                                                                    color: expRate > 0.3 ? "var(--rose)" : "var(--green)",
                                                                }, children: [Math.round(expRate * 100), "%"] })] }), _jsx("div", { style: { height: 3, background: "var(--surface2)" }, children: _jsx("div", { style: {
                                                                height: "100%",
                                                                width: `${expRate * 100}%`,
                                                                background: expRate > 0.3
                                                                    ? "var(--rose)"
                                                                    : expRate > 0.1
                                                                        ? "var(--accent)"
                                                                        : "var(--green)",
                                                            } }) })] })] }, tool.name));
                                }) })] })), _jsxs("div", { style: { marginBottom: 28 }, children: [_jsx(SectionHeader, { label: "API Keys", sub: "encrypted local storage with rotation" }), saveMessage && (_jsx("div", { style: {
                                    marginBottom: 12,
                                    padding: "10px 12px",
                                    border: "1px solid var(--border)",
                                    background: "white",
                                    fontSize: 12,
                                    color: saveMessage.includes("Error") ? "var(--rose)" : "var(--ink2)",
                                }, children: saveMessage })), _jsx("div", { style: {
                                    display: "grid",
                                    gridTemplateColumns: "repeat(3, 1fr)",
                                    gap: 12,
                                }, children: providers.map((provider) => (_jsxs("div", { style: {
                                        padding: "14px 16px",
                                        border: "1px solid var(--border)",
                                        background: "white",
                                    }, children: [_jsxs("div", { style: {
                                                display: "flex",
                                                justifyContent: "space-between",
                                                marginBottom: 8,
                                                alignItems: "center",
                                            }, children: [_jsx("span", { style: {
                                                        fontFamily: "var(--mono)",
                                                        fontSize: 12,
                                                        fontWeight: 700,
                                                    }, children: provider.label }), _jsx("span", { style: {
                                                        fontFamily: "var(--mono)",
                                                        fontSize: 9,
                                                        color: provider.activeSource === "env"
                                                            ? "var(--accent)"
                                                            : provider.activeSource === "stored"
                                                                ? "var(--green)"
                                                                : "var(--ink3)",
                                                    }, children: provider.activeSource.toUpperCase() })] }), _jsxs("div", { style: { fontSize: 11, color: "var(--ink3)", marginBottom: 6 }, children: ["Active key: ", provider.activePreview || "none"] }), _jsxs("div", { style: { fontSize: 11, color: "var(--ink3)", marginBottom: 6 }, children: ["Stored versions: ", provider.storedVersionsCount] }), _jsxs("div", { style: { fontSize: 11, color: "var(--ink3)", marginBottom: 12 }, children: ["Updated: ", formatTimestamp(provider.updatedAt)] }), provider.note && (_jsx("div", { style: {
                                                marginBottom: 10,
                                                fontSize: 11,
                                                color: "var(--accent)",
                                                lineHeight: 1.5,
                                            }, children: provider.note })), _jsx("input", { type: "password", value: draftKeys[provider.provider] || "", onChange: (e) => setDraftKeys((prev) => ({
                                                ...prev,
                                                [provider.provider]: e.target.value,
                                            })), placeholder: provider.hasStoredKey ? "Paste new key to rotate" : "Paste key to store", style: {
                                                width: "100%",
                                                padding: "10px 12px",
                                                border: "1px solid var(--border)",
                                                fontFamily: "var(--mono)",
                                                fontSize: 11,
                                                marginBottom: 10,
                                                boxSizing: "border-box",
                                            } }), _jsx("button", { onClick: () => handleKeySave(provider), disabled: savingProvider === provider.provider ||
                                                !(draftKeys[provider.provider] || "").trim(), style: {
                                                width: "100%",
                                                padding: "8px 12px",
                                                border: "1px solid var(--border)",
                                                background: "transparent",
                                                fontFamily: "var(--mono)",
                                                fontSize: 10,
                                                color: "var(--accent)",
                                                cursor: "pointer",
                                            }, children: savingProvider === provider.provider
                                                ? "saving…"
                                                : provider.hasStoredKey
                                                    ? "rotate key"
                                                    : "store key" })] }, provider.provider))) })] }), policies.length > 0 && (_jsxs("div", { style: { marginBottom: 28 }, children: [_jsx(SectionHeader, { label: "Router Policy \u2014 Digest Depths", sub: "auto-tuned from expansion ledger" }), _jsx("div", { style: { border: "1px solid var(--border)", background: "white" }, children: policies.map((p, i) => (_jsxs("div", { style: {
                                        padding: "12px 18px",
                                        borderBottom: i < policies.length - 1
                                            ? "1px solid var(--surface2)"
                                            : "none",
                                        display: "flex",
                                        alignItems: "center",
                                        gap: 16,
                                    }, children: [_jsx("span", { style: {
                                                width: 96,
                                                fontFamily: "var(--mono)",
                                                fontSize: 11,
                                                color: "var(--ink2)",
                                            }, children: p.label }), _jsx("div", { style: { display: "flex", gap: 4 }, children: [1, 2, 3].map((d) => (_jsx("div", { style: {
                                                    width: 28,
                                                    height: 14,
                                                    background: d <= p.depth
                                                        ? p.tuned
                                                            ? "var(--accent)"
                                                            : "var(--ink2)"
                                                        : "var(--surface2)",
                                                } }, d))) }), _jsxs("span", { style: {
                                                fontFamily: "var(--mono)",
                                                fontSize: 10,
                                                color: "var(--ink3)",
                                                width: 48,
                                            }, children: ["depth ", p.depth] }), _jsxs("div", { style: { flex: 1 }, children: [_jsxs("div", { style: {
                                                        display: "flex",
                                                        justifyContent: "space-between",
                                                        marginBottom: 2,
                                                    }, children: [_jsx("span", { style: {
                                                                fontFamily: "var(--mono)",
                                                                fontSize: 9,
                                                                color: "var(--ink3)",
                                                            }, children: "expansion" }), _jsxs("span", { style: {
                                                                fontFamily: "var(--mono)",
                                                                fontSize: 9,
                                                                color: p.expansionRate > 0.3
                                                                    ? "var(--rose)"
                                                                    : p.expansionRate < 0.05
                                                                        ? "var(--green)"
                                                                        : "var(--ink3)",
                                                            }, children: [Math.round(p.expansionRate * 100), "%"] })] }), _jsx("div", { style: { height: 3, background: "var(--surface2)" }, children: _jsx("div", { style: {
                                                            height: "100%",
                                                            width: `${p.expansionRate * 100}%`,
                                                            background: p.expansionRate > 0.3
                                                                ? "var(--rose)"
                                                                : p.expansionRate < 0.05
                                                                    ? "var(--green)"
                                                                    : "var(--accent)",
                                                        } }) })] }), _jsxs("div", { style: { display: "flex", gap: 6, alignItems: "center" }, children: [p.tuned && (_jsx("span", { style: {
                                                        fontFamily: "var(--mono)",
                                                        fontSize: 8,
                                                        color: "var(--accent)",
                                                        padding: "1px 5px",
                                                        border: "1px solid var(--accent)",
                                                    }, children: "AUTO-TUNED" })), p.tuned && p.depth !== p.prevDepth && (_jsxs("span", { style: {
                                                        fontFamily: "var(--mono)",
                                                        fontSize: 9,
                                                        color: "var(--ink3)",
                                                    }, children: [p.prevDepth, "\u2192", p.depth] }))] })] }, `${p.tool}-${p.intent}`))) })] })), _jsxs("div", { children: [_jsx(SectionHeader, { label: "7-Day Token Savings" }), _jsx("div", { style: {
                                    border: "1px solid var(--border)",
                                    padding: "20px 24px",
                                    background: "white",
                                    display: "flex",
                                    gap: 8,
                                    alignItems: "flex-end",
                                    height: 140,
                                }, children: stats.dailySavings.map((v, i) => {
                                    const isNow = i === stats.dailySavings.length - 1;
                                    const max = Math.max(...stats.dailySavings, 1);
                                    const barH = Math.max(8, (v / max) * 88);
                                    return (_jsxs("div", { style: {
                                            flex: 1,
                                            display: "flex",
                                            flexDirection: "column",
                                            alignItems: "center",
                                            gap: 4,
                                        }, children: [_jsx("span", { style: {
                                                    fontFamily: "var(--mono)",
                                                    fontSize: 9,
                                                    color: "var(--ink3)",
                                                }, children: v >= 1000 ? `${(v / 1000).toFixed(0)}k` : v }), _jsx("div", { style: {
                                                    width: "100%",
                                                    background: isNow ? "var(--accent)" : "var(--border)",
                                                    height: barH,
                                                } }), _jsx("span", { style: {
                                                    fontFamily: "var(--mono)",
                                                    fontSize: 9,
                                                    color: isNow ? "var(--accent)" : "var(--ink3)",
                                                    fontWeight: isNow ? 700 : 400,
                                                }, children: stats.days[i] })] }, i));
                                }) })] })] })] }));
}
