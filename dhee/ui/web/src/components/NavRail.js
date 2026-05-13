import { jsx as _jsx, jsxs as _jsxs } from "react/jsx-runtime";
export function NavRail({ view, setView, conflictCount, }) {
    const items = [
        { id: "command", icon: "⌂", label: "HOME", tip: "Command center · current truth · next action" },
        { id: "router", icon: "⇌", label: "FIREWALL", tip: "Context firewall · routing · expansions · tokens saved" },
        { id: "canvas", icon: "⊞", label: "BRAIN", tip: "Repo Brain · linked folders · active sessions" },
        { id: "handoff", icon: "↗", label: "HANDOFF", tip: "Resume state across agents" },
        { id: "replay", icon: "◌", label: "REPLAY", tip: "Context decision replay" },
        { id: "learnings", icon: "↑", label: "LEARN", tip: "Evidence-backed learning review" },
        { id: "context", icon: "◐", label: "CONTEXT", tip: "Context vault · personal and shared memory" },
        { id: "portability", icon: "□", label: "PACKS", tip: ".dheemem export · import dry-run · portability" },
        {
            id: "conflicts",
            icon: "⟷",
            label: "INBOX",
            tip: "Proposals · findings · conflicts",
            badge: conflictCount,
        },
    ];
    return (_jsxs("div", { style: {
            width: "var(--nav)",
            borderRight: "1px solid var(--border)",
            display: "flex",
            flexDirection: "column",
            flexShrink: 0,
            background: "var(--bg)",
            zIndex: 20,
        }, children: [_jsx("div", { style: {
                    height: 48,
                    borderBottom: "1px solid var(--border)",
                    display: "flex",
                    alignItems: "center",
                    justifyContent: "center",
                }, children: _jsx("img", { src: "/dhee-logo.png", alt: "Dhee", style: { width: 22, height: 22, objectFit: "contain" } }) }), _jsx("div", { style: {
                    flex: 1,
                    display: "flex",
                    flexDirection: "column",
                    padding: "6px 0",
                    gap: 0,
                }, children: items.map((item) => {
                    const active = item.id === "router"
                        ? view === "router" || view.startsWith("router/")
                        : view === item.id;
                    return (_jsxs("div", { title: item.tip, onClick: () => setView(item.id), style: {
                            position: "relative",
                            height: 44,
                            display: "flex",
                            flexDirection: "column",
                            alignItems: "center",
                            justifyContent: "center",
                            cursor: "pointer",
                            background: active ? "var(--surface)" : "transparent",
                            borderLeft: `2px solid ${active ? "var(--accent)" : "transparent"}`,
                            gap: 2,
                            transition: "all 0.1s",
                        }, onMouseEnter: (e) => {
                            if (!active)
                                e.currentTarget.style.background = "var(--surface)";
                        }, onMouseLeave: (e) => {
                            if (!active)
                                e.currentTarget.style.background = "transparent";
                        }, children: [_jsx("span", { style: {
                                    fontSize: 14,
                                    color: active ? "var(--accent)" : "var(--ink3)",
                                    lineHeight: 1,
                                }, children: item.icon }), _jsx("span", { style: {
                                    fontFamily: "var(--mono)",
                                    fontSize: 7,
                                    color: active ? "var(--accent)" : "var(--ink3)",
                                    letterSpacing: "0.04em",
                                }, children: item.label }), item.badge && item.badge > 0 ? (_jsx("div", { style: {
                                    position: "absolute",
                                    top: 6,
                                    right: 6,
                                    width: 14,
                                    height: 14,
                                    borderRadius: "50%",
                                    background: "var(--rose)",
                                    display: "flex",
                                    alignItems: "center",
                                    justifyContent: "center",
                                }, children: _jsx("span", { style: {
                                        fontFamily: "var(--mono)",
                                        fontSize: 8,
                                        color: "white",
                                        fontWeight: 700,
                                    }, children: item.badge }) })) : null] }, item.id));
                }) }), _jsx("div", { style: {
                    borderTop: "1px solid var(--border)",
                    height: 44,
                    display: "flex",
                    alignItems: "center",
                    justifyContent: "center",
                }, children: _jsx("div", { title: "Dhee active", style: {
                        width: 5,
                        height: 5,
                        borderRadius: "50%",
                        background: "var(--green)",
                    } }) })] }));
}
