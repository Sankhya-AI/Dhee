import { jsx as _jsx, jsxs as _jsxs } from "react/jsx-runtime";
const panelStyle = {
    border: "1px solid var(--border)",
    background: "var(--bg)",
    borderRadius: 8,
    padding: 18,
    width: "min(760px, 100%)",
    boxSizing: "border-box",
    display: "flex",
    flexWrap: "wrap",
    gap: 18,
    boxShadow: "0 10px 28px rgba(20,16,10,0.06)",
};
const monoCaps = {
    fontFamily: "var(--mono)",
    fontSize: 9,
    letterSpacing: "0.08em",
    textTransform: "uppercase",
    color: "var(--ink3)",
};
const actionBase = {
    borderRadius: 5,
    cursor: "pointer",
    fontFamily: "var(--mono)",
    fontSize: 10,
    padding: "8px 11px",
    whiteSpace: "nowrap",
};
function actionStyle(tone = "secondary", disabled) {
    const primary = tone === "primary";
    return {
        ...actionBase,
        border: `1px solid ${primary ? "var(--ink)" : "var(--border)"}`,
        background: primary ? "var(--ink)" : "white",
        color: primary ? "white" : "var(--accent)",
        opacity: disabled ? 0.55 : 1,
        cursor: disabled ? "not-allowed" : "pointer",
    };
}
export function FirstRunPanel({ title = "Set up a developer workspace", eyebrow = "First run", body = "Connect a repo folder, then start Codex or Claude Code from that folder so Dhee can mirror sessions and context.", actions = [], commands = [
    "dhee onboard --root .",
    "dhee doctor",
], aside, }) {
    return (_jsxs("section", { style: panelStyle, children: [_jsxs("div", { style: { flex: "1 1 300px", minWidth: 0 }, children: [_jsx("div", { style: monoCaps, children: eyebrow }), _jsx("h2", { style: {
                            margin: "5px 0 7px",
                            fontSize: 22,
                            lineHeight: 1.15,
                            color: "var(--ink)",
                            letterSpacing: 0,
                        }, children: title }), _jsx("div", { style: {
                            color: "var(--ink2)",
                            fontSize: 12.5,
                            lineHeight: 1.55,
                            maxWidth: 680,
                        }, children: body }), actions.length ? (_jsx("div", { style: { display: "flex", gap: 8, flexWrap: "wrap", marginTop: 14 }, children: actions.map((action) => (_jsx("button", { type: "button", onClick: action.onClick, disabled: action.disabled, style: actionStyle(action.tone, action.disabled), children: action.label }, action.label))) })) : null] }), _jsxs("div", { style: {
                    flex: "1 1 260px",
                    border: "1px solid var(--border)",
                    background: "var(--surface)",
                    borderRadius: 6,
                    padding: 12,
                    minWidth: 0,
                }, children: [_jsx("div", { style: { ...monoCaps, marginBottom: 8 }, children: "Terminal path" }), _jsx("div", { style: { display: "grid", gap: 7 }, children: commands.map((command) => (_jsx("code", { style: {
                                display: "block",
                                border: "1px solid var(--border)",
                                background: "white",
                                borderRadius: 4,
                                padding: "8px 9px",
                                fontFamily: "var(--mono)",
                                fontSize: 10,
                                color: "var(--ink)",
                                lineHeight: 1.45,
                                overflowWrap: "anywhere",
                            }, children: command }, command))) }), aside ? _jsx("div", { style: { marginTop: 10 }, children: aside }) : null] })] }));
}
