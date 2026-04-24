import { jsx as _jsx, jsxs as _jsxs } from "react/jsx-runtime";
import { BrowserCard, CodeCard, DocumentCard, GrepCard, LinkCard, } from "./cards/Cards";
export function ChatMessage({ msg, tasks, onSelectTask, }) {
    if (msg.role === "component") {
        const anyMsg = msg;
        return (_jsxs("div", { style: { marginBottom: 14, paddingLeft: 14 }, children: [anyMsg.type === "browser" && (_jsx(BrowserCard, { url: anyMsg.url, title: anyMsg.title, lines: anyMsg.lines })), anyMsg.type === "grep" && (_jsx(GrepCard, { query: anyMsg.query, files: anyMsg.files })), anyMsg.type === "code" && (_jsx(CodeCard, { lang: anyMsg.lang, lines: anyMsg.lines })), anyMsg.type === "document" && (_jsx(DocumentCard, { title: anyMsg.title, lines: anyMsg.lines })), anyMsg.type === "link" && (_jsx(LinkCard, { linkedTask: anyMsg.linkedTask, preview: anyMsg.preview, tasks: tasks, onSelectTask: onSelectTask }))] }));
    }
    const isUser = msg.role === "user";
    return (_jsxs("div", { style: {
            marginBottom: 13,
            display: "flex",
            flexDirection: "column",
            alignItems: isUser ? "flex-end" : "flex-start",
        }, children: [!isUser && (_jsxs("div", { style: {
                    display: "flex",
                    alignItems: "center",
                    gap: 5,
                    marginBottom: 4,
                }, children: [_jsx("div", { style: {
                            width: 5,
                            height: 5,
                            background: "var(--green)",
                            borderRadius: "50%",
                        } }), _jsx("span", { style: {
                            fontFamily: "var(--mono)",
                            fontSize: 9,
                            color: "var(--ink3)",
                            letterSpacing: 1,
                        }, children: "AGENT" })] })), _jsx("div", { style: {
                    maxWidth: "88%",
                    padding: "9px 13px",
                    background: isUser ? "var(--ink)" : "white",
                    color: isUser ? "var(--bg)" : "var(--ink)",
                    border: isUser ? "none" : "1px solid var(--border)",
                    fontSize: 13.5,
                    lineHeight: 1.6,
                    whiteSpace: "pre-wrap",
                }, children: msg.content })] }));
}
