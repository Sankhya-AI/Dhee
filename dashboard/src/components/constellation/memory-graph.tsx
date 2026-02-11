"use client";

import { useRef, useEffect, useCallback } from "react";
import { GraphRenderer } from "./graph-renderer";
import { useConstellation } from "@/lib/hooks/use-constellation";
import { useInspectorStore } from "@/lib/stores/inspector-store";
import { useGraphStore } from "@/lib/stores/graph-store";

export function MemoryGraph() {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const rendererRef = useRef<GraphRenderer | null>(null);
  const workerRef = useRef<Worker | null>(null);
  const { data } = useConstellation();
  const openInspector = useInspectorStore((s) => s.open);
  const { showSml, showLml } = useGraphStore();

  const handleNodeClick = useCallback(
    (id: string) => {
      openInspector(id);
    },
    [openInspector]
  );

  useEffect(() => {
    if (!canvasRef.current || !data) return;

    const canvas = canvasRef.current;
    const renderer = new GraphRenderer();
    rendererRef.current = renderer;

    // Filter nodes by layer visibility
    const filteredNodes = (data.nodes || []).filter((n) => {
      if (n.layer === "sml" && !showSml) return false;
      if (n.layer === "lml" && !showLml) return false;
      return true;
    });

    const filteredIds = new Set(filteredNodes.map((n) => n.id));
    const filteredEdges = (data.edges || []).filter(
      (e) => filteredIds.has(e.source) && filteredIds.has(e.target)
    );

    renderer.init(canvas, handleNodeClick).then(() => {
      renderer.setData(filteredNodes, filteredEdges);

      // Start force layout in worker
      const worker = new Worker(
        new URL("@/workers/force-layout.worker.ts", import.meta.url)
      );
      workerRef.current = worker;

      worker.postMessage({
        type: "init",
        nodes: filteredNodes.map((n) => ({
          id: n.id,
          layer: n.layer,
          strength: n.strength,
        })),
        edges: filteredEdges.map((e) => ({
          source: e.source,
          target: e.target,
          type: e.type,
        })),
        width: canvas.clientWidth,
        height: canvas.clientHeight,
      });

      worker.onmessage = (e) => {
        renderer.updatePositions(e.data.nodes);
      };
    });

    return () => {
      workerRef.current?.terminate();
      rendererRef.current?.destroy();
    };
  }, [data, showSml, showLml, handleNodeClick]);

  return (
    <canvas
      ref={canvasRef}
      className="h-full w-full"
      style={{ display: "block" }}
    />
  );
}
