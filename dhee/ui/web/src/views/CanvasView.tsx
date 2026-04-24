import { useEffect, useMemo, useRef, useState } from "react";
import { CanvasControls } from "../components/canvas/CanvasControls";
import { CanvasSkeleton } from "../components/canvas/CanvasSkeleton";
import { DirectionHints } from "../components/canvas/DirectionHints";
import { NodeCard, TYPE_COLOR } from "../components/canvas/NodeCard";
import { layoutGraph, type NodeLayout } from "../components/canvas/layout";
import { useInfiniteCanvas } from "../components/canvas/useInfiniteCanvas";
import { StatPill } from "../components/ui/StatPill";
import type {
  CanvasSelectionState,
  SankhyaTask,
  Tweaks,
  WorkspaceGraphEdge,
  WorkspaceGraphNode,
  WorkspaceGraphSnapshot,
} from "../types";

// ---------------------------------------------------------------------------
// CanvasView — openswarm-inspired infinite canvas. Renders every graph
// node as a real DOM card laid out hierarchically (workspace → projects →
// children), with smooth pan/zoom/momentum, minimap, direction hints,
// skeleton loader, and an inspector that's neighbour-aware.
// ---------------------------------------------------------------------------

const TYPE_LABEL: Record<string, string> = {
  workspace: "Workspace",
  project: "Project",
  channel: "Channel",
  session: "Session",
  task: "Task",
  result: "Tool result",
  file: "File",
  asset: "Asset",
  broadcast: "Broadcast",
};

function linkEndpointId(endpoint: unknown): string {
  if (!endpoint) return "";
  if (typeof endpoint === "string") return endpoint;
  if (typeof endpoint === "object" && endpoint !== null && "id" in endpoint)
    return String((endpoint as { id?: unknown }).id || "");
  return "";
}

function edgeHighlightClass(
  edge: WorkspaceGraphEdge,
  focusedId: string | null,
  neighbourSet: Set<string> | null,
): string {
  const src = linkEndpointId((edge as { source?: unknown }).source);
  const tgt = linkEndpointId((edge as { target?: unknown }).target);
  if (focusedId && (src === focusedId || tgt === focusedId))
    return "dhee-edge-path dhee-edge-path--highlight";
  if (neighbourSet && !(neighbourSet.has(src) && neighbourSet.has(tgt)))
    return "dhee-edge-path dhee-edge-path--dim";
  return "dhee-edge-path";
}

