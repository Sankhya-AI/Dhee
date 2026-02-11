import { Application, Graphics, Text, TextStyle, Container, FederatedPointerEvent } from "pixi.js";
import { COLORS } from "@/lib/utils/colors";
import type { ConstellationNode, ConstellationEdge } from "@/lib/types/constellation";

interface NodePosition {
  id: string;
  x: number;
  y: number;
}

export class GraphRenderer {
  private app: Application | null = null;
  private nodesContainer: Container = new Container();
  private edgesContainer: Container = new Container();
  private nodes: ConstellationNode[] = [];
  private edges: ConstellationEdge[] = [];
  private nodeGraphics: Map<string, Graphics> = new Map();
  private positions: Map<string, { x: number; y: number }> = new Map();
  private onNodeClick?: (id: string) => void;

  async init(canvas: HTMLCanvasElement, onNodeClick?: (id: string) => void) {
    this.onNodeClick = onNodeClick;
    this.app = new Application();
    await this.app.init({
      canvas,
      resizeTo: canvas.parentElement || undefined,
      backgroundColor: 0x050510,
      antialias: true,
      resolution: window.devicePixelRatio || 1,
      autoDensity: true,
    });
    this.app.stage.addChild(this.edgesContainer);
    this.app.stage.addChild(this.nodesContainer);
  }

  setData(nodes: ConstellationNode[], edges: ConstellationEdge[]) {
    this.nodes = nodes;
    this.edges = edges;
  }

  updatePositions(positions: NodePosition[]) {
    for (const p of positions) {
      this.positions.set(p.id, { x: p.x, y: p.y });
    }
    this.draw();
  }

  private draw() {
    // Draw edges
    this.edgesContainer.removeChildren();
    const edgeGfx = new Graphics();
    for (const edge of this.edges) {
      const sPos = this.positions.get(edge.source);
      const tPos = this.positions.get(edge.target);
      if (!sPos || !tPos) continue;
      const color = edge.type === "category" ? 0x7c3aed : 0x22d3ee;
      edgeGfx.moveTo(sPos.x, sPos.y);
      edgeGfx.lineTo(tPos.x, tPos.y);
      edgeGfx.stroke({ width: 0.5, color, alpha: 0.15 });
    }
    this.edgesContainer.addChild(edgeGfx);

    // Draw nodes
    this.nodesContainer.removeChildren();
    this.nodeGraphics.clear();

    for (const node of this.nodes) {
      const pos = this.positions.get(node.id);
      if (!pos) continue;

      const radius = 4 + node.strength * 10;
      const colorHex =
        node.layer === "sml"
          ? parseInt(COLORS.sml.slice(1), 16)
          : parseInt(COLORS.lml.slice(1), 16);

      const gfx = new Graphics();

      // Glow halo
      gfx.circle(0, 0, radius * 2);
      gfx.fill({ color: colorHex, alpha: 0.08 });

      // Main node
      gfx.circle(0, 0, radius);
      gfx.fill({ color: colorHex, alpha: 0.85 });

      gfx.position.set(pos.x, pos.y);
      gfx.eventMode = "static";
      gfx.cursor = "pointer";

      gfx.on("pointerdown", (e: FederatedPointerEvent) => {
        e.stopPropagation();
        this.onNodeClick?.(node.id);
      });

      gfx.on("pointerover", () => {
        gfx.scale.set(1.3);
      });

      gfx.on("pointerout", () => {
        gfx.scale.set(1);
      });

      this.nodesContainer.addChild(gfx);
      this.nodeGraphics.set(node.id, gfx);

      // Label for larger nodes
      if (node.strength > 0.5 && node.label) {
        const label = new Text({
          text: node.label.slice(0, 20),
          style: new TextStyle({
            fontSize: 9,
            fill: 0x94a3b8,
            fontFamily: "system-ui",
          }),
        });
        label.anchor.set(0.5, 0);
        label.position.set(pos.x, pos.y + radius + 3);
        this.nodesContainer.addChild(label);
      }
    }
  }

  highlightNode(id: string | null) {
    for (const [nodeId, gfx] of this.nodeGraphics) {
      gfx.alpha = id === null || nodeId === id ? 1 : 0.3;
    }
  }

  destroy() {
    this.app?.destroy(true);
    this.app = null;
  }
}
