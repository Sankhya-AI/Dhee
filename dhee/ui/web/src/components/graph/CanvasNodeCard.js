import { jsx as _jsx, jsxs as _jsxs } from "react/jsx-runtime";
import { StatPill } from "../ui/StatPill";
const TYPE_BG = {
    project: "oklch(0.99 0.02 75)",
    workspace: "oklch(0.99 0.01 250)",
    session: "oklch(0.98 0.02 145)",
    task: "white",
    result: "oklch(0.99 0.01 85)",
    file: "oklch(0.98 0.015 265)",
    asset: "oklch(0.99 0.02 20)",
};
export function CanvasNodeCard({ node, active, onClick, }) {
    const accent = node.accent || "var(--accent)";
    const meta = node.meta || {};
    const plan = Array.isArray(meta.plan) ? meta.plan : [];
    const tools = Array.isArray(meta.tools) ? meta.tools : [];
    return (_jsxs("div", { onClick: onClick, style: {
            width: node.type === "result" ? 220 : node.type === "file" ? 190 : 240,
            minHeight: node.type === "file" ? 84 : 122,
            background: TYPE_BG[node.type] || "white",
            border: `1.5px solid ${active ? accent : "var(--border)"}`,
            boxShadow: active ? `0 12px 32px color-mix(in oklch, ${accent} 18%, transparent)` : "0 6px 16px rgba(0,0,0,0.05)",
            cursor: onClick ? "pointer" : "default",
            transition: "border-color 0.14s ease, box-shadow 0.14s ease, transform 0.14s ease",
        }, children: [_jsx("div", { style: { height: 4, background: accent } }), _jsxs("div", { style: { padding: "12px 13px 11px" }, children: [_jsxs("div", { style: {
                            display: "flex",
                            alignItems: "flex-start",
                            justifyContent: "space-between",
                            gap: 10,
                            marginBottom: 6,
                        }, children: [_jsxs("div", { style: { minWidth: 0 }, children: [_jsx("div", { style: {
                                            fontSize: 12.5,
                                            fontWeight: 600,
                                            lineHeight: 1.35,
                                            marginBottom: 4,
                                        }, children: node.label }), node.subLabel && (_jsx("div", { style: {
                                            fontFamily: "var(--mono)",
                                            fontSize: 9,
                                            color: "var(--ink3)",
                                            lineHeight: 1.45,
                                        }, children: node.subLabel }))] }), _jsx(StatPill, { label: node.type, tone: accent })] }), node.body && (_jsx("div", { style: {
                            fontSize: 11.5,
                            color: "var(--ink2)",
                            lineHeight: 1.45,
                            marginBottom: 8,
                            whiteSpace: "pre-wrap",
                        }, children: node.body })), (plan.length > 0 || tools.length > 0 || node.status) && (_jsxs("div", { style: {
                            display: "flex",
                            flexWrap: "wrap",
                            gap: 6,
                            alignItems: "center",
                        }, children: [node.status && _jsx(StatPill, { label: node.status, tone: accent }), plan.length > 0 && _jsx(StatPill, { label: `${plan.length} plan items` }), tools.length > 0 && _jsx(StatPill, { label: `${tools.length} tool events` })] }))] })] }));
}
