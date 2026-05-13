import { memo } from "react";
import type { WorkspaceGraphNode } from "../../types";

// ---------------------------------------------------------------------------
// NodeCard — content-aware card rendered at a fixed position on the
// infinite canvas. One card per graph node. Rendering is
// content-conscious: workspace/project/session cards show rich meta;
// result/file/asset chips are deliberately compact so dozens of them
// still fit in a zoomed-out view.
//
// Styling principles (to match openswarm's premium feel):
//   - paper-white surface with a 1px tonal border
//   - type-coloured accent strip along the left edge
//   - subtle shadow at rest, stronger on hover/selection
//   - hover lift via transform (GPU-friendly, no repaint jitter)
// ---------------------------------------------------------------------------

export const TYPE_COLOR: Record<string, string> = {
  workspace: "#e06b3f",
  project: "#4d6cff",
  channel: "#1fa971",
  session: "#1a1a1a",
  task: "#0f9f55",
  result: "#0b8b5f",
  file: "#64748b",
  asset: "#d74b7b",
  broadcast: "#e08b3f",
};

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

function accentFor(node: WorkspaceGraphNode) {
  return node.accent || TYPE_COLOR[node.type] || "#555";
}

function fmtTime(value?: unknown): string {
  if (!value) return "";
  const date = new Date(String(value));
  if (Number.isNaN(date.getTime())) return "";
  return date.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

function clamp(text: string, max: number): string {
  const trimmed = text.trim();
  if (trimmed.length <= max) return trimmed;
  return `${trimmed.slice(0, max - 1).trimEnd()}…`;
}

interface Props {
  node: WorkspaceGraphNode;
  x: number;
  y: number;
  width: number;
  height: number;
  selected: boolean;
  dim: boolean;
  onSelect: (node: WorkspaceGraphNode) => void;
  onHover: (node: WorkspaceGraphNode | null) => void;
  entranceDelay?: number;
}

function RawNodeCard({
  node,
  x,
  y,
  width,
  height,
  selected,
  dim,
  onSelect,
  onHover,
  entranceDelay = 0,
}: Props) {
  const accent = accentFor(node);
  const meta = (node.meta || {}) as Record<string, unknown>;
  const isCompact = height < 120;
  const type = node.type;

  const baseStyle: React.CSSProperties = {
    position: "absolute",
    left: x,
    top: y,
    width,
    height,
    display: "flex",
    boxSizing: "border-box",
    background: "white",
    border: `1px solid ${selected ? accent : "rgba(20,16,10,0.12)"}`,
    borderLeft: `3px solid ${accent}`,
    borderRadius: 8,
    boxShadow: selected
      ? `0 10px 26px rgba(20,16,10,0.12), 0 0 0 3px ${accent}22`
      : "0 1px 2px rgba(20,16,10,0.04), 0 2px 10px rgba(20,16,10,0.04)",
    transition: "box-shadow 0.18s ease, border-color 0.18s ease, transform 0.18s ease, opacity 0.18s ease",
    cursor: "pointer",
    userSelect: "none",
    opacity: dim ? 0.32 : 1,
    willChange: "transform",
    transform: "translate3d(0, 0, 0)",
    overflow: "hidden",
    // Slight entrance stagger — pure CSS keyframe set below.
    animation: `dhee-card-in 320ms ${entranceDelay}ms cubic-bezier(0.17, 0.67, 0.3, 1) both`,
  };

  const content: React.CSSProperties = {
    flex: 1,
    padding: isCompact ? "10px 12px" : "12px 14px",
    display: "flex",
    flexDirection: "column",
    gap: isCompact ? 4 : 6,
    minWidth: 0,
  };

  const headerRow: React.CSSProperties = {
    display: "flex",
    alignItems: "center",
    gap: 6,
  };

  const chip = (text: string, tone?: string): React.CSSProperties => ({
    fontFamily: "var(--mono)",
    fontSize: 9,
    color: tone || "var(--ink3)",
    letterSpacing: 0.4,
    textTransform: "uppercase",
    lineHeight: 1.1,
    padding: "2px 6px",
    border: `1px solid ${tone || "var(--border)"}`,
    borderRadius: 2,
    whiteSpace: "nowrap",
    background: "white",
    ...(text ? {} : {}),
  });

  const titleStyle: React.CSSProperties = {
    fontSize: isCompact ? 12 : 14,
    fontWeight: 600,
    lineHeight: 1.25,
    color: "var(--ink)",
    whiteSpace: "nowrap",
    overflow: "hidden",
    textOverflow: "ellipsis",
  };

  const bodyStyle: React.CSSProperties = {
    fontSize: isCompact ? 11 : 12,
    color: "var(--ink2)",
    lineHeight: 1.5,
    display: "-webkit-box",
    WebkitLineClamp: isCompact ? 2 : 3,
    WebkitBoxOrient: "vertical",
    overflow: "hidden",
  };

  const monoMuted: React.CSSProperties = {
    fontFamily: "var(--mono)",
    fontSize: 9,
    color: "var(--ink3)",
    letterSpacing: 0.3,
  };

  const typeLabel = TYPE_LABEL[type] || type;
  const runtime = String(meta.runtime || "");
  const state = String(meta.state || "");
  const ptr = String(meta.ptr || "");
  const tool = String(meta.toolName || meta.tool_name || "");
  const sourcePath = String(meta.sourcePath || meta.source_path || "");
  const model = String(meta.model || "");
  const harness = String(meta.harness || "");
  const sessionCount = Number(meta.sessionCount ?? 0);
  const projectCount = Number(meta.projectCount ?? 0);
  const taskCount = Number(meta.taskCount ?? 0);
  const messageCount = Number(meta.messageCount ?? 0);
  const updatedAt = meta.updatedAt || meta.last_seen_at;

  const onEnter = () => onHover(node);
  const onLeave = () => onHover(null);
  const onClick = (e: React.MouseEvent) => {
    e.stopPropagation();
    onSelect(node);
  };

  return (
    <div
      style={baseStyle}
      onClick={onClick}
      onMouseEnter={onEnter}
      onMouseLeave={onLeave}
      data-canvas-draggable="false"
      data-node-id={node.id}
      className="dhee-node-card"
    >
      <div style={content}>
        <div style={headerRow}>
          <span
            style={{
              width: 8,
              height: 8,
              borderRadius: "50%",
              background: accent,
              flexShrink: 0,
            }}
          />
          <span style={{ ...monoMuted, color: accent }}>{typeLabel}</span>
          {node.status ? <span style={chip(node.status)}>{node.status}</span> : null}
          {state ? <span style={chip(state)}>{state}</span> : null}
          {runtime ? <span style={chip(runtime)}>{runtime}</span> : null}
          {harness && !runtime ? <span style={chip(harness)}>{harness}</span> : null}
          {type === "session" && meta.isCurrent ? (
            <span
              style={{
                width: 7,
                height: 7,
                borderRadius: "50%",
                background: "var(--green)",
                boxShadow: "0 0 0 3px rgba(31,169,113,0.22)",
                marginLeft: "auto",
              }}
            />
          ) : null}
        </div>

        <div style={titleStyle}>{node.label || "(unnamed)"}</div>

        {node.subLabel ? (
          <div style={{ ...monoMuted, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>
            {node.subLabel}
          </div>
        ) : null}

        {/* Type-specific body */}
        {!isCompact && node.body ? <div style={bodyStyle}>{node.body}</div> : null}

        {type === "workspace" && !isCompact ? (
          <div style={{ display: "flex", gap: 12, ...monoMuted }}>
            <span>{projectCount || "—"} projects</span>
            <span>{sessionCount || "—"} sessions</span>
          </div>
        ) : null}

        {type === "project" && !isCompact ? (
          <div style={{ display: "flex", gap: 12, ...monoMuted }}>
            <span>{sessionCount || "—"} sessions</span>
            <span>{taskCount || "—"} tasks</span>
          </div>
        ) : null}

        {type === "session" && !isCompact ? (
          <div style={{ display: "flex", gap: 12, ...monoMuted }}>
            {model ? <span>{clamp(model, 22)}</span> : null}
            {updatedAt ? <span>{fmtTime(updatedAt)}</span> : null}
          </div>
        ) : null}

        {type === "task" && !isCompact ? (
          <div style={{ display: "flex", gap: 12, ...monoMuted }}>
            {messageCount ? <span>{messageCount} messages</span> : null}
            {updatedAt ? <span>{fmtTime(updatedAt)}</span> : null}
          </div>
        ) : null}

        {type === "result" ? (
          <div style={{ display: "flex", gap: 8, alignItems: "center", ...monoMuted }}>
            {tool ? <span>{tool}</span> : null}
            {ptr ? <span>{ptr}</span> : null}
            {sourcePath ? (
              <span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                · {sourcePath.split("/").pop()}
              </span>
            ) : null}
          </div>
        ) : null}

        {type === "broadcast" ? (
          <div style={{ display: "flex", gap: 8, alignItems: "center", ...monoMuted }}>
            {String(meta.sourceChannel || meta.sourceProject || "") ? (
              <span>from {String(meta.sourceChannel || meta.sourceProject || "")}</span>
            ) : null}
            {String(meta.targetProject || "") ? (
              <span>→ {String(meta.targetProject)}</span>
            ) : null}
          </div>
        ) : null}
      </div>
    </div>
  );
}

export const NodeCard = memo(RawNodeCard);
