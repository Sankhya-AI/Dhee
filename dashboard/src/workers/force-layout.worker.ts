import {
  forceSimulation,
  forceLink,
  forceManyBody,
  forceCenter,
  forceCollide,
  type SimulationNodeDatum,
  type SimulationLinkDatum,
} from "d3-force";

interface WorkerNode extends SimulationNodeDatum {
  id: string;
  layer: string;
  strength: number;
}

interface WorkerEdge {
  source: string;
  target: string;
  type: string;
}

interface InitMessage {
  type: "init";
  nodes: WorkerNode[];
  edges: WorkerEdge[];
  width: number;
  height: number;
}

interface TickResult {
  type: "tick" | "end";
  nodes: { id: string; x: number; y: number }[];
}

self.onmessage = (e: MessageEvent<InitMessage>) => {
  const { nodes, edges, width, height } = e.data;

  const sim = forceSimulation<WorkerNode>(nodes)
    .force(
      "link",
      forceLink<WorkerNode, SimulationLinkDatum<WorkerNode>>(
        edges.map((e) => ({ ...e }))
      )
        .id((d) => d.id)
        .distance(60)
        .strength(0.3)
    )
    .force("charge", forceManyBody().strength(-80))
    .force("center", forceCenter(width / 2, height / 2))
    .force("collide", forceCollide(12))
    .alphaDecay(0.02);

  sim.on("tick", () => {
    const positions = nodes.map((n) => ({
      id: n.id,
      x: n.x ?? 0,
      y: n.y ?? 0,
    }));
    (self as unknown as Worker).postMessage({
      type: "tick",
      nodes: positions,
    } as TickResult);
  });

  sim.on("end", () => {
    const positions = nodes.map((n) => ({
      id: n.id,
      x: n.x ?? 0,
      y: n.y ?? 0,
    }));
    (self as unknown as Worker).postMessage({
      type: "end",
      nodes: positions,
    } as TickResult);
  });
};
