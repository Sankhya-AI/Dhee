import { jsx as _jsx, jsxs as _jsxs } from "react/jsx-runtime";
export function TweaksPanel({ tweaks, setTweaks, visible, }) {
    if (!visible)
        return null;
    const set = (k, v) => {
        const next = { ...tweaks, [k]: v };
        setTweaks(next);
    };
    return (_jsxs("div", { style: {
            position: "fixed",
            bottom: 20,
            right: 20,
            width: 236,
            border: "1px solid var(--border)",
            background: "white",
            zIndex: 1000,
            boxShadow: "0 8px 32px rgba(0,0,0,0.1)",
        }, children: [_jsx("div", { style: {
                    padding: "9px 14px",
                    borderBottom: "1px solid var(--border)",
                    fontFamily: "var(--mono)",
                    fontSize: 10,
                    fontWeight: 700,
                    letterSpacing: "0.06em",
                }, children: "TWEAKS" }), _jsxs("div", { style: { padding: "14px" }, children: [_jsxs("div", { style: { marginBottom: 14 }, children: [_jsx("div", { style: {
                                    fontFamily: "var(--mono)",
                                    fontSize: 9,
                                    color: "var(--ink3)",
                                    marginBottom: 6,
                                    textTransform: "uppercase",
                                }, children: "Accent hue" }), _jsx("input", { type: "range", min: "0", max: "360", value: tweaks.accentHue, onChange: (e) => {
                                    const h = e.target.value;
                                    set("accentHue", h);
                                    document.documentElement.style.setProperty("--accent", `oklch(0.64 0.18 ${h})`);
                                    document.documentElement.style.setProperty("--accent-dim", `oklch(0.97 0.04 ${h})`);
                                }, style: { width: "100%" } }), _jsxs("div", { style: {
                                    fontFamily: "var(--mono)",
                                    fontSize: 9,
                                    color: "var(--ink3)",
                                    marginTop: 2,
                                }, children: ["hue ", tweaks.accentHue, "\u00B0"] })] }), _jsxs("div", { style: { marginBottom: 14 }, children: [_jsx("div", { style: {
                                    fontFamily: "var(--mono)",
                                    fontSize: 9,
                                    color: "var(--ink3)",
                                    marginBottom: 6,
                                    textTransform: "uppercase",
                                }, children: "Compact nav" }), _jsx("button", { onClick: () => set("compactNav", !tweaks.compactNav), style: {
                                    padding: "4px 10px",
                                    border: "1px solid var(--border)",
                                    fontFamily: "var(--mono)",
                                    fontSize: 10,
                                    background: tweaks.compactNav ? "var(--ink)" : "transparent",
                                    color: tweaks.compactNav ? "var(--bg)" : "var(--ink)",
                                    cursor: "pointer",
                                }, children: tweaks.compactNav ? "ON" : "OFF" })] }), _jsxs("div", { style: { marginBottom: 14 }, children: [_jsx("div", { style: {
                                    fontFamily: "var(--mono)",
                                    fontSize: 9,
                                    color: "var(--ink3)",
                                    marginBottom: 6,
                                    textTransform: "uppercase",
                                }, children: "Canvas style" }), _jsx("div", { style: { display: "flex", gap: 5 }, children: ["dots", "grid"].map((s) => (_jsx("button", { onClick: () => set("canvasStyle", s), style: {
                                        padding: "4px 10px",
                                        border: "1px solid var(--border)",
                                        fontFamily: "var(--mono)",
                                        fontSize: 9,
                                        background: tweaks.canvasStyle === s ? "var(--ink)" : "transparent",
                                        color: tweaks.canvasStyle === s ? "var(--bg)" : "var(--ink)",
                                        cursor: "pointer",
                                    }, children: s }, s))) })] }), _jsxs("div", { children: [_jsx("div", { style: {
                                    fontFamily: "var(--mono)",
                                    fontSize: 9,
                                    color: "var(--ink3)",
                                    marginBottom: 6,
                                    textTransform: "uppercase",
                                }, children: "Timestamps" }), _jsx("button", { onClick: () => set("showTimestamps", !tweaks.showTimestamps), style: {
                                    padding: "4px 10px",
                                    border: "1px solid var(--border)",
                                    fontFamily: "var(--mono)",
                                    fontSize: 10,
                                    background: tweaks.showTimestamps ? "var(--ink)" : "transparent",
                                    color: tweaks.showTimestamps ? "var(--bg)" : "var(--ink)",
                                    cursor: "pointer",
                                }, children: tweaks.showTimestamps ? "ON" : "OFF" })] })] })] }));
}
