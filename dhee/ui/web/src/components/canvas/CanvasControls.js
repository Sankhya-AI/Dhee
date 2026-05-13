import { jsx as _jsx, jsxs as _jsxs } from "react/jsx-runtime";
import { useState } from "react";
import { Minimap } from "./Minimap";
// ---------------------------------------------------------------------------
// CanvasControls — a floating panel stack in the bottom-right corner:
// minimap card (toggleable) + toolbar with zoom / fit / reset / minimap
// toggle. Matches openswarm's interaction grammar with plain SVG/HTML so
// we don't take a MUI dependency.
// ---------------------------------------------------------------------------
const ICON_SIZE = 14;
const buttonStyle = (active = false) => ({
    width: 26,
    height: 26,
    display: "flex",
    alignItems: "center",
    justifyContent: "center",
    padding: 0,
    border: 0,
    borderRadius: 4,
    background: active ? "rgba(224, 107, 63, 0.12)" : "transparent",
    color: active ? "var(--accent)" : "var(--ink3)",
    cursor: "pointer",
    transition: "background 0.14s ease, color 0.14s ease",
});
function MinusIcon() {
    return (_jsx("svg", { width: ICON_SIZE, height: ICON_SIZE, viewBox: "0 0 24 24", fill: "none", children: _jsx("path", { d: "M5 12h14", stroke: "currentColor", strokeWidth: 2, strokeLinecap: "round" }) }));
}
function PlusIcon() {
    return (_jsx("svg", { width: ICON_SIZE, height: ICON_SIZE, viewBox: "0 0 24 24", fill: "none", children: _jsx("path", { d: "M12 5v14M5 12h14", stroke: "currentColor", strokeWidth: 2, strokeLinecap: "round" }) }));
}
function FitIcon() {
    return (_jsx("svg", { width: ICON_SIZE, height: ICON_SIZE, viewBox: "0 0 24 24", fill: "none", children: _jsx("path", { d: "M4 9V5a1 1 0 0 1 1-1h4M15 4h4a1 1 0 0 1 1 1v4M20 15v4a1 1 0 0 1-1 1h-4M9 20H5a1 1 0 0 1-1-1v-4", stroke: "currentColor", strokeWidth: 1.8, strokeLinecap: "round" }) }));
}
function MapIcon() {
    return (_jsx("svg", { width: ICON_SIZE, height: ICON_SIZE, viewBox: "0 0 24 24", fill: "none", children: _jsx("path", { d: "M9 4 3 6v14l6-2 6 2 6-2V4l-6 2-6-2Z M9 4v14 M15 6v14", stroke: "currentColor", strokeWidth: 1.6, strokeLinejoin: "round" }) }));
}
function TidyIcon() {
    return (_jsxs("svg", { width: ICON_SIZE, height: ICON_SIZE, viewBox: "0 0 24 24", fill: "none", children: [_jsx("path", { d: "M12 3v3M12 18v3M3 12h3M18 12h3M5.6 5.6l2.1 2.1M16.3 16.3l2.1 2.1M5.6 18.4l2.1-2.1M16.3 7.7l2.1-2.1", stroke: "currentColor", strokeWidth: 1.8, strokeLinecap: "round" }), _jsx("circle", { cx: 12, cy: 12, r: 3, stroke: "currentColor", strokeWidth: 1.8 })] }));
}
export function CanvasControls({ zoom, actions, onFitToContent, onTidy, minimapProps, onMinimapPan, }) {
    const [minimapOpen, setMinimapOpen] = useState(true);
    const pct = Math.round(zoom * 100);
    const panelStyle = {
        background: "rgba(255,255,255,0.94)",
        backdropFilter: "blur(10px)",
        WebkitBackdropFilter: "blur(10px)",
        border: "1px solid rgba(20,16,10,0.12)",
        borderRadius: 10,
        boxShadow: "0 12px 32px rgba(20,16,10,0.08), 0 2px 8px rgba(20,16,10,0.04)",
    };
    return (_jsxs("div", { style: {
            position: "absolute",
            right: 16,
            bottom: 16,
            display: "flex",
            flexDirection: "column",
            alignItems: "flex-end",
            gap: 8,
            zIndex: 30,
            pointerEvents: "none",
        }, children: [minimapOpen && (_jsx("div", { style: {
                    ...panelStyle,
                    width: 220,
                    height: 154,
                    padding: 6,
                    overflow: "hidden",
                    pointerEvents: "auto",
                }, children: _jsx(Minimap, { ...minimapProps, onPan: onMinimapPan }) })), _jsxs("div", { style: {
                    ...panelStyle,
                    display: "flex",
                    alignItems: "center",
                    gap: 2,
                    padding: "4px 6px",
                    pointerEvents: "auto",
                }, children: [_jsx("button", { title: "Zoom out (\u2318\u2212)", "aria-label": "Zoom out", onClick: actions.zoomOut, style: buttonStyle(), onMouseEnter: (e) => (e.currentTarget.style.background = "rgba(20,16,10,0.05)"), onMouseLeave: (e) => (e.currentTarget.style.background = "transparent"), children: _jsx(MinusIcon, {}) }), _jsxs("button", { title: "Reset to 100% (\u23180)", "aria-label": "Reset zoom", onClick: actions.resetZoom, style: {
                            ...buttonStyle(),
                            width: 46,
                            fontFamily: "var(--mono)",
                            fontSize: 10,
                            color: "var(--ink2)",
                        }, onMouseEnter: (e) => (e.currentTarget.style.background = "rgba(20,16,10,0.05)"), onMouseLeave: (e) => (e.currentTarget.style.background = "transparent"), children: [pct, "%"] }), _jsx("button", { title: "Zoom in (\u2318+)", "aria-label": "Zoom in", onClick: actions.zoomIn, style: buttonStyle(), onMouseEnter: (e) => (e.currentTarget.style.background = "rgba(20,16,10,0.05)"), onMouseLeave: (e) => (e.currentTarget.style.background = "transparent"), children: _jsx(PlusIcon, {}) }), _jsx("div", { style: { width: 1, height: 14, background: "rgba(20,16,10,0.12)", margin: "0 4px" } }), _jsx("button", { title: "Fit to content", "aria-label": "Fit to content", onClick: onFitToContent, style: buttonStyle(), onMouseEnter: (e) => (e.currentTarget.style.background = "rgba(20,16,10,0.05)"), onMouseLeave: (e) => (e.currentTarget.style.background = "transparent"), children: _jsx(FitIcon, {}) }), _jsx("button", { title: "Tidy layout", "aria-label": "Tidy layout", onClick: onTidy, style: buttonStyle(), onMouseEnter: (e) => (e.currentTarget.style.background = "rgba(20,16,10,0.05)"), onMouseLeave: (e) => (e.currentTarget.style.background = "transparent"), children: _jsx(TidyIcon, {}) }), _jsx("div", { style: { width: 1, height: 14, background: "rgba(20,16,10,0.12)", margin: "0 4px" } }), _jsx("button", { title: minimapOpen ? "Hide minimap" : "Show minimap", "aria-label": "Toggle minimap", onClick: () => setMinimapOpen((v) => !v), style: buttonStyle(minimapOpen), onMouseEnter: (e) => {
                            if (!minimapOpen)
                                e.currentTarget.style.background = "rgba(20,16,10,0.05)";
                        }, onMouseLeave: (e) => {
                            if (!minimapOpen)
                                e.currentTarget.style.background = "transparent";
                        }, children: _jsx(MapIcon, {}) })] })] }));
}
