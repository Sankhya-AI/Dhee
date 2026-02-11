"use client";

import { useRef, useEffect, useCallback, useMemo } from "react";
import { Application, Graphics, Text, TextStyle, Container, FederatedPointerEvent } from "pixi.js";
import { useMemories } from "@/lib/hooks/use-memories";
import { useClusterStore } from "@/lib/stores/cluster-store";
import { useInspectorStore } from "@/lib/stores/inspector-store";
import { computeClusterLayout } from "@/lib/utils/cluster-layout";
import { NEURAL } from "@/lib/utils/neural-palette";
import type { Memory } from "@/lib/types/memory";

export function ClusterCanvas() {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const appRef = useRef<Application | null>(null);
  const { data } = useMemories({ limit: 500 });
  const { dimension, transitioning } = useClusterStore();
  const openInspectorRef = useRef(useInspectorStore.getState().open);

  // Keep ref in sync without causing redraws
  useEffect(() => {
    return useInspectorStore.subscribe((s) => {
      openInspectorRef.current = s.open;
    });
  }, []);

  const memories = useMemo(() => data?.memories ?? [], [data]);

  const draw = useCallback(async () => {
    const canvas = canvasRef.current;
    if (!canvas) return;

    // Destroy existing app
    if (appRef.current) {
      appRef.current.destroy(true);
      appRef.current = null;
    }

    const app = new Application();
    await app.init({
      canvas,
      resizeTo: canvas.parentElement || undefined,
      backgroundColor: 0x050510,
      antialias: false,
      resolution: Math.min(window.devicePixelRatio || 1, 2),
      autoDensity: true,
    });
    appRef.current = app;

    if (memories.length === 0) {
      const text = new Text({
        text: "No memories to display",
        style: new TextStyle({ fontSize: 14, fill: 0x64748b, fontFamily: "system-ui" }),
      });
      text.anchor.set(0.5);
      text.position.set(canvas.clientWidth / 2, canvas.clientHeight / 2);
      app.stage.addChild(text);
      return;
    }

    const { clusters, nodes } = computeClusterLayout(
      memories,
      dimension,
      canvas.clientWidth,
      canvas.clientHeight
    );

    const clustersContainer = new Container();
    const nodesContainer = new Container();
    app.stage.addChild(clustersContainer);
    app.stage.addChild(nodesContainer);

    // Draw cluster boundaries
    for (const cluster of clusters) {
      const gfx = new Graphics();

      // Cluster halo
      gfx.circle(cluster.x, cluster.y, cluster.radius * 1.2);
      gfx.fill({ color: parseInt(cluster.color.slice(1), 16), alpha: 0.03 });

      // Cluster boundary
      gfx.circle(cluster.x, cluster.y, cluster.radius);
      gfx.stroke({ width: 1, color: parseInt(cluster.color.slice(1), 16), alpha: 0.15 });

      clustersContainer.addChild(gfx);

      // Cluster label
      const label = new Text({
        text: cluster.label,
        style: new TextStyle({
          fontSize: 11,
          fill: parseInt(cluster.color.slice(1), 16),
          fontFamily: "system-ui",
          fontWeight: "600",
        }),
      });
      label.anchor.set(0.5);
      label.position.set(cluster.x, cluster.y - cluster.radius - 12);
      clustersContainer.addChild(label);

      // Count label
      const count = new Text({
        text: `${cluster.memories.length}`,
        style: new TextStyle({
          fontSize: 9,
          fill: 0x64748b,
          fontFamily: "system-ui",
        }),
      });
      count.anchor.set(0.5);
      count.position.set(cluster.x, cluster.y + cluster.radius + 8);
      clustersContainer.addChild(count);
    }

    // Draw memory nodes
    const nodeMap = new Map(nodes.map(n => [n.id, n]));
    for (const memory of memories) {
      const nodePos = nodeMap.get(memory.id);
      if (!nodePos) continue;

      const clusterColor = clusters[nodePos.clusterIndex]?.color || NEURAL.episodic;
      const radius = 3 + memory.strength * 8;
      const colorHex = parseInt(clusterColor.slice(1), 16);

      const gfx = new Graphics();

      // Glow
      gfx.circle(0, 0, radius * 2);
      gfx.fill({ color: colorHex, alpha: 0.06 });

      // Node
      gfx.circle(0, 0, radius);
      gfx.fill({ color: colorHex, alpha: 0.7 + memory.strength * 0.3 });

      gfx.position.set(nodePos.x, nodePos.y);
      gfx.eventMode = "static";
      gfx.cursor = "pointer";

      gfx.on("pointerdown", (e: FederatedPointerEvent) => {
        e.stopPropagation();
        openInspectorRef.current(memory.id);
      });

      gfx.on("pointerover", () => gfx.scale.set(1.5));
      gfx.on("pointerout", () => gfx.scale.set(1));

      nodesContainer.addChild(gfx);
    }
  }, [memories, dimension]);

  useEffect(() => {
    draw();
    return () => {
      appRef.current?.destroy(true);
      appRef.current = null;
    };
  }, [draw]);

  return (
    <div className="relative h-full w-full">
      <canvas
        ref={canvasRef}
        className="h-full w-full"
        style={{
          display: "block",
          opacity: transitioning ? 0.5 : 1,
          transition: "opacity 0.3s ease",
        }}
      />
    </div>
  );
}
