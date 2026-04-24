import { useCallback, useMemo, useRef } from "react";
import type { NodeLayout } from "./layout";
import { TYPE_COLOR } from "./NodeCard";

// ---------------------------------------------------------------------------
// Minimap — scaled overview of the canvas. Shows every card as a filled
// rect, draws the viewport as an outlined rect, and lets the user click
// or drag to pan the main canvas.
// ---------------------------------------------------------------------------

const MINIMAP_W = 208;
const MINIMAP_H = 140;
const PADDING = 12;

export interface MinimapProps {
  panX: number;
  panY: number;
  zoom: number;
  viewportRef: React.RefObject<HTMLDivElement | null>;
  cards: NodeLayout[];
  nodeTypes: Record<string, string>; // id → type (for colour)
  onPan: (panX: number, panY: number) => void;
}

export function Minimap({
  panX,
  panY,
  zoom,
  viewportRef,
  cards,
  nodeTypes,
  onPan,
}: MinimapProps) {
  const svgRef = useRef<SVGSVGElement>(null);
  const isDraggingRef = useRef(false);

  const layout = useMemo(() => {
    const vp = viewportRef.current;
    const vpW = vp ? vp.clientWidth : 1200;
    const vpH = vp ? vp.clientHeight : 800;
    const vpRect = {
      x: -panX / zoom,
      y: -panY / zoom,
      width: vpW / zoom,
      height: vpH / zoom,
    };

    if (cards.length === 0) {
      const scale = Math.min(
        (MINIMAP_W - PADDING * 2) / Math.max(1, vpRect.width),
        (MINIMAP_H - PADDING * 2) / Math.max(1, vpRect.height),
      );
      return {
        scale,
        offsetX: MINIMAP_W / 2 - (vpRect.x + vpRect.width / 2) * scale,
        offsetY: MINIMAP_H / 2 - (vpRect.y + vpRect.height / 2) * scale,
        vpRect,
      };
    }

    let minX = vpRect.x,
      minY = vpRect.y;
    let maxX = vpRect.x + vpRect.width,
      maxY = vpRect.y + vpRect.height;
    for (const card of cards) {
      minX = Math.min(minX, card.x);
      minY = Math.min(minY, card.y);
      maxX = Math.max(maxX, card.x + card.width);
      maxY = Math.max(maxY, card.y + card.height);
    }
    const contentW = Math.max(1, maxX - minX);
    const contentH = Math.max(1, maxY - minY);
    const scale = Math.min(
      (MINIMAP_W - PADDING * 2) / contentW,
      (MINIMAP_H - PADDING * 2) / contentH,
    );
    return {
      scale,
      offsetX: (MINIMAP_W - contentW * scale) / 2 - minX * scale,
      offsetY: (MINIMAP_H - contentH * scale) / 2 - minY * scale,
      vpRect,
    };
  }, [cards, panX, panY, zoom, viewportRef]);

  const minimapToCanvas = useCallback(
    (clientX: number, clientY: number) => {
      const svg = svgRef.current;
      if (!svg) return;
      const rect = svg.getBoundingClientRect();
      const mx = clientX - rect.left;
      const my = clientY - rect.top;
      const canvasX = (mx - layout.offsetX) / layout.scale;
      const canvasY = (my - layout.offsetY) / layout.scale;
      onPan(
        -(canvasX - layout.vpRect.width / 2) * zoom,
        -(canvasY - layout.vpRect.height / 2) * zoom,
      );
    },
    [layout, zoom, onPan],
  );

  const handleMouseDown = useCallback(
    (e: React.MouseEvent) => {
      e.preventDefault();
      e.stopPropagation();
      isDraggingRef.current = true;
      minimapToCanvas(e.clientX, e.clientY);
      const onMove = (ev: MouseEvent) => {
        if (isDraggingRef.current) minimapToCanvas(ev.clientX, ev.clientY);
      };
      const onUp = () => {
        isDraggingRef.current = false;
        window.removeEventListener("mousemove", onMove);
        window.removeEventListener("mouseup", onUp);
      };
      window.addEventListener("mousemove", onMove);
      window.addEventListener("mouseup", onUp);
    },
    [minimapToCanvas],
  );

  return (
    <svg
      ref={svgRef}
      width={MINIMAP_W}
      height={MINIMAP_H}
      onMouseDown={handleMouseDown}
      style={{
        cursor: "pointer",
        display: "block",
        borderRadius: 6,
        background:
          "radial-gradient(circle at 30% 20%, rgba(250,246,236,0.6), rgba(236,227,210,0.4))",
      }}
    >
      {cards.map((card) => {
        const type = nodeTypes[card.id] || "session";
        const color = TYPE_COLOR[type] || "#666";
        return (
          <rect
            key={card.id}
            x={card.x * layout.scale + layout.offsetX}
            y={card.y * layout.scale + layout.offsetY}
            width={Math.max(2, card.width * layout.scale)}
            height={Math.max(2, card.height * layout.scale)}
            fill={color}
            opacity={0.55}
            rx={1.2}
          />
        );
      })}
      <rect
        x={layout.vpRect.x * layout.scale + layout.offsetX}
        y={layout.vpRect.y * layout.scale + layout.offsetY}
        width={Math.max(4, layout.vpRect.width * layout.scale)}
        height={Math.max(4, layout.vpRect.height * layout.scale)}
        fill="rgba(224,107,63,0.08)"
        stroke="#e06b3f"
        strokeWidth={1.2}
        rx={2}
      />
    </svg>
  );
}
