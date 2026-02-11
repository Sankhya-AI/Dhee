"use client";

import { useRef, useEffect, useCallback } from "react";
import { Application, Graphics, Text, TextStyle, Container, FederatedPointerEvent } from "pixi.js";
import { useConstellation } from "@/lib/hooks/use-constellation";
import { useInspectorStore } from "@/lib/stores/inspector-store";
import { NEURAL } from "@/lib/utils/neural-palette";
import {
  forceSimulation,
  forceLink,
  forceManyBody,
  forceCenter,
  forceCollide,
  type SimulationNodeDatum,
  type SimulationLinkDatum,
} from "d3-force";

interface ForceNode extends SimulationNodeDatum {
  id: string;
  layer: string;
  strength: number;
  label: string;
}

export function SynapseGraph() {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const appRef = useRef<Application | null>(null);
  const { data } = useConstellation();
  const openInspector = useInspectorStore((s) => s.open);

  const draw = useCallback(async () => {
    const canvas = canvasRef.current;
    if (!canvas || !data) return;

    if (appRef.current) {
      appRef.current.destroy(true);
      appRef.current = null;
    }

    const app = new Application();
    await app.init({
      canvas,
      resizeTo: canvas.parentElement || undefined,
      backgroundColor: 0x050510,
      antialias: true,
      resolution: window.devicePixelRatio || 1,
      autoDensity: true,
    });
    appRef.current = app;

    const nodes = data.nodes || [];
    const edges = data.edges || [];
    if (nodes.length === 0) {
      const text = new Text({
        text: "No synaptic connections to display",
        style: new TextStyle({ fontSize: 14, fill: 0x64748b, fontFamily: "system-ui" }),
      });
      text.anchor.set(0.5);
      text.position.set(canvas.clientWidth / 2, canvas.clientHeight / 2);
      app.stage.addChild(text);
      return;
    }

    const edgesContainer = new Container();
    const nodesContainer = new Container();
    app.stage.addChild(edgesContainer);
    app.stage.addChild(nodesContainer);

    // Run d3-force
    const forceNodes: ForceNode[] = nodes.map(n => ({
      id: n.id,
      layer: n.layer,
      strength: n.strength,
      label: n.label || '',
    }));

    const forceEdges = edges.map(e => ({
      source: e.source,
      target: e.target,
      type: e.type,
    }));

    const sim = forceSimulation<ForceNode>(forceNodes)
      .force(
        "link",
        forceLink<ForceNode, SimulationLinkDatum<ForceNode>>(forceEdges as SimulationLinkDatum<ForceNode>[])
          .id(d => d.id)
          .distance(80)
          .strength(0.2)
      )
      .force("charge", forceManyBody().strength(-100))
      .force("center", forceCenter(canvas.clientWidth / 2, canvas.clientHeight / 2))
      .force("collide", forceCollide(15));

    sim.on("tick", () => {
      edgesContainer.removeChildren();
      nodesContainer.removeChildren();

      // Draw edges as synapses
      const edgeGfx = new Graphics();
      for (const edge of forceEdges) {
        const s = forceNodes.find(n => n.id === (edge.source as unknown as ForceNode).id || n.id === edge.source);
        const t = forceNodes.find(n => n.id === (edge.target as unknown as ForceNode).id || n.id === edge.target);
        if (!s?.x || !s?.y || !t?.x || !t?.y) continue;

        const color = edge.type === "category" ? 0x7c3aed : 0x22d3ee;
        const alpha = 0.12;

        edgeGfx.moveTo(s.x, s.y);
        edgeGfx.lineTo(t.x, t.y);
        edgeGfx.stroke({ width: 1, color, alpha });
      }
      edgesContainer.addChild(edgeGfx);

      // Draw nodes
      for (const node of forceNodes) {
        if (!node.x || !node.y) continue;

        const radius = 4 + node.strength * 8;
        const colorHex = node.layer === "sml" ? 0x22d3ee : 0xfbbf24;

        const gfx = new Graphics();
        // Glow
        gfx.circle(0, 0, radius * 2.5);
        gfx.fill({ color: colorHex, alpha: 0.05 });
        // Node
        gfx.circle(0, 0, radius);
        gfx.fill({ color: colorHex, alpha: 0.8 });

        gfx.position.set(node.x, node.y);
        gfx.eventMode = "static";
        gfx.cursor = "pointer";

        gfx.on("pointerdown", (e: FederatedPointerEvent) => {
          e.stopPropagation();
          openInspector(node.id);
        });
        gfx.on("pointerover", () => gfx.scale.set(1.4));
        gfx.on("pointerout", () => gfx.scale.set(1));

        nodesContainer.addChild(gfx);

        // Label
        if (node.strength > 0.5 && node.label) {
          const label = new Text({
            text: node.label.slice(0, 25),
            style: new TextStyle({ fontSize: 8, fill: 0x94a3b8, fontFamily: "system-ui" }),
          });
          label.anchor.set(0.5, 0);
          label.position.set(node.x, node.y + radius + 4);
          nodesContainer.addChild(label);
        }
      }
    });

    // Stop after 200 ticks
    sim.alpha(1).alphaDecay(0.005);
  }, [data, openInspector]);

  useEffect(() => {
    draw();
    return () => {
      appRef.current?.destroy(true);
      appRef.current = null;
    };
  }, [draw]);

  return (
    <canvas ref={canvasRef} className="h-full w-full" style={{ display: "block" }} />
  );
}
