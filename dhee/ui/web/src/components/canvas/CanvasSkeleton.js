import { jsx as _jsx, jsxs as _jsxs } from "react/jsx-runtime";
// ---------------------------------------------------------------------------
// CanvasSkeleton — shimmer placeholders shown while the graph loads.
// Mimics the hierarchical layout (one workspace, a row of projects, one
// cluster of children) so the transition to real content feels continuous
// rather than a swap.
// ---------------------------------------------------------------------------
function ShimmerCard({ width, height, delay = 0, }) {
    return (_jsx("div", { style: {
            width,
            height,
            borderRadius: 8,
            background: "linear-gradient(90deg, rgba(20,16,10,0.04) 0%, rgba(20,16,10,0.08) 50%, rgba(20,16,10,0.04) 100%)",
            backgroundSize: "200% 100%",
            animation: `dhee-shimmer 1.4s linear ${delay}ms infinite`,
            border: "1px solid rgba(20,16,10,0.06)",
            borderLeft: "3px solid rgba(20,16,10,0.12)",
        } }));
}
export function CanvasSkeleton() {
    return (_jsxs("div", { style: {
            position: "absolute",
            inset: 0,
            display: "flex",
            flexDirection: "column",
            alignItems: "center",
            justifyContent: "center",
            gap: 28,
            pointerEvents: "none",
        }, children: [_jsx(ShimmerCard, { width: 320, height: 140 }), _jsxs("div", { style: { display: "flex", gap: 40 }, children: [_jsx(ShimmerCard, { width: 240, height: 120, delay: 120 }), _jsx(ShimmerCard, { width: 240, height: 120, delay: 240 }), _jsx(ShimmerCard, { width: 240, height: 120, delay: 360 })] }), _jsxs("div", { style: { display: "flex", gap: 20 }, children: [_jsx(ShimmerCard, { width: 200, height: 90, delay: 480 }), _jsx(ShimmerCard, { width: 200, height: 90, delay: 560 }), _jsx(ShimmerCard, { width: 200, height: 90, delay: 640 }), _jsx(ShimmerCard, { width: 200, height: 90, delay: 720 })] })] }));
}
