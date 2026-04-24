import { jsx as _jsx, jsxs as _jsxs } from "react/jsx-runtime";
import { memo } from "react";
// ---------------------------------------------------------------------------
// NodeCard — content-aware card rendered at a fixed position on the
// infinite canvas. One card per graph node. Rendering is
// content-conscious: workspace/project/session cards show rich meta;
// result/file/asset chips are deliberately compact so dozens of them
// still fit in a zoomed-out view.
//
// Styling principles (to match openswarm's premium feel):
//   - paper-white surface with a 1px tonal border
//   - type-coloured accent strip along the left edge
//   - subtle shadow at rest, stronger on hover/selection
//   - hover lift via transform (GPU-friendly, no repaint jitter)
// ---------------------------------------------------------------------------
export const TYPE_COLOR = {
    workspace: "#e06b3f",
    project: "#4d6cff",
    channel: "#1fa971",
    session: "#1a1a1a",
    task: "#0f9f55",
    result: "#0b8b5f",
    file: "#64748b",
    asset: "#d74b7b",
    broadcast: "#e08b3f",
};
const TYPE_LABEL = {
    workspace: "Workspace",
    project: "Project",
    channel: "Channel",
    session: "Session",
    task: "Task",
    result: "Tool result",
    file: "File",
    asset: "Asset",
    broadcast: "Broadcast",
};
function accentFor(node) {
    return node.accent || TYPE_COLOR[node.type] || "#555";
}
function fmtTime(value) {
    if (!value)
        return "";
    const date = new Date(String(value));
    if (Number.isNaN(date.getTime()))
        return "";
    return date.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}
