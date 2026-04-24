import { jsx as _jsx, jsxs as _jsxs } from "react/jsx-runtime";
import { useState } from "react";
export function BrowserCard({ url, title, lines, }) {
    const [open, setOpen] = useState(true);
    return (_jsxs("div", { style: {
            border: "1px solid var(--border)",
            marginTop: 8,
            background: "white",
        }, children: [_jsxs("div", { onClick: () => setOpen((o) => !o), style: {
                    borderBottom: open ? "1px solid var(--border)" : "none",
                    padding: "6px 10px",
                    display: "flex",
                    alignItems: "center",
                    gap: 8,
                    background: "var(--surface)",
                    cursor: "pointer",
                }, children: [_jsx("span", { style: {
                            fontFamily: "var(--mono)",
                            fontSize: 9,
                            color: "var(--ink3)",
                            letterSpacing: 1,
                        }, children: "BROWSER" }), _jsx("span", { style: {
                            flex: 1,
                            fontFamily: "var(--mono)",
                            fontSize: 10,
                            color: "var(--ink2)",
                            overflow: "hidden",
                            textOverflow: "ellipsis",
                            whiteSpace: "nowrap",
                        }, children: url }), _jsx("span", { style: { fontSize: 10, color: "var(--ink3)" }, children: open ? "▲" : "▼" })] }), open && (_jsxs("div", { style: { padding: "10px 12px" }, children: [_jsx("div", { style: { fontWeight: 600, fontSize: 13, marginBottom: 8 }, children: title }), lines.map((l, i) => (_jsxs("div", { style: { display: "flex", gap: 7, marginBottom: 3 }, children: [_jsx("span", { style: {
                                    color: "var(--accent)",
                                    fontFamily: "var(--mono)",
                                    fontSize: 9,
                                    marginTop: 2,
                                    flexShrink: 0,
                                }, children: "\u2192" }), _jsx("span", { style: {
                                    color: "var(--ink2)",
                                    fontSize: 12.5,
                                    lineHeight: 1.4,
                                }, children: l })] }, i)))] }))] }));
}
export function GrepCard({ query, files, }) {
    return (_jsxs("div", { style: { border: "1px solid var(--border)", marginTop: 8, background: "white" }, children: [_jsxs("div", { style: {
                    borderBottom: "1px solid var(--border)",
                    padding: "6px 10px",
                    display: "flex",
                    gap: 8,
                    alignItems: "center",
                    background: "var(--surface)",
                }, children: [_jsx("span", { style: {
                            fontFamily: "var(--mono)",
                            fontSize: 9,
                            color: "var(--ink3)",
                            letterSpacing: 1,
                        }, children: "GREP" }), _jsxs("span", { style: {
                            fontFamily: "var(--mono)",
                            fontSize: 10,
                            color: "var(--accent)",
                        }, children: ["\"", query, "\""] }), _jsxs("span", { style: {
                            marginLeft: "auto",
                            fontFamily: "var(--mono)",
                            fontSize: 9,
                            color: "var(--ink3)",
                        }, children: [files.length, " matches"] })] }), _jsx("div", { style: { fontFamily: "var(--mono)", fontSize: 11.5 }, children: files.map((f, i) => (_jsxs("div", { style: {
                        padding: "8px 12px",
                        borderBottom: i < files.length - 1 ? "1px solid var(--surface2)" : "none",
                    }, children: [_jsxs("div", { style: { marginBottom: 4 }, children: [_jsx("span", { style: { color: "var(--indigo)", fontWeight: 500 }, children: f.name }), _jsxs("span", { style: { color: "var(--ink3)", marginLeft: 4 }, children: [":", f.line] })] }), _jsxs("div", { style: {
                                paddingLeft: 10,
                                borderLeft: "2px solid var(--border)",
                            }, children: [_jsx("span", { style: { color: "var(--ink)" }, children: f.match }), f.note && (_jsx("span", { style: { color: "var(--accent)", marginLeft: 4 }, children: f.note }))] })] }, i))) })] }));
}
export function CodeCard({ lang, lines, }) {
    return (_jsxs("div", { style: {
            border: "1px solid var(--border)",
            marginTop: 8,
            background: "oklch(0.1 0.01 260)",
        }, children: [_jsxs("div", { style: {
                    borderBottom: "1px solid oklch(0.2 0.01 260)",
                    padding: "5px 12px",
                    display: "flex",
                    gap: 8,
                    background: "oklch(0.12 0.01 260)",
                }, children: [_jsx("span", { style: {
                            fontFamily: "var(--mono)",
                            fontSize: 9,
                            color: "oklch(0.5 0.01 260)",
                            letterSpacing: 1,
                        }, children: "CODE" }), _jsx("span", { style: {
                            fontFamily: "var(--mono)",
                            fontSize: 9,
                            color: "var(--accent)",
                        }, children: lang })] }), _jsx("div", { style: {
                    padding: "10px 14px",
                    fontFamily: "var(--mono)",
                    fontSize: 12,
                    lineHeight: 1.7,
                }, children: lines.map((l, i) => (_jsx("div", { style: {
                        color: l.c === "comment"
                            ? "oklch(0.5 0.01 260)"
                            : l.c === "bad"
                                ? "var(--rose)"
                                : l.c === "good"
                                    ? "var(--green-mid)"
                                    : "oklch(0.88 0.01 260)",
                    }, children: l.t || " " }, i))) })] }));
}
export function DocumentCard({ title, lines, }) {
    return (_jsxs("div", { style: {
            border: "1px solid var(--border)",
            marginTop: 8,
            background: "white",
        }, children: [_jsxs("div", { style: {
                    borderBottom: "1px solid var(--border)",
                    padding: "6px 12px",
                    display: "flex",
                    gap: 8,
                    alignItems: "center",
                    background: "var(--surface)",
                }, children: [_jsx("span", { style: {
                            fontFamily: "var(--mono)",
                            fontSize: 9,
                            color: "var(--ink3)",
                            letterSpacing: 1,
                        }, children: "DOCUMENT" }), _jsx("span", { style: {
                            fontSize: 11,
                            fontWeight: 500,
                            fontFamily: "var(--mono)",
                            color: "var(--ink2)",
                        }, children: title })] }), _jsx("div", { style: { padding: "12px 16px", fontSize: 13, lineHeight: 1.65 }, children: lines.map((l, i) => typeof l === "object" && "h" in l ? (_jsx("div", { style: {
                        fontWeight: 700,
                        marginTop: i > 0 ? 12 : 0,
                        marginBottom: 3,
                        fontSize: 10.5,
                        textTransform: "uppercase",
                        letterSpacing: "0.06em",
                        color: "var(--ink2)",
                    }, children: l.h }, i)) : (_jsx("div", { style: { color: "var(--ink)" }, children: l }, i))) })] }));
}
export function LinkCard({ linkedTask, preview, tasks, onSelectTask, }) {
    const linked = tasks.find((t) => t.id === linkedTask);
    if (!linked)
        return null;
    const colorMap = {
        green: "var(--green)",
        indigo: "var(--indigo)",
        orange: "var(--accent)",
        rose: "var(--rose)",
    };
    const c = colorMap[linked.color] || "var(--accent)";
    return (_jsxs("div", { onClick: () => onSelectTask(linked.id), style: {
            border: `1px solid ${c}`,
            marginTop: 8,
            cursor: "pointer",
            display: "flex",
            gap: 12,
            padding: "9px 12px",
            background: "white",
            transition: "background 0.12s",
        }, onMouseEnter: (e) => (e.currentTarget.style.background = "var(--surface)"), onMouseLeave: (e) => (e.currentTarget.style.background = "white"), children: [_jsx("div", { style: {
                    width: 8,
                    height: 8,
                    background: c,
                    flexShrink: 0,
                    marginTop: 3,
                } }), _jsxs("div", { style: { flex: 1 }, children: [_jsx("div", { style: {
                            fontFamily: "var(--mono)",
                            fontSize: 9,
                            color: "var(--ink3)",
                            letterSpacing: 1,
                            marginBottom: 2,
                        }, children: "LINKED TASK" }), _jsx("div", { style: { fontWeight: 500, fontSize: 13 }, children: linked.title }), _jsx("div", { style: { fontSize: 11, color: "var(--ink2)", marginTop: 2 }, children: preview })] }), _jsx("div", { style: { color: c, fontSize: 15, alignSelf: "center" }, children: "\u2192" })] }));
}
