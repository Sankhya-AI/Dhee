import type { ReactNode } from "react";

type ActionTone = "primary" | "secondary";

export interface FirstRunAction {
  label: string;
  onClick: () => void;
  tone?: ActionTone;
  disabled?: boolean;
}

interface FirstRunPanelProps {
  title?: string;
  eyebrow?: string;
  body?: string;
  actions?: FirstRunAction[];
  commands?: string[];
  aside?: ReactNode;
}

const panelStyle: React.CSSProperties = {
  border: "1px solid var(--border)",
  background: "var(--bg)",
  borderRadius: 8,
  padding: 18,
  width: "min(760px, 100%)",
  boxSizing: "border-box",
  display: "flex",
  flexWrap: "wrap",
  gap: 18,
  boxShadow: "0 10px 28px rgba(20,16,10,0.06)",
};

const monoCaps: React.CSSProperties = {
  fontFamily: "var(--mono)",
  fontSize: 9,
  letterSpacing: "0.08em",
  textTransform: "uppercase",
  color: "var(--ink3)",
};

const actionBase: React.CSSProperties = {
  borderRadius: 5,
  cursor: "pointer",
  fontFamily: "var(--mono)",
  fontSize: 10,
  padding: "8px 11px",
  whiteSpace: "nowrap",
};

function actionStyle(tone: ActionTone = "secondary", disabled?: boolean): React.CSSProperties {
  const primary = tone === "primary";
  return {
    ...actionBase,
    border: `1px solid ${primary ? "var(--ink)" : "var(--border)"}`,
    background: primary ? "var(--ink)" : "white",
    color: primary ? "white" : "var(--accent)",
    opacity: disabled ? 0.55 : 1,
    cursor: disabled ? "not-allowed" : "pointer",
  };
}

export function FirstRunPanel({
  title = "Set up a developer workspace",
  eyebrow = "First run",
  body = "Connect a repo folder, then start Codex or Claude Code from that folder so Dhee can mirror sessions and context.",
  actions = [],
  commands = [
    "dhee onboard --root .",
    "dhee doctor",
  ],
  aside,
}: FirstRunPanelProps) {
  return (
    <section style={panelStyle}>
      <div style={{ flex: "1 1 300px", minWidth: 0 }}>
        <div style={monoCaps}>{eyebrow}</div>
        <h2
          style={{
            margin: "5px 0 7px",
            fontSize: 22,
            lineHeight: 1.15,
            color: "var(--ink)",
            letterSpacing: 0,
          }}
        >
          {title}
        </h2>
        <div
          style={{
            color: "var(--ink2)",
            fontSize: 12.5,
            lineHeight: 1.55,
            maxWidth: 680,
          }}
        >
          {body}
        </div>
        {actions.length ? (
          <div style={{ display: "flex", gap: 8, flexWrap: "wrap", marginTop: 14 }}>
            {actions.map((action) => (
              <button
                key={action.label}
                type="button"
                onClick={action.onClick}
                disabled={action.disabled}
                style={actionStyle(action.tone, action.disabled)}
              >
                {action.label}
              </button>
            ))}
          </div>
        ) : null}
      </div>
      <div
        style={{
          flex: "1 1 260px",
          border: "1px solid var(--border)",
          background: "var(--surface)",
          borderRadius: 6,
          padding: 12,
          minWidth: 0,
        }}
      >
        <div style={{ ...monoCaps, marginBottom: 8 }}>Terminal path</div>
        <div style={{ display: "grid", gap: 7 }}>
          {commands.map((command) => (
            <code
              key={command}
              style={{
                display: "block",
                border: "1px solid var(--border)",
                background: "white",
                borderRadius: 4,
                padding: "8px 9px",
                fontFamily: "var(--mono)",
                fontSize: 10,
                color: "var(--ink)",
                lineHeight: 1.45,
                overflowWrap: "anywhere",
              }}
            >
              {command}
            </code>
          ))}
        </div>
        {aside ? <div style={{ marginTop: 10 }}>{aside}</div> : null}
      </div>
    </section>
  );
}
