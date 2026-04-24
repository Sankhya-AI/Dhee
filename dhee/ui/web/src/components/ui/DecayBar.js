import { jsx as _jsx, jsxs as _jsxs } from "react/jsx-runtime";
export function DecayBar({ decay, width = 56 }) {
    const color = decay > 0.8 ? "var(--green)" : decay > 0.5 ? "var(--accent)" : "var(--rose)";
    return (_jsxs("div", { style: { display: "flex", alignItems: "center", gap: 5 }, children: [_jsx("div", { style: {
                    width,
                    height: 3,
                    background: "var(--surface2)",
                    position: "relative",
                }, children: _jsx("div", { style: {
                        position: "absolute",
                        top: 0,
                        left: 0,
                        height: "100%",
                        width: `${decay * 100}%`,
                        background: color,
                    } }) }), _jsxs("span", { style: {
                    fontFamily: "var(--mono)",
                    fontSize: 9,
                    color: "var(--ink3)",
                }, children: [Math.round(decay * 100), "%"] })] }));
}
