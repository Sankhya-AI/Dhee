// Tiny self-check for the layout function. Run with `node --loader
// tsx src/components/canvas/__tests__/layout.smoke.ts` (dev only — the
// production build never imports this file).
import { layoutGraph } from "../layout";
function assert(cond, msg) {
    if (!cond)
        throw new Error(`assertion failed: ${msg}`);
}
function demoGraph() {
    const nodes = [
        { id: "ws", type: "workspace", label: "Sankhya AI Labs" },
        { id: "p-be", type: "project", label: "backend" },
        { id: "p-fe", type: "project", label: "frontend" },
        { id: "s-be-1", type: "session", label: "codex · api-refactor", meta: { runtime: "codex" } },
        { id: "s-fe-1", type: "session", label: "claude · ui polish", meta: { runtime: "claude-code" } },
        { id: "t-1", type: "task", label: "wire plan badge" },
        { id: "r-1", type: "result", label: "read · schema.sql", meta: { ptr: "R-001" } },
        { id: "b-1", type: "broadcast", label: "api contract changed" },
        { id: "f-1", type: "file", label: "api/main.py" },
        { id: "loose", type: "result", label: "orphan result" },
    ];
    const links = [
        { id: "e1", source: "ws", target: "p-be", label: "contains" },
        { id: "e2", source: "ws", target: "p-fe", label: "contains" },
        { id: "e3", source: "p-be", target: "s-be-1", label: "session" },
        { id: "e4", source: "p-fe", target: "s-fe-1", label: "session" },
        { id: "e5", source: "p-fe", target: "t-1", label: "task" },
        { id: "e6", source: "p-be", target: "r-1", label: "result" },
        { id: "e7", source: "p-be", target: "b-1", label: "broadcast" },
        { id: "e8", source: "p-be", target: "f-1", label: "file" },
    ];
    return { nodes, links };
}
function run() {
    const graph = demoGraph();
    const laid = layoutGraph(graph.nodes, graph.links);
    assert(laid.nodes.length === graph.nodes.length, "all nodes laid out");
    assert(laid.links.length === graph.links.length, "links preserved");
    assert(isFinite(laid.bounds.minX), "bounds resolved");
    assert(laid.bounds.maxX > laid.bounds.minX, "bounds width > 0");
    assert(laid.bounds.maxY > laid.bounds.minY, "bounds height > 0");
    const byId = new Map(laid.nodes.map((n) => [n.id, n]));
    const ws = byId.get("ws");
    const pbe = byId.get("p-be");
    const pfe = byId.get("p-fe");
    const sbe = byId.get("s-be-1");
    assert(pbe.y > ws.y, "project below workspace");
    assert(pfe.y > ws.y, "sibling project below workspace");
    assert(Math.abs(pbe.y - pfe.y) < 1, "sibling projects share y-row");
    assert(sbe.y > pbe.y, "session below its project");
    assert(Math.abs(pbe.x - pfe.x) > 100, "projects spaced apart");
    // Determinism — same input twice, same output.
    const again = layoutGraph(graph.nodes, graph.links);
    for (const n of laid.nodes) {
        const other = again.nodes.find((o) => o.id === n.id);
        assert(other.x === n.x && other.y === n.y, `stable layout for ${n.id}`);
    }
    // Orphan lands in the overflow strip (below all placed content).
    const loose = byId.get("loose");
    const maxPlacedY = Math.max(...laid.nodes.filter((n) => n.id !== "loose").map((n) => n.y + n.height));
    assert(loose.y > maxPlacedY + 100, "orphan placed below the cluster");
    // eslint-disable-next-line no-console
    console.log("layout smoke: OK", {
        nodes: laid.nodes.length,
        bounds: laid.bounds,
    });
}
run();
