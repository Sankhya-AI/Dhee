import { jsx as _jsx, jsxs as _jsxs } from "react/jsx-runtime";
import { useEffect, useState } from "react";
import { api } from "../api";
import { SectionHeader } from "../components/ui/SectionHeader";
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
    const [workspaces, setWorkspaces] = useState([]);
    const [repos, setRepos] = useState([]);
    const [selectedRepo, setSelectedRepo] = useState("");
    const [snapshot, setSnapshot] = useState(null);
    const [error, setError] = useState(null);
    const [busy, setBusy] = useState(null);
    const [demoteId, setDemoteId] = useState("");
    // ── Load linked workspaces and pick a default repo ─────────────────
    useEffect(() => {
        let cancelled = false;
        (async () => {
            try {
                const ws = await api.localWorkspaces();
                if (cancelled)
                    return;
                const list = ws.workspaces || [];
                setWorkspaces(list);
                const allRepos = list.flatMap((w) => w.folders || []);
                setRepos(allRepos);
                if (allRepos.length && !selectedRepo)
                    setSelectedRepo(allRepos[0]);
            }
            catch (e) {
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
                if (!cancelled)
                    setSnapshot(snap);
            }
            catch (e) {
                if (!cancelled)
                    setError(String(e));
            }
        })();
        return () => {
            cancelled = true;
        };
    }, [selectedRepo]);
    const reload = async () => {
        if (!selectedRepo)
            return;
        try {
            const snap = await api.contextEntries(selectedRepo, 200);
            setSnapshot(snap);
        }
        catch (e) {
            setError(String(e));
        }
    };
    const handleDemote = async (entry) => {
        setBusy(`demote:${entry.id}`);
        try {
            await api.contextDemote({ entry_id: entry.id, repo: selectedRepo });
            await reload();
        }
        catch (e) {
            setError(String(e));
        }
        finally {
            setBusy(null);
        }
    };
    const handleManualDemote = async () => {
        const id = demoteId.trim();
        if (!id)
            return;
        setBusy("demote:manual");
        try {
            await api.contextDemote({ entry_id: id, repo: selectedRepo });
            setDemoteId("");
            await reload();
        }
        catch (e) {
            setError(String(e));
        }
        finally {
            setBusy(null);
        }
    };
    const totals = snapshot?.totals;
    return (_jsxs("div", { style: {
            display: "flex",
            flexDirection: "column",
            height: "100%",
            padding: "16px 20px",
            gap: 16,
            overflow: "auto",
        }, children: [_jsx(SectionHeader, { label: "CONTEXT", sub: "Per-folder shared context \u00B7 personal vs repo \u00B7 share matrix" }), _jsxs("div", { style: {
                    display: "flex",
                    alignItems: "center",
                    gap: 12,
                    padding: "10px 12px",
                    border: "1px solid var(--border)",
                    background: "var(--surface)",
                    borderRadius: 4,
                }, children: [_jsx("span", { style: {
                            fontFamily: "var(--mono)",
                            fontSize: 11,
                            color: "var(--ink3)",
                        }, children: "REPO" }), _jsx("select", { value: selectedRepo, onChange: (e) => setSelectedRepo(e.target.value), style: {
                            flex: 1,
                            padding: "6px 8px",
                            background: "var(--bg)",
                            border: "1px solid var(--border)",
                            color: "var(--ink1)",
                            fontFamily: "var(--mono)",
                            fontSize: 12,
                        }, children: repos.length === 0 ? (_jsx("option", { value: "", children: "(no repos linked \u2014 run `dhee link` or add a folder)" })) : (repos.map((r) => (_jsx("option", { value: r, children: r }, r)))) }), totals ? (_jsxs("span", { style: {
                            fontFamily: "var(--mono)",
                            fontSize: 11,
                            color: "var(--ink2)",
                        }, children: [totals.repo_entries, " entries \u00B7 ", totals.promoted_in, " promoted in \u00B7", " ", totals.demoted_out, " demoted out \u00B7 ", totals.linked_peers, " peers"] })) : null] }), error ? (_jsx("div", { style: {
                    border: "1px solid var(--rose)",
                    color: "var(--rose)",
                    padding: "8px 10px",
                    fontFamily: "var(--mono)",
                    fontSize: 11,
                }, children: error })) : null, _jsxs("div", { style: {
                    display: "grid",
                    gridTemplateColumns: "1.4fr 1fr 1fr 0.8fr",
                    gap: 14,
                    alignItems: "stretch",
                }, children: [_jsx(RepoEntriesPanel, { entries: snapshot?.repo_entries || [], onDemote: handleDemote, busy: busy }), _jsx(ProvenancePanel, { title: "PROMOTED IN", subtitle: "Personal memories shared into this repo", rows: snapshot?.promoted_in || [], dateField: "promoted_at" }), _jsx(ProvenancePanel, { title: "DEMOTED OUT", subtitle: "Repo entries copied into your personal store", rows: snapshot?.demoted_out || [], dateField: "demoted_at" }), _jsx(SharePanel, { peers: snapshot?.share_matrix || [], activeRepo: selectedRepo, onSwitch: (p) => setSelectedRepo(p) })] }), _jsxs("div", { style: {
                    display: "flex",
                    gap: 8,
                    alignItems: "center",
                    padding: "8px 10px",
                    border: "1px dashed var(--border)",
                    color: "var(--ink2)",
                    fontSize: 11,
                }, children: [_jsx("span", { style: { fontFamily: "var(--mono)" }, children: "DEMOTE BY ID" }), _jsx("input", { value: demoteId, onChange: (e) => setDemoteId(e.target.value), placeholder: "entry id from the repo", style: {
                            flex: 1,
                            padding: "5px 7px",
                            background: "var(--bg)",
                            border: "1px solid var(--border)",
                            color: "var(--ink1)",
                            fontFamily: "var(--mono)",
                            fontSize: 11,
                        } }), _jsx("button", { disabled: !demoteId.trim() || busy === "demote:manual", onClick: handleManualDemote, style: {
                            padding: "5px 10px",
                            background: "var(--accent)",
                            color: "white",
                            border: 0,
                            fontFamily: "var(--mono)",
                            fontSize: 11,
                            cursor: "pointer",
                        }, children: busy === "demote:manual" ? "…" : "demote" })] })] }));
}
function RepoEntriesPanel({ entries, onDemote, busy, }) {
    return (_jsx(Panel, { title: "REPO ENTRIES", subtitle: `${entries.length} shared via git`, children: entries.length === 0 ? (_jsx(Empty, { hint: "No repo entries yet. Promote a personal memory or push a teammate's commits." })) : (entries.map((e) => (_jsxs("div", { style: {
                padding: "8px 0",
                borderBottom: "1px solid var(--border)",
                display: "flex",
                gap: 10,
                alignItems: "flex-start",
            }, children: [_jsxs("div", { style: { flex: 1, minWidth: 0 }, children: [_jsxs("div", { style: {
                                fontFamily: "var(--mono)",
                                fontSize: 10,
                                color: "var(--ink3)",
                                display: "flex",
                                gap: 8,
                            }, children: [_jsx("span", { children: e.kind }), _jsx("span", { children: "\u00B7" }), _jsx("span", { children: e.created_by }), _jsx("span", { children: "\u00B7" }), _jsx("span", { children: relTime(e.created_at) })] }), _jsx("div", { style: {
                                fontSize: 12,
                                color: "var(--ink1)",
                                fontWeight: 500,
                                margin: "2px 0",
                                textOverflow: "ellipsis",
                                whiteSpace: "nowrap",
                                overflow: "hidden",
                            }, title: e.title, children: e.title || "(untitled)" }), _jsx("div", { style: {
                                fontSize: 11,
                                color: "var(--ink2)",
                                display: "-webkit-box",
                                WebkitLineClamp: 2,
                                WebkitBoxOrient: "vertical",
                                overflow: "hidden",
                            }, children: e.content })] }), _jsx("button", { disabled: busy === `demote:${e.id}`, onClick: () => onDemote(e), title: "Copy this repo entry into your personal memory", style: {
                        padding: "4px 8px",
                        background: "transparent",
                        border: "1px solid var(--border)",
                        color: "var(--ink2)",
                        fontFamily: "var(--mono)",
                        fontSize: 10,
                        cursor: "pointer",
                    }, children: busy === `demote:${e.id}` ? "…" : "↓ keep" })] }, e.id)))) }));
}
function ProvenancePanel({ title, subtitle, rows, dateField, }) {
    return (_jsx(Panel, { title: title, subtitle: subtitle, children: rows.length === 0 ? (_jsx(Empty, { hint: "Nothing here yet." })) : (rows.map((r) => (_jsxs("div", { style: {
                padding: "8px 0",
                borderBottom: "1px solid var(--border)",
            }, children: [_jsxs("div", { style: {
                        fontFamily: "var(--mono)",
                        fontSize: 10,
                        color: "var(--ink3)",
                    }, children: [r.memory_id.slice(0, 12), " \u00B7 ", relTime(r[dateField])] }), _jsx("div", { style: {
                        fontSize: 11,
                        color: "var(--ink2)",
                        display: "-webkit-box",
                        WebkitLineClamp: 3,
                        WebkitBoxOrient: "vertical",
                        overflow: "hidden",
                    }, children: r.memory })] }, `${r.memory_id}-${r.entry_id || ""}`)))) }));
}
function SharePanel({ peers, activeRepo, onSwitch, }) {
    return (_jsx(Panel, { title: "SHARES WITH", subtitle: `${peers.length} other linked repos`, children: peers.length === 0 ? (_jsx(Empty, { hint: "No other linked repos. Run `dhee link` in another repo to compare context." })) : (peers.map((p) => (_jsxs("div", { onClick: () => onSwitch(p.repo_root), style: {
                padding: "8px 0",
                borderBottom: "1px solid var(--border)",
                cursor: "pointer",
                opacity: activeRepo === p.repo_root ? 0.6 : 1,
            }, title: "Switch to this repo", children: [_jsx("div", { style: {
                        fontFamily: "var(--mono)",
                        fontSize: 11,
                        color: "var(--ink1)",
                        fontWeight: 500,
                    }, children: p.label }), _jsx("div", { style: {
                        fontFamily: "var(--mono)",
                        fontSize: 10,
                        color: "var(--ink3)",
                        textOverflow: "ellipsis",
                        overflow: "hidden",
                        whiteSpace: "nowrap",
                    }, children: p.repo_root }), _jsxs("div", { style: {
                        fontFamily: "var(--mono)",
                        fontSize: 10,
                        color: "var(--ink2)",
                        marginTop: 2,
                    }, children: [p.entry_count, " entries"] })] }, p.repo_root)))) }));
}
function Panel({ title, subtitle, children, }) {
    return (_jsxs("div", { style: {
            border: "1px solid var(--border)",
            background: "var(--surface)",
            borderRadius: 4,
            padding: "10px 12px",
            display: "flex",
            flexDirection: "column",
            minHeight: 280,
        }, children: [_jsxs("div", { style: {
                    display: "flex",
                    alignItems: "baseline",
                    justifyContent: "space-between",
                    marginBottom: 6,
                }, children: [_jsx("div", { style: {
                            fontFamily: "var(--mono)",
                            fontSize: 11,
                            letterSpacing: "0.06em",
                            color: "var(--ink1)",
                            fontWeight: 600,
                        }, children: title }), subtitle ? (_jsx("div", { style: {
                            fontFamily: "var(--mono)",
                            fontSize: 10,
                            color: "var(--ink3)",
                        }, children: subtitle })) : null] }), _jsx("div", { style: { overflow: "auto", flex: 1 }, children: children })] }));
}
function Empty({ hint }) {
    return (_jsx("div", { style: {
            fontFamily: "var(--mono)",
            fontSize: 11,
            color: "var(--ink3)",
            padding: "16px 0",
        }, children: hint }));
}
function relTime(value) {
    if (!value)
        return "—";
    const t = new Date(value).getTime();
    if (Number.isNaN(t))
        return "—";
    const delta = Date.now() - t;
    if (delta < 60000)
        return "just now";
    if (delta < 3600000)
        return `${Math.floor(delta / 60000)}m ago`;
    if (delta < 86400000)
        return `${Math.floor(delta / 3600000)}h ago`;
    return `${Math.floor(delta / 86400000)}d ago`;
}