function NodeInspector({
  node,
  neighbourCount,
  onOpenWorkspace,
  onOpenProject,
  onOpenSession,
  onOpenTask,
}: {
  node: WorkspaceGraphNode | null;
  neighbourCount: number;
  onOpenWorkspace: (id: string) => void;
  onOpenProject: (projectId: string, workspaceId?: string) => void;
  onOpenSession: (sessionId: string, taskId?: string | null) => void;
  onOpenTask: (id: string) => void;
}) {
  const [showMeta, setShowMeta] = useState(false);
  if (!node) {
    return (
      <div
        style={{
          fontFamily: "var(--mono)",
          fontSize: 10,
          color: "var(--ink3)",
          lineHeight: 1.6,
        }}
      >
        Hover or click a card to inspect it. Drag the canvas with mouse or
        space-bar. ⌘/Ctrl + wheel to zoom.
      </div>
    );
  }

  const meta = (node.meta || {}) as Record<string, unknown>;
  const color = node.accent || TYPE_COLOR[node.type] || "#555";
  const buttonStyle: React.CSSProperties = {
    padding: "8px 12px",
    border: "1px solid var(--ink)",
    background: "var(--ink)",
    color: "white",
    fontFamily: "var(--mono)",
    fontSize: 9,
    letterSpacing: 0.6,
    textTransform: "uppercase",
    cursor: "pointer",
  };

  const kv = (key: string, value: unknown) => {
    if (value === undefined || value === null || value === "") return null;
    return (
      <div key={key} style={{ display: "flex", gap: 10, fontSize: 12, lineHeight: 1.55 }}>
        <span
          style={{
            fontFamily: "var(--mono)",
            fontSize: 10,
            color: "var(--ink3)",
            minWidth: 92,
            flexShrink: 0,
          }}
        >
          {key}
        </span>
        <span style={{ color: "var(--ink2)", wordBreak: "break-word" }}>{String(value)}</span>
      </div>
    );
  };

  const typeLabel = TYPE_LABEL[node.type] || node.type;

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>
      <div>
        <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 8 }}>
          <span
            style={{
              width: 10,
              height: 10,
              borderRadius: 2,
              background: color,
              boxShadow: `0 0 0 2px ${color}22`,
            }}
          />
          <StatPill label={typeLabel} tone={color} />
          {node.status ? <StatPill label={node.status} /> : null}
          <span style={{ marginLeft: "auto", fontFamily: "var(--mono)", fontSize: 9, color: "var(--ink3)" }}>
            {neighbourCount} connection{neighbourCount === 1 ? "" : "s"}
          </span>
        </div>
        <div style={{ fontSize: 18, fontWeight: 700, lineHeight: 1.25, wordBreak: "break-word" }}>
          {node.label || "(unnamed)"}
        </div>
        {node.subLabel ? (
          <div
            style={{
              marginTop: 4,
              fontFamily: "var(--mono)",
              fontSize: 10,
              color: "var(--ink3)",
              letterSpacing: 0.3,
            }}
          >
            {node.subLabel}
          </div>
        ) : null}
      </div>

      {node.body ? (
        <div
          style={{
            fontSize: 13,
            color: "var(--ink2)",
            lineHeight: 1.6,
            whiteSpace: "pre-wrap",
            borderLeft: `2px solid ${color}55`,
            paddingLeft: 10,
          }}
        >
          {node.body}
        </div>
      ) : null}

      {node.type === "workspace" && (
        <div style={{ display: "grid", gap: 4 }}>
          {kv("root", meta.rootPath)}
          {kv("projects", meta.projectCount)}
          {kv("sessions", meta.sessionCount)}
        </div>
      )}
      {node.type === "project" && (
        <div style={{ display: "grid", gap: 4 }}>
          {kv("workspace", meta.workspaceLabel)}
          {kv("runtime", meta.defaultRuntime)}
          {kv("sessions", meta.sessionCount)}
          {kv("tasks", meta.taskCount)}
        </div>
      )}
      {node.type === "session" && (
        <div style={{ display: "grid", gap: 4 }}>
          {kv("runtime", meta.runtime)}
          {kv("model", meta.model)}
          {kv("state", meta.state)}
          {kv("updated", meta.updatedAt)}
        </div>
      )}
      {node.type === "task" && (
        <div style={{ display: "grid", gap: 4 }}>
          {kv("harness", meta.harness)}
          {kv("status", meta.status)}
          {kv("messages", meta.messageCount)}
        </div>
      )}
      {node.type === "result" && (
        <div style={{ display: "grid", gap: 4 }}>
          {kv("tool", meta.toolName)}
          {kv("packet", meta.packetKind)}
          {kv("ptr", meta.ptr)}
          {kv("harness", meta.harness)}
          {kv("source", meta.sourcePath)}
        </div>
      )}
      {node.type === "broadcast" && (
        <div style={{ display: "grid", gap: 4 }}>
          {kv("from", meta.sourceProject || meta.sourceChannel)}
          {kv("to", meta.targetProject || meta.targetChannel)}
          {kv("kind", meta.messageKind)}
        </div>
      )}

      <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
        {node.type === "workspace" && Boolean(meta.workspaceId) && (
          <button onClick={() => onOpenWorkspace(String(meta.workspaceId))} style={buttonStyle}>
            open workspace
          </button>
        )}
        {node.type === "project" && Boolean(meta.projectId) && (
          <button
            onClick={() => onOpenProject(String(meta.projectId), (meta.workspaceId as string) || undefined)}
            style={buttonStyle}
          >
            open project
          </button>
        )}
        {node.type === "session" && (
          <button
            onClick={() => onOpenSession(node.id, (meta.taskId as string) || null)}
            style={buttonStyle}
          >
            open session
          </button>
        )}
        {node.type === "task" && (
          <button onClick={() => onOpenTask(node.id)} style={buttonStyle}>
            open task
          </button>
        )}
      </div>

      {Object.keys(meta).length > 0 && (
        <div style={{ borderTop: "1px solid var(--border)", paddingTop: 10 }}>
          <button
            onClick={() => setShowMeta((v) => !v)}
            style={{
              background: "transparent",
              border: 0,
              padding: 0,
              fontFamily: "var(--mono)",
              fontSize: 10,
              color: "var(--ink3)",
              cursor: "pointer",
              letterSpacing: 0.5,
            }}
          >
            {showMeta ? "▾ hide raw metadata" : "▸ show raw metadata"}
          </button>
          {showMeta && (
            <pre
              style={{
                marginTop: 8,
                border: "1px solid var(--border)",
                background: "var(--bg)",
                padding: 10,
                fontFamily: "var(--mono)",
                fontSize: 10,
                color: "var(--ink3)",
                lineHeight: 1.5,
                whiteSpace: "pre-wrap",
                wordBreak: "break-word",
                maxHeight: 260,
                overflow: "auto",
              }}
            >
              {JSON.stringify(meta, null, 2)}
            </pre>
          )}
        </div>
      )}
    </div>
  );
}

