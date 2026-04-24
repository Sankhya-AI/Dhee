import { jsx as _jsx } from "react/jsx-runtime";
export function StatPill({ label, tone = "var(--ink3)", }) {
    return (_jsx("span", { style: {
            display: "inline-flex",
            alignItems: "center",
            gap: 5,
            padding: "2px 7px",
            border: `1px solid ${tone}`,
            color: tone,
            fontFamily: "var(--mono)",
            fontSize: 9,
            lineHeight: 1.2,
            whiteSpace: "nowrap",
        }, children: label }));
}