function clamp(text, max) {
    const trimmed = text.trim();
    if (trimmed.length <= max)
        return trimmed;
    return `${trimmed.slice(0, max - 1).trimEnd()}…`;
}
function RawNodeCard({ node, x, y, width, height, selected, dim, onSelect, onHover, entranceDelay = 0, }) {
    const accent = accentFor(node);
    const meta = (node.meta || {});
    const isCompact = height < 120;
    const type = node.type;
    const baseStyle = {
        position: "absolute",
        left: x,
        top: y,
        width,
        height,
        display: "flex",
        boxSizing: "border-box",
        background: "white",
        border: `1px solid ${selected ? accent : "rgba(20,16,10,0.12)"}`,
        borderLeft: `3px solid ${accent}`,
        borderRadius: 8,
        boxShadow: selected
            ? `0 10px 26px rgba(20,16,10,0.12), 0 0 0 3px ${accent}22`
            : "0 1px 2px rgba(20,16,10,0.04), 0 2px 10px rgba(20,16,10,0.04)",
        transition: "box-shadow 0.18s ease, border-color 0.18s ease, transform 0.18s ease, opacity 0.18s ease",
        cursor: "pointer",
        userSelect: "none",
        opacity: dim ? 0.32 : 1,
        willChange: "transform",
        transform: "translate3d(0, 0, 0)",
        overflow: "hidden",
        // Slight entrance stagger — pure CSS keyframe set below.
        animation: `dhee-card-in 320ms ${entranceDelay}ms cubic-bezier(0.17, 0.67, 0.3, 1) both`,
    };
    const content = {
        flex: 1,
        padding: isCompact ? "10px 12px" : "12px 14px",
        display: "flex",
        flexDirection: "column",
        gap: isCompact ? 4 : 6,
        minWidth: 0,
    };
    const headerRow = {
        display: "flex",
        alignItems: "center",
        gap: 6,
    };
    const chip = (text, tone) => ({
        fontFamily: "var(--mono)",
        fontSize: 9,
        color: tone || "var(--ink3)",
        letterSpacing: 0.4,
        textTransform: "uppercase",
        lineHeight: 1.1,
        padding: "2px 6px",
        border: `1px solid ${tone || "var(--border)"}`,
        borderRadius: 2,
        whiteSpace: "nowrap",
        background: "white",
        ...(text ? {} : {}),
    });
    const titleStyle = {
        fontSize: isCompact ? 12 : 14,
        fontWeight: 600,
        lineHeight: 1.25,
        color: "var(--ink)",
        whiteSpace: "nowrap",
        overflow: "hidden",
        textOverflow: "ellipsis",
    };
    const bodyStyle = {
        fontSize: isCompact ? 11 : 12,
        color: "var(--ink2)",
        lineHeight: 1.5,
        display: "-webkit-box",
        WebkitLineClamp: isCompact ? 2 : 3,
        WebkitBoxOrient: "vertical",
        overflow: "hidden",
    };
    const monoMuted = {
        fontFamily: "var(--mono)",
        fontSize: 9,
        color: "var(--ink3)",
        letterSpacing: 0.3,
    };
    const typeLabel = TYPE_LABEL[type] || type;
    const runtime = String(meta.runtime || "");
    const state = String(meta.state || "");
    const ptr = String(meta.ptr || "");
    const tool = String(meta.toolName || meta.tool_name || "");
    const sourcePath = String(meta.sourcePath || meta.source_path || "");
    const model = String(meta.model || "");
    const harness = String(meta.harness || "");
    const sessionCount = Number(meta.sessionCount ?? 0);
    const projectCount = Number(meta.projectCount ?? 0);
    const taskCount = Number(meta.taskCount ?? 0);
    const messageCount = Number(meta.messageCount ?? 0);
    const updatedAt = meta.updatedAt || meta.last_seen_at;
    const onEnter = () => onHover(node);
    const onLeave = () => onHover(null);
    const onClick = (e) => {
        e.stopPropagation();
        onSelect(node);
    };
    return (_jsx("div", { style: baseStyle, onClick: onClick, onMouseEnter: onEnter, onMouseLeave: onLeave, "data-canvas-draggable": "false", "data-node-id": node.id, className: "dhee-node-card", children: _jsxs("div", { style: content, children: [_jsxs("div", { style: headerRow, children: [_jsx("span", { style: {
                                width: 8,
                                height: 8,
                                borderRadius: "50%",
                                background: accent,
                                flexShrink: 0,
                            } }), _jsx("span", { style: { ...monoMuted, color: accent }, children: typeLabel }), node.status ? _jsx("span", { style: chip(node.status), children: node.status }) : null, state ? _jsx("span", { style: chip(state), children: state }) : null, runtime ? _jsx("span", { style: chip(runtime), children: runtime }) : null, harness && !runtime ? _jsx("span", { style: chip(harness), children: harness }) : null, type === "session" && meta.isCurrent ? (_jsx("span", { style: {
                                width: 7,
                                height: 7,
                                borderRadius: "50%",
                                background: "var(--green)",
                                boxShadow: "0 0 0 3px rgba(31,169,113,0.22)",
                                marginLeft: "auto",
                            } })) : null] }), _jsx("div", { style: titleStyle, children: node.label || "(unnamed)" }), node.subLabel ? (_jsx("div", { style: { ...monoMuted, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }, children: node.subLabel })) : null, !isCompact && node.body ? _jsx("div", { style: bodyStyle, children: node.body }) : null, type === "workspace" && !isCompact ? (_jsxs("div", { style: { display: "flex", gap: 12, ...monoMuted }, children: [_jsxs("span", { children: [projectCount || "—", " projects"] }), _jsxs("span", { children: [sessionCount || "—", " sessions"] })] })) : null, type === "project" && !isCompact ? (_jsxs("div", { style: { display: "flex", gap: 12, ...monoMuted }, children: [_jsxs("span", { children: [sessionCount || "—", " sessions"] }), _jsxs("span", { children: [taskCount || "—", " tasks"] })] })) : null, type === "session" && !isCompact ? (_jsxs("div", { style: { display: "flex", gap: 12, ...monoMuted }, children: [model ? _jsx("span", { children: clamp(model, 22) }) : null, updatedAt ? _jsx("span", { children: fmtTime(updatedAt) }) : null] })) : null, type === "task" && !isCompact ? (_jsxs("div", { style: { display: "flex", gap: 12, ...monoMuted }, children: [messageCount ? _jsxs("span", { children: [messageCount, " messages"] }) : null, updatedAt ? _jsx("span", { children: fmtTime(updatedAt) }) : null] })) : null, type === "result" ? (_jsxs("div", { style: { display: "flex", gap: 8, alignItems: "center", ...monoMuted }, children: [tool ? _jsx("span", { children: tool }) : null, ptr ? _jsx("span", { children: ptr }) : null, sourcePath ? (_jsxs("span", { style: { overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }, children: ["\u00B7 ", sourcePath.split("/").pop()] })) : null] })) : null, type === "broadcast" ? (_jsxs("div", { style: { display: "flex", gap: 8, alignItems: "center", ...monoMuted }, children: [String(meta.sourceChannel || meta.sourceProject || "") ? (_jsxs("span", { children: ["from ", String(meta.sourceChannel || meta.sourceProject || "")] })) : null, String(meta.targetProject || "") ? (_jsxs("span", { children: ["\u2192 ", String(meta.targetProject)] })) : null] })) : null] }) }));
}
export const NodeCard = memo(RawNodeCard);
