import { jsx as _jsx } from "react/jsx-runtime";
const cfg = {
    canonical: {
        bg: "oklch(0.95 0.07 85)",
        txt: "oklch(0.38 0.16 85)",
        label: "CANONICAL",
    },
    high: {
        bg: "oklch(0.94 0.06 265)",
        txt: "oklch(0.38 0.18 265)",
        label: "HIGH",
    },
    medium: {
        bg: "oklch(0.95 0.06 55)",
        txt: "oklch(0.42 0.14 55)",
        label: "MEDIUM",
    },
    "short-term": {
        bg: "oklch(0.94 0.01 260)",
        txt: "oklch(0.48 0.04 260)",
        label: "SHORT-TERM",
    },
    avoid: {
        bg: "oklch(0.96 0.05 10)",
        txt: "oklch(0.45 0.18 10)",
        label: "AVOID",
    },
};
export function TierBadge({ tier }) {
    const c = cfg[tier] ?? cfg["short-term"];
    return (_jsx("span", { style: {
            background: c.bg,
            color: c.txt,
            fontFamily: "var(--mono)",
            fontSize: 9,
            fontWeight: 700,
            padding: "2px 6px",
            letterSpacing: "0.08em",
            flexShrink: 0,
        }, children: c.label }));
}