export function CanvasView({
  tasks,
  selectedProjectId,
  workspaceGraph,
  onSelectTask,
  onSelectSession,
  onSelectWorkspace,
  onSelectProject,
  onClose,
}: {
  tasks: SankhyaTask[];
  selectedProjectId?: string;
  workspaceGraph?: WorkspaceGraphSnapshot | null;
  onSelectTask: (id: string) => void;
  onSelectSession: (sessionId: string, taskId?: string | null) => void;
  onSelectWorkspace: (workspaceId: string) => void;
  onSelectProject: (projectId: string, workspaceId?: string | null) => void;
  onClose: () => void;
  tweaks: Tweaks;
}) {
  const [selection, setSelection] = useState<CanvasSelectionState>({});
  const [hovered, setHovered] = useState<WorkspaceGraphNode | null>(null);
  const [typeFilter, setTypeFilter] = useState<string | null>(null);

  // Fallback: if the server hasn't produced a graph, show each task as a
  // standalone card. Keeps the canvas useful before any agent session
  // exists in the workspace.
  const rawGraph = useMemo(() => {
    if (workspaceGraph?.graph?.nodes?.length) {
      return workspaceGraph.graph;
    }
    const nodes: WorkspaceGraphNode[] = tasks.map((task) => ({
      id: task.id,
      type: "task",
      label: task.title,
      subLabel: `${task.status || "active"} · ${task.harness || "dhee"}`,
      body: task.messages[task.messages.length - 1]?.content || "No activity yet.",
      val: 8,
      accent: TYPE_COLOR.task,
    }));
    return { nodes, links: [] as WorkspaceGraphEdge[] };
  }, [tasks, workspaceGraph]);

  const laidOut = useMemo(() => layoutGraph(rawGraph.nodes, rawGraph.links), [rawGraph]);

  const nodeMap = useMemo(
    () => new Map(laidOut.nodes.map((node) => [node.id, node])),
    [laidOut.nodes],
  );

  const nodeTypes = useMemo(() => {
    const out: Record<string, string> = {};
    for (const n of laidOut.nodes) out[n.id] = n.type;
    return out;
  }, [laidOut.nodes]);

  const neighbours = useMemo(() => {
    const map = new Map<string, Set<string>>();
    for (const node of laidOut.nodes) map.set(node.id, new Set());
    for (const link of laidOut.links) {
      const src = linkEndpointId((link as { source?: unknown }).source);
      const tgt = linkEndpointId((link as { target?: unknown }).target);
      if (!src || !tgt) continue;
      map.get(src)?.add(tgt);
      map.get(tgt)?.add(src);
    }
    return map;
  }, [laidOut.nodes, laidOut.links]);

  const presentTypes = useMemo(() => {
    const seen = new Set<string>();
    for (const node of laidOut.nodes) seen.add(node.type);
    return Array.from(seen);
  }, [laidOut.nodes]);

  const selectedNode = selection.nodeId ? nodeMap.get(selection.nodeId) || null : null;
  const focused = hovered || selectedNode;
  const focusedId = focused?.id || null;

  const neighbourSet = useMemo(() => {
    if (!focused) return null;
    const set = new Set<string>([focused.id]);
    for (const id of neighbours.get(focused.id) || []) set.add(id);
    return set;
  }, [focused, neighbours]);

  const {
    panX,
    panY,
    zoom,
    isPanning,
    spaceHeld,
    viewportRef,
    contentRef,
    handlers,
    actions,
  } = useInfiniteCanvas({
    contentBounds: laidOut.bounds,
  });

  // Initial fit: centre the whole graph when data first arrives, and
  // re-fit any time the graph materially changes shape.
  const lastBoundsKey = useRef("");
  useEffect(() => {
    const key = `${laidOut.nodes.length}:${Math.round(laidOut.bounds.minX)}:${Math.round(laidOut.bounds.maxX)}:${Math.round(laidOut.bounds.minY)}:${Math.round(laidOut.bounds.maxY)}`;
    if (key === lastBoundsKey.current) return;
    lastBoundsKey.current = key;
    if (!laidOut.nodes.length) return;
    const handle = window.setTimeout(() => {
      const rects = laidOut.nodes.map((n) => ({ x: n.x, y: n.y, width: n.width, height: n.height }));
      actions.fitToCards(rects, { maxZoom: 1, animate: true });
    }, 50);
    return () => window.clearTimeout(handle);
  }, [laidOut, actions]);

  // When the inspector selection changes (via link clicks or similar),
  // bring that card into view.
  useEffect(() => {
    if (!selection.nodeId) return;
    const node = nodeMap.get(selection.nodeId);
    if (!node) return;
    const rect = { x: node.x, y: node.y, width: node.width, height: node.height };
    actions.fitToCards([rect], { maxZoom: 1.3, animate: true });
  }, [selection.nodeId, nodeMap, actions]);

  // Direction hints: does content extend past the viewport edges right now?
  const offscreen = useMemo(() => {
    const vp = viewportRef.current;
    if (!vp || laidOut.nodes.length === 0)
      return { hasLeft: false, hasRight: false, hasUp: false, hasDown: false };
    const vRect = vp.getBoundingClientRect();
    // canvas-coord edges of visible region
    const visLeft = -panX / zoom;
    const visTop = -panY / zoom;
    const visRight = visLeft + vRect.width / zoom;
    const visBottom = visTop + vRect.height / zoom;
    const { minX, minY, maxX, maxY } = laidOut.bounds;
    return {
      hasLeft: minX < visLeft - 20,
      hasRight: maxX > visRight + 20,
      hasUp: minY < visTop - 20,
      hasDown: maxY > visBottom + 20,
    };
  }, [laidOut, panX, panY, zoom, viewportRef]);

  const panToDirection = (direction: "left" | "right" | "up" | "down") => {
    const vp = viewportRef.current;
    if (!vp) return;
    const { minX, minY, maxX, maxY } = laidOut.bounds;
    const vRect = vp.getBoundingClientRect();
    const vpW = vRect.width;
    const vpH = vRect.height;
    let targetPanX = panX;
    let targetPanY = panY;
    const PADDING = 40;
    if (direction === "left") targetPanX = -minX * zoom + PADDING;
    if (direction === "right") targetPanX = vpW - maxX * zoom - PADDING;
    if (direction === "up") targetPanY = -minY * zoom + PADDING;
    if (direction === "down") targetPanY = vpH - maxY * zoom - PADDING;
    actions.animateTo({ panX: targetPanX, panY: targetPanY, zoom });
  };

  const handleSelect = (node: WorkspaceGraphNode) => {
    setSelection({ nodeId: node.id, nodeType: node.type });
    // Delegate to the host when the node has a natural "open" action.
    const meta = (node.meta || {}) as Record<string, unknown>;
    if (node.type === "workspace" && meta.workspaceId) {
      onSelectWorkspace(String(meta.workspaceId));
    } else if (node.type === "project" && meta.projectId) {
      onSelectProject(String(meta.projectId), (meta.workspaceId as string) || undefined);
    } else if (node.type === "session") {
      onSelectSession(node.id, (meta.taskId as string) || undefined);
    } else if (node.type === "task" && tasks.some((t) => t.id === node.id)) {
      onSelectTask(node.id);
    }
  };

  const isLoading = workspaceGraph === undefined;

  // Filtered visibility — fades the non-matching nodes instead of hiding
  // them, so the overall structure stays legible.
  const matchesFilter = (type: string) => !typeFilter || type === typeFilter;

  const handleFit = () => {
    const rects = laidOut.nodes.map((n) => ({ x: n.x, y: n.y, width: n.width, height: n.height }));
    actions.fitToCards(rects, { maxZoom: 1, animate: true });
  };

  // "Tidy" re-applies the deterministic layout in place — gives the user
  // an explicit affordance for snapping back to the canonical view after
  // drags or scrolls.
  const handleTidy = () => handleFit();

  return (
    <div style={{ height: "100%", display: "flex", flexDirection: "column" }}>
      {/* Header */}
      <div
        style={{
          borderBottom: "1px solid var(--border)",
          padding: "0 18px",
          height: 48,
          display: "flex",
          alignItems: "center",
          gap: 10,
          flexShrink: 0,
        }}
      >
        <span style={{ fontFamily: "var(--mono)", fontSize: 10, color: "var(--ink3)" }}>
          {workspaceGraph?.workspace?.label || workspaceGraph?.workspace?.name || "workspace"}
          {selectedProjectId
            ? ` / ${
                laidOut.nodes.find(
                  (node) =>
                    node.type === "project" &&
                    String((node.meta || {}).projectId || "") === selectedProjectId,
                )?.label || "project"
              }`
            : ""}
        </span>
        <span style={{ fontFamily: "var(--mono)", fontSize: 10, color: "var(--ink3)" }}>
          {laidOut.nodes.length} cards · {laidOut.links.length} links
        </span>
        {workspaceGraph?.currentSessionId && <StatPill label="live session" tone="var(--green)" />}
        {typeFilter && <StatPill label={`filtered · ${typeFilter}`} tone={TYPE_COLOR[typeFilter]} />}
        <div style={{ marginLeft: "auto", display: "flex", gap: 6, alignItems: "center" }}>
          {presentTypes.map((type) => (
            <button
              key={type}
              onClick={() => setTypeFilter((curr) => (curr === type ? null : type))}
              style={{
                display: "flex",
                alignItems: "center",
                gap: 5,
                padding: "4px 8px",
                border: `1px solid ${typeFilter === type ? TYPE_COLOR[type] || "var(--ink)" : "var(--border)"}`,
                background: typeFilter === type ? `${TYPE_COLOR[type] || "#555"}14` : "white",
                color: typeFilter === type ? TYPE_COLOR[type] || "var(--ink)" : "var(--ink2)",
                fontFamily: "var(--mono)",
                fontSize: 9,
                letterSpacing: 0.5,
                textTransform: "uppercase",
                cursor: "pointer",
              }}
            >
              <span
                style={{
                  width: 7,
                  height: 7,
                  borderRadius: "50%",
                  background: TYPE_COLOR[type] || "#999",
                }}
              />
              {type}
            </button>
          ))}
          <button
            onClick={onClose}
            style={{
              padding: "6px 12px",
              border: "1px solid var(--border)",
              fontFamily: "var(--mono)",
              fontSize: 9,
              color: "var(--ink2)",
              background: "white",
              cursor: "pointer",
            }}
          >
            exit
          </button>
        </div>
      </div>

      {/* Body */}
      <div style={{ flex: 1, display: "grid", gridTemplateColumns: "minmax(0, 1fr) 340px", overflow: "hidden" }}>
        {/* Canvas viewport */}
        <div
          ref={viewportRef}
          className="dhee-canvas-bg"
          onMouseDown={handlers.onMouseDown}
          onMouseMove={handlers.onMouseMove}
          onMouseUp={handlers.onMouseUp}
          onClick={(e) => {
            // Deselect when clicking on the background (not a card).
            if (e.target === e.currentTarget || (e.target as HTMLElement).dataset.canvasBg) {
              setSelection({});
            }
          }}
          style={{
            position: "relative",
            overflow: "hidden",
            cursor: spaceHeld ? (isPanning ? "grabbing" : "grab") : isPanning ? "grabbing" : "default",
          }}
        >
          {/* The transformed content plane. Contains the SVG edges + the
              card layer. Panning/zooming is applied here via one matrix. */}
          <div
            ref={contentRef}
            data-canvas-bg="true"
            style={{
              position: "absolute",
              left: 0,
              top: 0,
              transformOrigin: "0 0",
              transform: `translate3d(${panX}px, ${panY}px, 0) scale(${zoom})`,
              willChange: "transform",
            }}
          >
            {/* Edges layer */}
            {laidOut.links.length > 0 ? (
              <svg
                style={{
                  position: "absolute",
                  left: laidOut.bounds.minX - 200,
                  top: laidOut.bounds.minY - 200,
                  width: laidOut.bounds.maxX - laidOut.bounds.minX + 400,
                  height: laidOut.bounds.maxY - laidOut.bounds.minY + 400,
                  pointerEvents: "none",
                }}
              >
                {laidOut.links.map((link) => {
                  const src = linkEndpointId((link as { source?: unknown }).source);
                  const tgt = linkEndpointId((link as { target?: unknown }).target);
                  const srcNode = nodeMap.get(src);
                  const tgtNode = nodeMap.get(tgt);
                  if (!srcNode || !tgtNode) return null;
                  const ox = laidOut.bounds.minX - 200;
                  const oy = laidOut.bounds.minY - 200;
                  const x1 = srcNode.x + srcNode.width / 2 - ox;
                  const y1 = srcNode.y + srcNode.height / 2 - oy;
                  const x2 = tgtNode.x + tgtNode.width / 2 - ox;
                  const y2 = tgtNode.y + tgtNode.height / 2 - oy;
                  // Cubic curve: vertical-biased bezier so parent→child lines
                  // flow top-down but stay visually soft.
                  const dx = x2 - x1;
                  const dy = y2 - y1;
                  const mid = Math.abs(dy) > Math.abs(dx) ? Math.abs(dy) * 0.45 : Math.abs(dx) * 0.3;
                  const c1x = x1;
                  const c1y = y1 + (dy > 0 ? mid : -mid);
                  const c2x = x2;
                  const c2y = y2 - (dy > 0 ? mid : -mid);
                  return (
                    <path
                      key={link.id}
                      d={`M ${x1} ${y1} C ${c1x} ${c1y} ${c2x} ${c2y} ${x2} ${y2}`}
                      className={edgeHighlightClass(link, focusedId, neighbourSet)}
                    />
                  );
                })}
              </svg>
            ) : null}

            {/* Card layer */}
            {laidOut.nodes.map((node, idx) => {
              const isFocus = focusedId === node.id;
              const matches = matchesFilter(node.type);
              const inNeighbourhood = neighbourSet ? neighbourSet.has(node.id) : true;
              const dim = !matches || (neighbourSet ? !inNeighbourhood : false);
              return (
                <NodeCard
                  key={node.id}
                  node={node}
                  x={node.x}
                  y={node.y}
                  width={node.width}
                  height={node.height}
                  selected={isFocus}
                  dim={dim}
                  onSelect={handleSelect}
                  onHover={setHovered}
                  entranceDelay={Math.min(idx * 18, 540)}
                />
              );
            })}
          </div>

          {isLoading && <CanvasSkeleton />}
          {!isLoading && laidOut.nodes.length === 0 && (
            <div
              style={{
                position: "absolute",
                inset: 0,
                display: "flex",
                alignItems: "center",
                justifyContent: "center",
                pointerEvents: "none",
                fontFamily: "var(--mono)",
                fontSize: 11,
                color: "var(--ink3)",
              }}
            >
              no cards yet — launch a session to populate the canvas
            </div>
          )}

          <DirectionHints
            hasLeft={offscreen.hasLeft}
            hasRight={offscreen.hasRight}
            hasUp={offscreen.hasUp}
            hasDown={offscreen.hasDown}
            onPanTo={panToDirection}
          />

          <CanvasControls
            zoom={zoom}
            actions={actions}
            onFitToContent={handleFit}
            onTidy={handleTidy}
            minimapProps={{
              panX,
              panY,
              zoom,
              viewportRef,
              cards: laidOut.nodes.map<NodeLayout>((n) => ({
                id: n.id,
                x: n.x,
                y: n.y,
                width: n.width,
                height: n.height,
              })),
              nodeTypes,
            }}
            onMinimapPan={(nextPanX, nextPanY) =>
              actions.setState({ panX: nextPanX, panY: nextPanY, zoom })
            }
          />
        </div>

        {/* Inspector */}
        <div
          style={{
            borderLeft: "1px solid var(--border)",
            background: "white",
            padding: 20,
            overflowY: "auto",
          }}
        >
          <div
            style={{
              display: "flex",
              justifyContent: "space-between",
              alignItems: "center",
              marginBottom: 14,
              fontFamily: "var(--mono)",
              fontSize: 10,
              color: "var(--ink3)",
              letterSpacing: 0.5,
              textTransform: "uppercase",
            }}
          >
            <span>Inspector</span>
            {isLoading ? <span>loading…</span> : null}
          </div>
          <NodeInspector
            node={focused}
            neighbourCount={focused ? neighbours.get(focused.id)?.size || 0 : 0}
            onOpenWorkspace={onSelectWorkspace}
            onOpenProject={onSelectProject}
            onOpenSession={onSelectSession}
            onOpenTask={onSelectTask}
          />
        </div>
      </div>
    </div>
  );
}
