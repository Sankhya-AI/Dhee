import { jsx as _jsx, jsxs as _jsxs } from "react/jsx-runtime";
import { useEffect, useMemo, useState } from "react";
import { api } from "../api";
const EMPTY = {
    live: false,
    proposals: [],
    findings: [],
    conflicts: [],
    totals: { proposals: 0, findings: 0, conflicts: 0 },
};
function severityColor(severity) {
    if (severity === "high")
        return "var(--rose)";
    if (severity === "medium")
        return "var(--accent)";
    return "var(--indigo)";
}
function proposalSnippet(proposal) {
    const content = proposal.summary || proposal.content || "";
    return content.length > 260 ? `${content.slice(0, 260)}...` : content;
}
export function ConflictView({ viewer, onChanged }) {
    const [snapshot, setSnapshot] = useState(EMPTY);
    const [selected, setSelected] = useState(null);
    const [busy, setBusy] = useState(null);
    const [error, setError] = useState(null);
    const loadInbox = async () => {
        try {
            const box = await api.inbox(viewer?.team_id ? { team: viewer.team_id, user: viewer.user_id } : { user: viewer?.user_id });
            setSnapshot(box);
            setSelected((current) => {
                if (current)
                    return current;
                return (box.proposals?.[0]?.context_id ||
                    box.findings?.[0]?.finding_id ||
                    String(box.conflicts?.[0]?.id || "") ||
                    null);
            });
        }
        catch (exc) {
            setError(exc instanceof Error ? exc.message : String(exc));
        }
    };
    useEffect(() => {
        void loadInbox();
        const timer = window.setInterval(() => void loadInbox(), 6000);
        return () => window.clearInterval(timer);
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [viewer?.team_id, viewer?.user_id]);
    const totalOpen = useMemo(() => (snapshot.totals?.proposals || 0) +
        (snapshot.totals?.findings || 0) +
        (snapshot.totals?.conflicts || 0), [snapshot.totals]);
    const decideProposal = async (proposal, decision) => {
        setBusy(`${decision}:${proposal.context_id}`);
        setError(null);
        try {
            if (decision === "approve") {
                await api.approveProposal(proposal.context_id, viewer?.user_id || "manager");
            }
            else {
                await api.rejectProposal(proposal.context_id, viewer?.user_id || "manager");
            }
            await loadInbox();
            await onChanged?.();
        }
        catch (exc) {
            setError(exc instanceof Error ? exc.message : String(exc));
        }
        finally {
            setBusy(null);
        }
    };
    const resolveFinding = async (finding) => {
        setBusy(`finding:${finding.finding_id}`);
        setError(null);
        try {
            await api.resolveFinding(finding.finding_id, viewer?.user_id || "manager");
            await loadInbox();
            await onChanged?.();
        }
        catch (exc) {
            setError(exc instanceof Error ? exc.message : String(exc));
        }
        finally {
            setBusy(null);
        }
    };
    const resolveConflict = async (conflict, action) => {
        const id = String(conflict.id || "");
        if (!id)
            return;
        setBusy(`conflict:${id}:${action}`);
        setError(null);
        try {
            await api.resolveConflictDetailed(id, { action });
            await loadInbox();
            await onChanged?.();
        }
        catch (exc) {
            setError(exc instanceof Error ? exc.message : String(exc));
        }
        finally {
            setBusy(null);
        }
    };
    return (_jsxs("div", { style: { display: "flex", height: "100%", minHeight: 0 }, children: [_jsxs("aside", { style: {
                    width: 300,
                    borderRight: "1px solid var(--border)",
                    background: "white",
                    padding: 16,
                    overflowY: "auto",
                    flexShrink: 0,
                }, children: [_jsx("div", { style: {
                            fontFamily: "var(--mono)",
                            fontSize: 10,
                            color: "var(--ink3)",
                            letterSpacing: "0.08em",
                            textTransform: "uppercase",
                        }, children: "Inbox" }), _jsxs("div", { style: { fontSize: 22, fontWeight: 650, marginTop: 6 }, children: [totalOpen, " open"] }), _jsxs("div", { style: {
                            marginTop: 10,
                            display: "grid",
                            gap: 8,
                            fontFamily: "var(--mono)",
                            fontSize: 10,
                        }, children: [_jsx(SummaryRow, { label: "Proposals", value: snapshot.totals?.proposals || 0 }), _jsx(SummaryRow, { label: "Findings", value: snapshot.totals?.findings || 0 }), _jsx(SummaryRow, { label: "Conflicts", value: snapshot.totals?.conflicts || 0 })] }), _jsx("div", { style: {
                            marginTop: 18,
                            padding: 12,
                            border: "1px solid var(--border)",
                            background: "var(--surface)",
                            fontSize: 12,
                            lineHeight: 1.5,
                            color: "var(--ink2)",
                        }, children: "Review context changes, stale-context findings, and memory conflicts from one queue. Approvals activate context used by routing." }), error ? (_jsx("div", { style: {
                            marginTop: 12,
                            padding: 10,
                            border: "1px solid var(--rose)",
                            background: "var(--rose-dim)",
                            color: "var(--rose)",
                            fontFamily: "var(--mono)",
                            fontSize: 10,
                            lineHeight: 1.5,
                        }, children: error })) : null] }), _jsxs("main", { style: {
                    flex: 1,
                    minWidth: 0,
                    overflowY: "auto",
                    background: "var(--bg)",
                    padding: 18,
                    display: "grid",
                    gap: 16,
                    alignContent: "start",
                }, children: [_jsx(InboxSection, { title: "Pending Proposals", count: snapshot.proposals.length, empty: "No context edits are waiting for approval.", children: snapshot.proposals.map((proposal) => (_jsxs("article", { onClick: () => setSelected(proposal.context_id), style: rowStyle(selected === proposal.context_id), children: [_jsxs("div", { style: rowHeaderStyle, children: [_jsxs("div", { children: [_jsx("div", { style: rowTitleStyle, children: proposal.title }), _jsxs("div", { style: rowMetaStyle, children: [proposal.proposed_by_user_id || "developer", " \u00B7 ", proposal.team_id || proposal.project_id || proposal.scope] })] }), _jsx(Badge, { color: "var(--accent)", children: "pending" })] }), _jsx("p", { style: snippetStyle, children: proposalSnippet(proposal) || "No preview available." }), _jsxs("div", { style: { display: "flex", gap: 8, justifyContent: "flex-end" }, children: [_jsx(QueueButton, { label: "Open in Context", onClick: (e) => {
                                                e.stopPropagation();
                                                window.location.hash = `#vault/item/${proposal.context_id}`;
                                                window.history.replaceState(null, "", `?view=context${window.location.hash}`);
                                                window.dispatchEvent(new PopStateEvent("popstate"));
                                            } }), _jsx(QueueButton, { label: "Reject", color: "var(--rose)", busy: busy === `reject:${proposal.context_id}`, onClick: (e) => {
                                                e.stopPropagation();
                                                void decideProposal(proposal, "reject");
                                            } }), _jsx(QueueButton, { label: "Approve", color: "var(--green)", busy: busy === `approve:${proposal.context_id}`, onClick: (e) => {
                                                e.stopPropagation();
                                                void decideProposal(proposal, "approve");
                                            } })] })] }, proposal.context_id))) }), _jsx(InboxSection, { title: "Manager Findings", count: snapshot.findings.length, empty: "No stale, low-quality, or duplicate context findings.", children: snapshot.findings.map((finding) => (_jsxs("article", { onClick: () => setSelected(finding.finding_id), style: rowStyle(selected === finding.finding_id), children: [_jsxs("div", { style: rowHeaderStyle, children: [_jsxs("div", { children: [_jsx("div", { style: rowTitleStyle, children: finding.title }), _jsxs("div", { style: rowMetaStyle, children: [finding.team_id, " \u00B7 ", finding.finding_type] })] }), _jsx(Badge, { color: severityColor(finding.severity), children: finding.severity })] }), _jsx("p", { style: snippetStyle, children: finding.detail }), _jsx("div", { style: { display: "flex", justifyContent: "flex-end" }, children: _jsx(QueueButton, { label: "Resolve", color: "var(--green)", busy: busy === `finding:${finding.finding_id}`, onClick: (e) => {
                                            e.stopPropagation();
                                            void resolveFinding(finding);
                                        } }) })] }, finding.finding_id))) }), _jsx(InboxSection, { title: "Memory Conflicts", count: snapshot.conflicts.length, empty: "No memory contradictions detected.", children: snapshot.conflicts.map((conflict) => {
                            const c = conflict;
                            const id = String(c.id || Math.random());
                            return (_jsxs("article", { onClick: () => setSelected(id), style: rowStyle(selected === id), children: [_jsxs("div", { style: rowHeaderStyle, children: [_jsxs("div", { children: [_jsx("div", { style: rowTitleStyle, children: "Memory conflict" }), _jsx("div", { style: rowMetaStyle, children: c.reason || "Contradiction" })] }), _jsx(Badge, { color: severityColor(c.severity), children: c.severity || "open" })] }), _jsxs("div", { style: { display: "grid", gap: 6, marginTop: 10 }, children: [_jsx(ConflictQuote, { label: "A", text: c.belief_a?.content }), _jsx(ConflictQuote, { label: "B", text: c.belief_b?.content })] }), _jsx("div", { style: { display: "flex", gap: 8, justifyContent: "flex-end", marginTop: 10 }, children: ["KEEP A", "KEEP B", "MERGE"].map((action) => (_jsx(QueueButton, { label: action, busy: busy === `conflict:${id}:${action}`, onClick: (e) => {
                                                e.stopPropagation();
                                                void resolveConflict(conflict, action);
                                            } }, action))) })] }, id));
                        }) })] })] }));
}
function SummaryRow({ label, value }) {
    return (_jsxs("div", { style: { display: "flex", justifyContent: "space-between" }, children: [_jsx("span", { style: { color: "var(--ink3)" }, children: label }), _jsx("span", { style: { color: value ? "var(--accent)" : "var(--ink2)" }, children: value })] }));
}
function InboxSection({ title, count, empty, children, }) {
    return (_jsxs("section", { style: { display: "grid", gap: 10 }, children: [_jsxs("div", { style: {
                    display: "flex",
                    alignItems: "center",
                    gap: 8,
                    fontFamily: "var(--mono)",
                    fontSize: 10,
                    letterSpacing: "0.08em",
                    color: "var(--ink3)",
                    textTransform: "uppercase",
                }, children: [_jsx("span", { children: title }), _jsx(Badge, { children: count })] }), count === 0 ? (_jsx("div", { style: {
                    border: "1px dashed var(--border)",
                    background: "white",
                    color: "var(--ink3)",
                    padding: 16,
                    fontSize: 12,
                }, children: empty })) : (children)] }));
}
function Badge({ children, color = "var(--ink3)", }) {
    return (_jsx("span", { style: {
            display: "inline-flex",
            alignItems: "center",
            padding: "2px 7px",
            border: `1px solid ${color}`,
            color,
            background: "white",
            borderRadius: 3,
            fontFamily: "var(--mono)",
            fontSize: 9,
        }, children: children }));
}
function QueueButton({ label, onClick, color = "var(--ink2)", busy, }) {
    return (_jsx("button", { onClick: onClick, disabled: busy, style: {
            padding: "6px 9px",
            border: `1px solid ${color}`,
            color,
            background: "white",
            fontFamily: "var(--mono)",
            fontSize: 9,
            borderRadius: 3,
            cursor: busy ? "wait" : "pointer",
        }, children: busy ? "..." : label }));
}
function ConflictQuote({ label, text }) {
    return (_jsxs("div", { style: {
            border: "1px solid var(--border)",
            background: "var(--surface)",
            padding: 10,
            display: "grid",
            gridTemplateColumns: "20px minmax(0, 1fr)",
            gap: 8,
        }, children: [_jsx("span", { style: { fontFamily: "var(--mono)", color: "var(--ink3)" }, children: label }), _jsx("span", { style: { color: "var(--ink2)", fontSize: 12, lineHeight: 1.5 }, children: text || "No content" })] }));
}
const rowHeaderStyle = {
    display: "flex",
    justifyContent: "space-between",
    alignItems: "flex-start",
    gap: 12,
};
const rowTitleStyle = {
    fontSize: 15,
    fontWeight: 650,
    color: "var(--ink)",
};
const rowMetaStyle = {
    fontFamily: "var(--mono)",
    fontSize: 10,
    color: "var(--ink3)",
    marginTop: 3,
};
const snippetStyle = {
    margin: "10px 0",
    color: "var(--ink2)",
    fontSize: 12,
    lineHeight: 1.55,
};
function rowStyle(active) {
    return {
        border: `1px solid ${active ? "var(--accent)" : "var(--border)"}`,
        background: "white",
        padding: 14,
        boxShadow: active ? "0 10px 24px rgba(20,16,10,0.06)" : "none",
        cursor: "pointer",
    };
}
