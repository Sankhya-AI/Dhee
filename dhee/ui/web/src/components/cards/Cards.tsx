import { useState } from "react";
import type { SankhyaTask, TaskColor } from "../../types";

export function BrowserCard({
  url,
  title,
  lines,
}: {
  url: string;
  title: string;
  lines: string[];
}) {
  const [open, setOpen] = useState(true);
  return (
    <div
      style={{
        border: "1px solid var(--border)",
        marginTop: 8,
        background: "white",
      }}
    >
      <div
        onClick={() => setOpen((o) => !o)}
        style={{
          borderBottom: open ? "1px solid var(--border)" : "none",
          padding: "6px 10px",
          display: "flex",
          alignItems: "center",
          gap: 8,
          background: "var(--surface)",
          cursor: "pointer",
        }}
      >
        <span
          style={{
            fontFamily: "var(--mono)",
            fontSize: 9,
            color: "var(--ink3)",
            letterSpacing: 1,
          }}
        >
          BROWSER
        </span>
        <span
          style={{
            flex: 1,
            fontFamily: "var(--mono)",
            fontSize: 10,
            color: "var(--ink2)",
            overflow: "hidden",
            textOverflow: "ellipsis",
            whiteSpace: "nowrap",
          }}
        >
          {url}
        </span>
        <span style={{ fontSize: 10, color: "var(--ink3)" }}>
          {open ? "▲" : "▼"}
        </span>
      </div>
      {open && (
        <div style={{ padding: "10px 12px" }}>
          <div style={{ fontWeight: 600, fontSize: 13, marginBottom: 8 }}>
            {title}
          </div>
          {lines.map((l, i) => (
            <div
              key={i}
              style={{ display: "flex", gap: 7, marginBottom: 3 }}
            >
              <span
                style={{
                  color: "var(--accent)",
                  fontFamily: "var(--mono)",
                  fontSize: 9,
                  marginTop: 2,
                  flexShrink: 0,
                }}
              >
                →
              </span>
              <span
                style={{
                  color: "var(--ink2)",
                  fontSize: 12.5,
                  lineHeight: 1.4,
                }}
              >
                {l}
              </span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

export function GrepCard({
  query,
  files,
}: {
  query: string;
  files: { name: string; line: number; match: string; note?: string }[];
}) {
  return (
    <div style={{ border: "1px solid var(--border)", marginTop: 8, background: "white" }}>
      <div
        style={{
          borderBottom: "1px solid var(--border)",
          padding: "6px 10px",
          display: "flex",
          gap: 8,
          alignItems: "center",
          background: "var(--surface)",
        }}
      >
        <span
          style={{
            fontFamily: "var(--mono)",
            fontSize: 9,
            color: "var(--ink3)",
            letterSpacing: 1,
          }}
        >
          GREP
        </span>
        <span
          style={{
            fontFamily: "var(--mono)",
            fontSize: 10,
            color: "var(--accent)",
          }}
        >
          "{query}"
        </span>
        <span
          style={{
            marginLeft: "auto",
            fontFamily: "var(--mono)",
            fontSize: 9,
            color: "var(--ink3)",
          }}
        >
          {files.length} matches
        </span>
      </div>
      <div style={{ fontFamily: "var(--mono)", fontSize: 11.5 }}>
        {files.map((f, i) => (
          <div
            key={i}
            style={{
              padding: "8px 12px",
              borderBottom:
                i < files.length - 1 ? "1px solid var(--surface2)" : "none",
            }}
          >
            <div style={{ marginBottom: 4 }}>
              <span style={{ color: "var(--indigo)", fontWeight: 500 }}>
                {f.name}
              </span>
              <span style={{ color: "var(--ink3)", marginLeft: 4 }}>
                :{f.line}
              </span>
            </div>
            <div
              style={{
                paddingLeft: 10,
                borderLeft: "2px solid var(--border)",
              }}
            >
              <span style={{ color: "var(--ink)" }}>{f.match}</span>
              {f.note && (
                <span style={{ color: "var(--accent)", marginLeft: 4 }}>
                  {f.note}
                </span>
              )}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

export function CodeCard({
  lang,
  lines,
}: {
  lang: string;
  lines: { t: string; c?: "comment" | "bad" | "good" | string }[];
}) {
  return (
    <div
      style={{
        border: "1px solid var(--border)",
        marginTop: 8,
        background: "oklch(0.1 0.01 260)",
      }}
    >
      <div
        style={{
          borderBottom: "1px solid oklch(0.2 0.01 260)",
          padding: "5px 12px",
          display: "flex",
          gap: 8,
          background: "oklch(0.12 0.01 260)",
        }}
      >
        <span
          style={{
            fontFamily: "var(--mono)",
            fontSize: 9,
            color: "oklch(0.5 0.01 260)",
            letterSpacing: 1,
          }}
        >
          CODE
        </span>
        <span
          style={{
            fontFamily: "var(--mono)",
            fontSize: 9,
            color: "var(--accent)",
          }}
        >
          {lang}
        </span>
      </div>
      <div
        style={{
          padding: "10px 14px",
          fontFamily: "var(--mono)",
          fontSize: 12,
          lineHeight: 1.7,
        }}
      >
        {lines.map((l, i) => (
          <div
            key={i}
            style={{
              color:
                l.c === "comment"
                  ? "oklch(0.5 0.01 260)"
                  : l.c === "bad"
                  ? "var(--rose)"
                  : l.c === "good"
                  ? "var(--green-mid)"
                  : "oklch(0.88 0.01 260)",
            }}
          >
            {l.t || " "}
          </div>
        ))}
      </div>
    </div>
  );
}

type DocLine = string | { h: string };

export function DocumentCard({
  title,
  lines,
}: {
  title: string;
  lines: DocLine[];
}) {
  return (
    <div
      style={{
        border: "1px solid var(--border)",
        marginTop: 8,
        background: "white",
      }}
    >
      <div
        style={{
          borderBottom: "1px solid var(--border)",
          padding: "6px 12px",
          display: "flex",
          gap: 8,
          alignItems: "center",
          background: "var(--surface)",
        }}
      >
        <span
          style={{
            fontFamily: "var(--mono)",
            fontSize: 9,
            color: "var(--ink3)",
            letterSpacing: 1,
          }}
        >
          DOCUMENT
        </span>
        <span
          style={{
            fontSize: 11,
            fontWeight: 500,
            fontFamily: "var(--mono)",
            color: "var(--ink2)",
          }}
        >
          {title}
        </span>
      </div>
      <div style={{ padding: "12px 16px", fontSize: 13, lineHeight: 1.65 }}>
        {lines.map((l, i) =>
          typeof l === "object" && "h" in l ? (
            <div
              key={i}
              style={{
                fontWeight: 700,
                marginTop: i > 0 ? 12 : 0,
                marginBottom: 3,
                fontSize: 10.5,
                textTransform: "uppercase",
                letterSpacing: "0.06em",
                color: "var(--ink2)",
              }}
            >
              {l.h}
            </div>
          ) : (
            <div key={i} style={{ color: "var(--ink)" }}>
              {l as string}
            </div>
          )
        )}
      </div>
    </div>
  );
}

export function LinkCard({
  linkedTask,
  preview,
  tasks,
  onSelectTask,
}: {
  linkedTask: string;
  preview: string;
  tasks: SankhyaTask[];
  onSelectTask: (id: string) => void;
}) {
  const linked = tasks.find((t) => t.id === linkedTask);
  if (!linked) return null;
  const colorMap: Record<TaskColor, string> = {
    green: "var(--green)",
    indigo: "var(--indigo)",
    orange: "var(--accent)",
    rose: "var(--rose)",
  };
  const c = colorMap[linked.color] || "var(--accent)";
  return (
    <div
      onClick={() => onSelectTask(linked.id)}
      style={{
        border: `1px solid ${c}`,
        marginTop: 8,
        cursor: "pointer",
        display: "flex",
        gap: 12,
        padding: "9px 12px",
        background: "white",
        transition: "background 0.12s",
      }}
      onMouseEnter={(e) =>
        (e.currentTarget.style.background = "var(--surface)")
      }
      onMouseLeave={(e) => (e.currentTarget.style.background = "white")}
    >
      <div
        style={{
          width: 8,
          height: 8,
          background: c,
          flexShrink: 0,
          marginTop: 3,
        }}
      />
      <div style={{ flex: 1 }}>
        <div
          style={{
            fontFamily: "var(--mono)",
            fontSize: 9,
            color: "var(--ink3)",
            letterSpacing: 1,
            marginBottom: 2,
          }}
        >
          LINKED TASK
        </div>
        <div style={{ fontWeight: 500, fontSize: 13 }}>{linked.title}</div>
        <div style={{ fontSize: 11, color: "var(--ink2)", marginTop: 2 }}>
          {preview}
        </div>
      </div>
      <div style={{ color: c, fontSize: 15, alignSelf: "center" }}>→</div>
    </div>
  );
}
