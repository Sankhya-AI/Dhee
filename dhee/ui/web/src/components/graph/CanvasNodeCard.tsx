import { StatPill } from "../ui/StatPill";
import type { WorkspaceGraphNode } from "../../types";

const TYPE_BG: Record<string, string> = {
  project: "oklch(0.99 0.02 75)",
  workspace: "oklch(0.99 0.01 250)",
  session: "oklch(0.98 0.02 145)",
  task: "white",
  result: "oklch(0.99 0.01 85)",
  file: "oklch(0.98 0.015 265)",
  asset: "oklch(0.99 0.02 20)",
};

export function CanvasNodeCard({
  node,
  active,
  onClick,
}: {
  node: WorkspaceGraphNode;
  active?: boolean;
  onClick?: () => void;
}) {
  const accent = node.accent || "var(--accent)";
  const meta = node.meta || {};
  const plan = Array.isArray(meta.plan) ? meta.plan : [];
  const tools = Array.isArray(meta.tools) ? meta.tools : [];

  return (
    <div
      onClick={onClick}
      style={{
        width: node.type === "result" ? 220 : node.type === "file" ? 190 : 240,
        minHeight: node.type === "file" ? 84 : 122,
        background: TYPE_BG[node.type] || "white",
        border: `1.5px solid ${active ? accent : "var(--border)"}`,
        boxShadow: active ? `0 12px 32px color-mix(in oklch, ${accent} 18%, transparent)` : "0 6px 16px rgba(0,0,0,0.05)",
        cursor: onClick ? "pointer" : "default",
        transition: "border-color 0.14s ease, box-shadow 0.14s ease, transform 0.14s ease",
      }}
    >
      <div style={{ height: 4, background: accent }} />
      <div style={{ padding: "12px 13px 11px" }}>
        <div
          style={{
            display: "flex",
            alignItems: "flex-start",
            justifyContent: "space-between",
            gap: 10,
            marginBottom: 6,
          }}
        >
          <div style={{ minWidth: 0 }}>
            <div
              style={{
                fontSize: 12.5,
                fontWeight: 600,
                lineHeight: 1.35,
                marginBottom: 4,
              }}
            >
              {node.label}
            </div>
            {node.subLabel && (
              <div
                style={{
                  fontFamily: "var(--mono)",
                  fontSize: 9,
                  color: "var(--ink3)",
                  lineHeight: 1.45,
                }}
              >
                {node.subLabel}
              </div>
            )}
          </div>
          <StatPill label={node.type} tone={accent} />
        </div>

        {node.body && (
          <div
            style={{
              fontSize: 11.5,
              color: "var(--ink2)",
              lineHeight: 1.45,
              marginBottom: 8,
              whiteSpace: "pre-wrap",
            }}
          >
            {node.body}
          </div>
        )}

        {(plan.length > 0 || tools.length > 0 || node.status) && (
          <div
            style={{
              display: "flex",
              flexWrap: "wrap",
              gap: 6,
              alignItems: "center",
            }}
          >
            {node.status && <StatPill label={node.status} tone={accent} />}
            {plan.length > 0 && <StatPill label={`${plan.length} plan items`} />}
            {tools.length > 0 && <StatPill label={`${tools.length} tool events`} />}
          </div>
        )}
      </div>
    </div>
  );
}
