import { jsx as _jsx, jsxs as _jsxs } from "react/jsx-runtime";
export function SectionHeader({ label, sub, children, }) {
    const text = label || children;
    return (_jsxs("div", { style: {
            display: "flex",
            alignItems: "baseline",
            gap: 10,
            marginBottom: 12,
        }, children: [_jsx("span", { style: {
                    fontFamily: "var(--mono)",
                    fontSize: 9,
                    fontWeight: 700,
                    color: "var(--ink3)",
                    letterSpacing: "0.1em",
                    textTransform: "uppercase",
                }, children: text }), sub && (_jsx("span", { style: {
                    fontFamily: "var(--mono)",
                    fontSize: 9,
                    color: "var(--border2)",
                }, children: sub }))] }));
}
