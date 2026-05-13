import { jsx as _jsx, jsxs as _jsxs } from "react/jsx-runtime";
import { useEffect, useMemo, useRef, useState } from "react";
import { api } from "../api";
import { OrgDrawer } from "../components/OrgDrawer";
import { layoutTree } from "../components/canvas/treeLayout";
import { RepoBrainHeader } from "./ProductViews";
// Card sizes for the local-first context map.
const CARD = {
    workspace: { w: 280, h: 76 },
    project: { w: 200, h: 64 },
    team: { w: 180, h: 56 },
    global_team: { w: 200, h: 60 },
    repo: { w: 180, h: 52 },
    folder: { w: 230, h: 70 },
    session: { w: 190, h: 58 },
    integration: { w: 150, h: 46 },
    default: { w: 160, h: 50 },
};
function cardSize(type) {
    if (CARD[type])
        return CARD[type];
    if (type.startsWith("integration:"))
        return CARD.integration;
    return CARD.default;
}
function runtimeOf(node) {
    return String((node.meta || {}).runtime || "").toLowerCase();
}
function runtimeStroke(runtime) {
    if (runtime === "codex")
        return "var(--indigo)";
    if (runtime === "claude-code" || runtime === "claude")
        return "var(--accent)";
    return "var(--ink3)";
}
function runtimeFill(runtime) {
    if (runtime === "codex")
        return "var(--indigo-dim)";
    if (runtime === "claude-code" || runtime === "claude")
        return "var(--accent-dim)";
    return "var(--surface)";
}
function strokeFor(type, health) {
    if (health === "needs_work")
        return "var(--rose)";
    if (health === "watch")
        return "var(--accent)";
    if (type === "folder")
        return "var(--green)";
    if (type === "session")
        return "var(--accent)";
    if (type === "workspace")
        return "var(--ink2)";
    if (type === "project")
        return "var(--accent)";
    if (type === "team")
        return "var(--green)";
    if (type === "global_team")
        return "var(--indigo)";
    if (type === "repo")
        return "var(--green)";
    if (type === "integration:slack")
        return "var(--indigo)";
    if (type === "integration:docs")
        return "var(--accent)";
    if (type === "integration:email")
        return "var(--rose)";
    if (type === "integration:git")
        return "var(--green)";
    return "var(--ink3)";
}
function strokeForNode(node) {
    if (node.health === "needs_work")
        return "var(--rose)";
    if (node.type === "session")
        return runtimeStroke(runtimeOf(node));
    return strokeFor(node.type, node.health);
}
function fillFor(type) {
    if (type === "workspace")
        return "white";
    if (type === "folder")
        return "var(--green-dim)";
    if (type === "session")
        return "var(--surface)";
    if (type === "project")
        return "var(--accent-dim)";
    if (type === "team" || type === "global_team")
        return "var(--surface)";
    if (type === "repo")
        return "var(--green-dim)";
    if (type.startsWith("integration:"))
        return "var(--surface)";
    return "var(--surface)";
}
function fillForNode(node) {
    if (node.type === "session")
        return runtimeFill(runtimeOf(node));
    return fillFor(node.type);
}
export function OrgCanvas({ graph, viewer, onOpenVault, onOpenSession, onChanged, }) {
    const containerRef = useRef(null);
    const panRef = useRef({
        down: false,
        lastX: 0,
        lastY: 0,
    });
    const userViewportDirtyRef = useRef(false);
    const lastAutoFitRef = useRef(null);
    const [size, setSize] = useState({ w: 1200, h: 700 });
    const [tx, setTx] = useState(0);
    const [ty, setTy] = useState(0);
    const [scale, setScale] = useState(1);
    const [selected, setSelected] = useState(null);
    const [hover, setHover] = useState(null);
    const [busy, setBusy] = useState(null);
    const [manualPath, setManualPath] = useState("");
    const [pathError, setPathError] = useState(null);
    const isLocalContext = graph.raw?.mode === "local_context";
    const addLocalFolder = async () => {
        setBusy("add-folder");
        setPathError(null);
        try {
            const picked = await api.pickFolderPath("Choose a folder to share context");
            if (picked.ok && picked.path) {
                await api.localContextAddFolder({ path: picked.path, shared: true });
                onChanged();
            }
        }
        catch (e) {
            setPathError(String(e));
        }
        finally {
            setBusy(null);
        }
    };
    const addManualFolder = async () => {
        const path = manualPath.trim();
        if (!path || busy)
            return;
        setBusy("manual-folder");
        setPathError(null);
        try {
            await api.localContextAddFolder({ path, shared: true });
            setManualPath("");
            onChanged();
        }
        catch (e) {
            setPathError(String(e));
        }
        finally {
            setBusy(null);
        }
    };
    // ─── ResizeObserver ─────────────────────────────────────────────────────
    useEffect(() => {
        const el = containerRef.current;
        if (!el)
            return;
        const update = () => {
            const r = el.getBoundingClientRect();
            const next = { w: Math.round(r.width), h: Math.round(r.height) };
            setSize((current) => current.w === next.w && current.h === next.h ? current : next);
        };
        update();
        const ro = new ResizeObserver(update);
        ro.observe(el);
        return () => ro.disconnect();
    }, []);
    // ─── Layout ─────────────────────────────────────────────────────────────
    const { positions, bounds } = useMemo(() => {
        const childIndex = new Map();
        for (const e of graph.edges) {
            if (e.kind !== "contains" && e.kind !== "uses")
                continue;
            const a = childIndex.get(e.source) || [];
            a.push(e.target);
            childIndex.set(e.source, a);
        }
        const parentOf = new Map();
        for (const e of graph.edges) {
            if (e.kind !== "contains" && e.kind !== "uses")
                continue;
            parentOf.set(e.target, e.source);
        }
        // Determine depth via BFS from workspace root(s).
        const depthOf = new Map();
        const roots = graph.nodes
            .filter((n) => n.type === "workspace" || !parentOf.has(n.id))
            .map((n) => n.id);
        const queue = roots.map((id) => ({
            id,
            depth: 0,
        }));
        while (queue.length) {
            const { id, depth } = queue.shift();
            if (depthOf.has(id))
                continue;
            depthOf.set(id, depth);
            for (const c of childIndex.get(id) || []) {
                queue.push({ id: c, depth: depth + 1 });
            }
        }
        // Anything not reached (orphan) lands at depth 0
        const inputs = graph.nodes.map((n) => {
            const sz = cardSize(n.type);
            return {
                id: n.id,
                parent: parentOf.get(n.id) || null,
                width: sz.w,
                height: sz.h,
                depth: depthOf.get(n.id) ?? 0,
            };
        });
        const out = layoutTree(inputs, { siblingGap: 24, levelGap: 90 });
        const positions = new Map();
        for (const n of out.nodes) {
            positions.set(n.id, { x: n.x, y: n.y, w: n.width, h: n.height });
        }
        return { positions, bounds: out.bounds };
    }, [graph.nodes, graph.edges]);
    const structureKey = useMemo(() => JSON.stringify({
        nodes: graph.nodes.map((n) => [n.id, n.type, n.label, n.health || ""]),
        edges: graph.edges.map((e) => [e.source, e.target, e.kind]),
    }), [graph.nodes, graph.edges]);
    const fitToBounds = () => {
        const w = bounds.maxX - bounds.minX;
        const h = bounds.maxY - bounds.minY;
        if (w <= 0 || h <= 0 || size.w <= 0 || size.h <= 0)
            return false;
        const padding = 80;
        const sx = (size.w - padding * 2) / w;
        const sy = (size.h - padding * 2) / h;
        const next = Math.min(1.2, Math.max(0.45, Math.min(sx, sy)));
        setScale(next);
        setTx(size.w / 2 - ((bounds.minX + bounds.maxX) / 2) * next);
        setTy(padding - bounds.minY * next);
        return true;
    };
    // Auto-fit once per real structure/size change. Polling can deliver fresh
    // objects with identical data; never let that snap a user's pan/zoom back.
    useEffect(() => {
        const sizeKey = `${size.w}x${size.h}`;
        const last = lastAutoFitRef.current;
        const graphChanged = last?.structure !== structureKey;
        const sizeChanged = last?.size !== sizeKey;
        if (!graphChanged && !sizeChanged)
            return;
        if (last && userViewportDirtyRef.current) {
            lastAutoFitRef.current = { structure: structureKey, size: sizeKey };
            return;
        }
        if (fitToBounds()) {
            lastAutoFitRef.current = { structure: structureKey, size: sizeKey };
        }
    }, [
        bounds.minX,
        bounds.maxX,
        bounds.minY,
        bounds.maxY,
        size.w,
        size.h,
        structureKey,
    ]);
    // ─── Pan / zoom ─────────────────────────────────────────────────────────
    const onWheel = (e) => {
        e.preventDefault();
        userViewportDirtyRef.current = true;
        const factor = Math.exp(-e.deltaY * 0.001);
        const next = Math.min(2.5, Math.max(0.3, scale * factor));
        const rect = containerRef.current?.getBoundingClientRect();
        if (!rect) {
            setScale(next);
            return;
        }
        const cx = e.clientX - rect.left;
        const cy = e.clientY - rect.top;
        setTx((x) => cx - ((cx - x) / scale) * next);
        setTy((y) => cy - ((cy - y) / scale) * next);
        setScale(next);
    };
    const onMouseDown = (e) => {
        panRef.current = { down: true, lastX: e.clientX, lastY: e.clientY };
    };
    const onMouseMove = (e) => {
        const p = panRef.current;
        if (!p.down)
            return;
        userViewportDirtyRef.current = true;
        setTx((x) => x + (e.clientX - p.lastX));
        setTy((y) => y + (e.clientY - p.lastY));
        panRef.current = { down: true, lastX: e.clientX, lastY: e.clientY };
    };
    const onMouseUp = () => {
        panRef.current.down = false;
    };
    const onDoubleClick = () => {
        userViewportDirtyRef.current = false;
        if (fitToBounds()) {
            lastAutoFitRef.current = {
                structure: structureKey,
                size: `${size.w}x${size.h}`,
            };
        }
    };
    // ─── Empty state ────────────────────────────────────────────────────────
    const hasContent = graph.totals.projects > 0 ||
        graph.totals.teams > 0 ||
        graph.totals.repos > 0 ||
        (graph.totals.folders || 0) > 0 ||
        (graph.totals.sessions || 0) > 0 ||
        graph.totals.context_items > 0;
    if (!graph.live || !hasContent) {
        return (_jsxs("div", { className: "dhee-canvas-bg", style: {
                position: "relative",
                flex: 1,
                display: "flex",
                flexDirection: "column",
                overflow: "hidden",
            }, children: [_jsx(RepoBrainHeader, { onOpenContext: () => onOpenVault() }), _jsx(EmptyState, { viewer: viewer, busy: busy, onAddFolder: addLocalFolder, manualPath: manualPath, onManualPathChange: setManualPath, onAddManualFolder: addManualFolder, error: pathError })] }));
    }
    // ─── Render ─────────────────────────────────────────────────────────────
    const isManager = viewer?.role === "manager" || viewer?.role === "admin";
    return (_jsxs("div", { ref: containerRef, onWheel: onWheel, onMouseDown: onMouseDown, onMouseMove: onMouseMove, onMouseUp: onMouseUp, onMouseLeave: onMouseUp, onDoubleClick: onDoubleClick, className: "dhee-canvas-bg", style: {
            position: "relative",
            flex: 1,
            cursor: panRef.current.down ? "grabbing" : "grab",
            overflow: "hidden",
        }, children: [_jsx(RepoBrainHeader, { onOpenContext: () => onOpenVault() }), _jsx("svg", { width: size.w, height: size.h, style: { display: "block", userSelect: "none" }, children: _jsxs("g", { transform: `translate(${tx},${ty}) scale(${scale})`, children: [graph.edges.map((e, idx) => {
                            const a = positions.get(e.source);
                            const b = positions.get(e.target);
                            if (!a || !b)
                                return null;
                            const x1 = a.x;
                            const y1 = a.y + a.h / 2;
                            const x2 = b.x;
                            const y2 = b.y - b.h / 2;
                            const my = (y1 + y2) / 2;
                            const isHover = hover && (e.source === hover || e.target === hover);
                            return (_jsx("path", { d: `M ${x1},${y1} C ${x1},${my} ${x2},${my} ${x2},${y2}`, className: isHover
                                    ? "dhee-edge-path dhee-edge-path--highlight"
                                    : hover
                                        ? "dhee-edge-path dhee-edge-path--dim"
                                        : "dhee-edge-path" }, `${e.source}-${e.target}-${idx}`));
                        }), graph.nodes.map((n) => {
                            const p = positions.get(n.id);
                            if (!p)
                                return null;
                            const stroke = strokeForNode(n);
                            const fill = fillForNode(n);
                            const isSelected = selected?.id === n.id;
                            return (_jsxs("g", { onMouseEnter: () => setHover(n.id), onMouseLeave: () => setHover(null), onClick: (e) => {
                                    e.stopPropagation();
                                    setSelected(n);
                                }, style: { cursor: "pointer" }, children: [_jsx("rect", { x: p.x - p.w / 2, y: p.y, width: p.w, height: p.h, rx: 8, ry: 8, fill: fill, stroke: stroke, strokeWidth: isSelected ? 2.4 : n.type === "workspace" ? 1.6 : 1.2 }), n.type === "workspace" ? (_jsx("text", { x: p.x, y: p.y + 24, textAnchor: "middle", fontFamily: "var(--mono)", fontSize: 9, letterSpacing: "0.12em", fill: "var(--ink3)", pointerEvents: "none", children: "WORKSPACE" })) : null, _jsx("text", { x: p.x, y: n.type === "workspace"
                                            ? p.y + 50
                                            : p.y + p.h / 2 + 4, textAnchor: "middle", fontFamily: "var(--font)", fontSize: n.type === "workspace" ? 16 : 12, fontWeight: n.type === "workspace" ? 500 : 400, fill: "var(--ink)", pointerEvents: "none", children: truncate(n.label, n.type === "workspace" ? 30 : 22) }), n.type !== "workspace" ? (_jsx("text", { x: p.x, y: p.y + 16, textAnchor: "middle", fontFamily: "var(--mono)", fontSize: 8, letterSpacing: "0.1em", fill: "var(--ink3)", pointerEvents: "none", children: nodeTypeLabel(n) })) : null] }, n.id));
                        })] }) }), isLocalContext ? (_jsxs("div", { className: "repo-brain-local-controls", style: {
                    position: "absolute",
                    left: 12,
                    top: 12,
                    display: "flex",
                    gap: 8,
                    alignItems: "center",
                    background: "var(--bg)",
                    border: "1px solid var(--border)",
                    borderRadius: 4,
                    padding: "6px 8px",
                    boxShadow: "0 4px 14px rgba(20,16,10,0.05)",
                }, children: [_jsx("button", { onClick: addLocalFolder, disabled: busy === "add-folder", style: {
                            fontFamily: "var(--mono)",
                            fontSize: 10,
                            border: "1px solid var(--accent)",
                            color: "var(--accent)",
                            background: "var(--accent-dim)",
                            borderRadius: 4,
                            padding: "5px 8px",
                            cursor: busy === "add-folder" ? "wait" : "pointer",
                        }, children: busy === "add-folder" ? "ADDING..." : "ADD FOLDER" }), _jsx("span", { style: { fontFamily: "var(--mono)", fontSize: 10, color: "var(--ink3)" }, children: "local context sharing" })] })) : null, _jsx(FooterStats, { graph: graph }), selected ? (_jsx(OrgDrawer, { node: selected, graph: graph, viewer: viewer, isManager: isManager, onClose: () => setSelected(null), onOpenVault: onOpenVault, onOpenSession: onOpenSession, onChanged: () => {
                    setSelected(null);
                    onChanged();
                } })) : null] }));
}
function nodeTypeLabel(node) {
    const t = node.type;
    if (t === "session") {
        const runtime = runtimeOf(node);
        if (runtime === "codex")
            return "CODEX";
        if (runtime === "claude-code" || runtime === "claude")
            return "CLAUDE CODE";
    }
    if (t.startsWith("integration:"))
        return t.split(":")[1].toUpperCase();
    return t.toUpperCase();
}
function truncate(s, max) {
    if (!s)
        return "";
    if (s.length <= max)
        return s;
    return s.slice(0, max - 1) + "…";
}
function FooterStats({ graph }) {
    if (graph.raw?.mode === "local_context") {
        return (_jsxs("div", { style: {
                position: "absolute",
                left: 12,
                bottom: 12,
                fontFamily: "var(--mono)",
                fontSize: 10,
                color: "var(--ink3)",
                background: "var(--bg)",
                border: "1px solid var(--border)",
                borderRadius: 4,
                padding: "5px 9px",
                letterSpacing: "0.04em",
            }, children: [graph.totals.folders || graph.totals.repos, " folders \u00B7", " ", graph.totals.sessions || graph.totals.teams, " sessions \u00B7", " ", graph.totals.shared_folders || 0, " shared"] }));
    }
    return (_jsxs("div", { style: {
            position: "absolute",
            left: 12,
            bottom: 12,
            fontFamily: "var(--mono)",
            fontSize: 10,
            color: "var(--ink3)",
            background: "var(--bg)",
            border: "1px solid var(--border)",
            borderRadius: 4,
            padding: "5px 9px",
            letterSpacing: "0.04em",
        }, children: [graph.totals.projects, " projects \u00B7 ", graph.totals.teams, " teams \u00B7", " ", graph.totals.repos, " folders \u00B7 ", graph.totals.pending_proposals, " pending"] }));
}
function EmptyState({ viewer, busy, onAddFolder, manualPath, onManualPathChange, onAddManualFolder, error, }) {
    return (_jsx("div", { className: "dhee-canvas-bg", style: {
            flex: 1,
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            padding: 32,
        }, children: _jsxs("div", { style: {
                width: "min(640px, calc(100vw - 64px))",
                padding: 24,
                background: "var(--bg)",
                border: "1px solid var(--border)",
                borderRadius: 8,
                boxShadow: "0 6px 18px rgba(20,16,10,0.05)",
                animation: "dhee-card-in 0.22s ease",
            }, children: [_jsx("div", { style: {
                        fontFamily: "var(--mono)",
                        fontSize: 9,
                        letterSpacing: "0.12em",
                        color: "var(--ink3)",
                        textTransform: "uppercase",
                        marginBottom: 6,
                    }, children: viewer?.user_id ? `local · ${viewer.user_id}` : "local context" }), _jsx("div", { style: {
                        fontSize: 18,
                        fontWeight: 500,
                        color: "var(--ink)",
                        marginBottom: 6,
                    }, children: "No local agent folders yet" }), _jsx("div", { style: {
                        fontSize: 12,
                        color: "var(--ink2)",
                        lineHeight: 1.5,
                        marginBottom: 14,
                    }, children: "Dhee will show local Claude Code and Codex sessions grouped by folder. Add any folder you want to share context with the rest of your local agent folders." }), _jsx("div", { style: { display: "flex", gap: 8, flexWrap: "wrap" }, children: _jsx("button", { onClick: onAddFolder, disabled: busy === "add-folder", style: {
                            fontFamily: "var(--mono)",
                            fontSize: 11,
                            padding: "8px 14px",
                            background: busy === "add-folder" ? "var(--surface)" : "var(--accent-dim)",
                            color: "var(--accent)",
                            border: "1px solid var(--accent)",
                            borderRadius: 4,
                            cursor: busy === "add-folder" ? "wait" : "pointer",
                        }, children: busy === "add-folder" ? "ADDING..." : "ADD FOLDER" }) }), _jsxs("div", { style: {
                        marginTop: 14,
                        borderTop: "1px solid var(--border)",
                        paddingTop: 14,
                        display: "grid",
                        gap: 8,
                    }, children: [_jsx("div", { style: { fontFamily: "var(--mono)", fontSize: 9, color: "var(--ink3)" }, children: "REPO PATH" }), _jsxs("div", { style: { display: "flex", gap: 8 }, children: [_jsx("input", { value: manualPath, onChange: (e) => onManualPathChange(e.target.value), onKeyDown: (e) => {
                                        if (e.key === "Enter")
                                            void onAddManualFolder();
                                    }, placeholder: "/Users/me/work/repo", style: {
                                        flex: 1,
                                        minWidth: 0,
                                        border: "1px solid var(--border)",
                                        borderRadius: 4,
                                        padding: "9px 10px",
                                        background: "white",
                                        fontFamily: "var(--mono)",
                                        fontSize: 11,
                                        color: "var(--ink)",
                                    } }), _jsx("button", { onClick: () => void onAddManualFolder(), disabled: !manualPath.trim() || busy === "manual-folder", style: {
                                        fontFamily: "var(--mono)",
                                        fontSize: 11,
                                        padding: "8px 12px",
                                        background: "white",
                                        color: "var(--accent)",
                                        border: "1px solid var(--border)",
                                        borderRadius: 4,
                                        opacity: !manualPath.trim() || busy === "manual-folder" ? 0.55 : 1,
                                        cursor: !manualPath.trim() || busy === "manual-folder" ? "not-allowed" : "pointer",
                                    }, children: busy === "manual-folder" ? "LINKING..." : "LINK PATH" })] }), _jsx("code", { style: {
                                display: "block",
                                border: "1px solid var(--border)",
                                background: "var(--surface)",
                                borderRadius: 4,
                                padding: "8px 9px",
                                fontFamily: "var(--mono)",
                                fontSize: 10,
                                color: "var(--ink2)",
                                overflowWrap: "anywhere",
                            }, children: "dhee onboard --root ." }), error ? _jsx("div", { style: { fontSize: 11, color: "var(--rose)" }, children: error }) : null] })] }) }));
}
