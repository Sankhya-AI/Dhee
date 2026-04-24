import { useEffect, useState } from "react";
import { api } from "../api";
import { SectionHeader } from "../components/ui/SectionHeader";
import type {
  EvolutionEvent,
  MetaBuddhiSnapshot,
  PolicyRow,
} from "../types";

const typeIcon: Record<string, string> = {
  tune: "◈",
  commit: "✓",
  rollback: "↺",
  nididhyasana: "≡",
  promotion: "↑",
};
const typeColor: Record<string, string> = {
  tune: "var(--accent)",
  commit: "var(--green)",
  rollback: "var(--rose)",
  nididhyasana: "var(--indigo)",
  promotion: "var(--green)",
};
const typeBg: Record<string, string> = {
  tune: "oklch(0.97 0.04 36)",
  commit: "oklch(0.96 0.06 145)",
  rollback: "oklch(0.97 0.04 10)",
  nididhyasana: "oklch(0.96 0.04 265)",
  promotion: "oklch(0.96 0.06 145)",
};

export function EvolutionView() {
  const [events, setEvents] = useState<EvolutionEvent[]>([]);
  const [eventsLive, setEventsLive] = useState(false);
  const [meta, setMeta] = useState<MetaBuddhiSnapshot | null>(null);
  const [policies, setPolicies] = useState<PolicyRow[]>([]);
  const [selectedEvt, setSelectedEvt] = useState<string | null>(null);

  useEffect(() => {
    (async () => {
      try {
        const [e, m, p] = await Promise.all([
          api.evolution(),
          api.metaBuddhi(),
          api.routerPolicy(),
        ]);
        setEvents(e.events || []);
        setEventsLive(e.live);
        setMeta(m);
        setPolicies(p.policies || []);
      } catch {}
    })();
  }, []);

  return (
    <div
      style={{
        display: "flex",
        flexDirection: "column",
        height: "100%",
        overflowY: "auto",
      }}
    >
      <div
        style={{
          borderBottom: "1px solid var(--border)",
          padding: "0 24px",
          height: 48,
          display: "flex",
          alignItems: "center",
          gap: 10,
          flexShrink: 0,
        }}
      >
        <span
          style={{
            fontFamily: "var(--mono)",
            fontSize: 11,
            fontWeight: 700,
            letterSpacing: "0.06em",
          }}
        >
          EVOLUTION
        </span>
        <div style={{ display: "flex", alignItems: "center", gap: 5 }}>
          <div
            style={{
              width: 6,
              height: 6,
              borderRadius: "50%",
              background:
                meta?.status === "active" ? "var(--green)" : "var(--ink3)",
            }}
          />
          <span
            style={{
              fontFamily: "var(--mono)",
              fontSize: 10,
              color:
                meta?.status === "active" ? "var(--green)" : "var(--ink3)",
            }}
          >
            MetaBuddhi {meta?.status ?? "unknown"}
          </span>
        </div>
        <span
          style={{
            fontFamily: "var(--mono)",
            fontSize: 10,
            color: "var(--ink3)",
            marginLeft: 4,
          }}
        >
          · strategy: {meta?.strategy ?? "—"}
        </span>
      </div>

      <div style={{ padding: "24px", maxWidth: 900 }}>
        <div
          style={{
            display: "grid",
            gridTemplateColumns: "1fr 1fr",
            gap: 16,
            marginBottom: 28,
          }}
        >
          <div
            style={{
              border: "1px solid var(--border)",
              padding: "18px",
              background: "white",
            }}
          >
            <div
              style={{
                fontFamily: "var(--mono)",
                fontSize: 9,
                color: "var(--ink3)",
                letterSpacing: "0.08em",
                marginBottom: 14,
              }}
            >
              METABUDDHI — COGNITIVE ENGINE
            </div>
            <div style={{ display: "flex", gap: 20, marginBottom: 16 }}>
              <div>
                <div
                  style={{
                    fontFamily: "var(--mono)",
                    fontSize: 28,
                    fontWeight: 700,
                    color: "var(--ink)",
                    lineHeight: 1,
                  }}
                >
                  {meta?.totalInsights ?? 0}
                </div>
                <div
                  style={{
                    fontSize: 11,
                    color: "var(--ink3)",
                    marginTop: 3,
                  }}
                >
                  total insights
                </div>
              </div>
              <div>
                <div
                  style={{
                    fontFamily: "var(--mono)",
                    fontSize: 28,
                    fontWeight: 700,
                    color: "var(--accent)",
                    lineHeight: 1,
                  }}
                >
                  {meta?.sessionInsights ?? 0}
                </div>
                <div
                  style={{
                    fontSize: 11,
                    color: "var(--ink3)",
                    marginTop: 3,
                  }}
                >
                  this session
                </div>
              </div>
              {meta && meta.pendingProposals > 0 && (
                <div>
                  <div
                    style={{
                      fontFamily: "var(--mono)",
                      fontSize: 28,
                      fontWeight: 700,
                      color: "var(--indigo)",
                      lineHeight: 1,
                    }}
                  >
                    {meta.pendingProposals}
                  </div>
                  <div
                    style={{
                      fontSize: 11,
                      color: "var(--ink3)",
                      marginTop: 3,
                    }}
                  >
                    pending
                  </div>
                </div>
              )}
            </div>
            <div
              style={{
                fontSize: 12,
                color: "var(--ink2)",
                marginBottom: 14,
              }}
            >
              Watches expansion events → self-tunes router policy. No config to
              maintain.
            </div>
            <div
              style={{
                fontFamily: "var(--mono)",
                fontSize: 9,
                color: "var(--ink3)",
                marginBottom: 6,
              }}
            >
              CURRENT CYCLE
            </div>
            <div style={{ display: "flex", alignItems: "center", gap: 0 }}>
              {["PROPOSE", "ASSESS", "COMMIT"].map((s, i) => (
                <span key={s} style={{ display: "flex", alignItems: "center" }}>
                  <div
                    style={{
                      padding: "5px 10px",
                      background:
                        i === 1 ? "var(--indigo)" : "var(--surface2)",
                      color: i === 1 ? "white" : "var(--ink3)",
                      fontFamily: "var(--mono)",
                      fontSize: 9,
                      fontWeight: i === 1 ? 700 : 400,
                    }}
                  >
                    {s}
                  </div>
                  {i < 2 && (
                    <div
                      style={{
                        width: 20,
                        height: 1,
                        background:
                          i === 0 ? "var(--indigo)" : "var(--border)",
                      }}
                    />
                  )}
                </span>
              ))}
              <div
                style={{
                  width: 1,
                  height: 28,
                  background: "var(--border)",
                  margin: "0 0 0 8px",
                }}
              />
              <div
                style={{
                  padding: "5px 10px",
                  background: "var(--surface2)",
                  color: "var(--ink3)",
                  fontFamily: "var(--mono)",
                  fontSize: 9,
                  marginLeft: 8,
                }}
              >
                ROLLBACK
              </div>
            </div>
            <div
              style={{
                marginTop: 10,
                fontFamily: "var(--mono)",
                fontSize: 9,
                color: "var(--ink3)",
              }}
            >
              Guardrail: single-group regression threshold −0.06
            </div>
          </div>

          <div
            style={{
              border: "1px solid var(--border)",
              padding: "18px",
              background: "white",
            }}
          >
            <div
              style={{
                fontFamily: "var(--mono)",
                fontSize: 9,
                color: "var(--ink3)",
                letterSpacing: "0.08em",
                marginBottom: 14,
              }}
            >
              NIDIDHYASANA — TRAINING GATE
            </div>
            <div
              style={{
                fontSize: 13,
                color: "var(--ink)",
                lineHeight: 1.6,
                marginBottom: 14,
              }}
            >
              Gates strategy training at session boundaries. A candidate only
              promotes when it beats the incumbent by ≥0.02 on the held-out
              corpus.
            </div>
            <div
              style={{
                fontFamily: "var(--mono)",
                fontSize: 11,
                color: "var(--ink2)",
                marginBottom: 12,
              }}
            >
              Last gate:{" "}
              <span style={{ color: "var(--ink)" }}>
                {meta?.lastGate || "—"}
              </span>
            </div>
            <div
              style={{
                fontFamily: "var(--mono)",
                fontSize: 9,
                color: "var(--ink3)",
                marginBottom: 8,
              }}
            >
              CONFIDENCE BY INTENT CLASS
            </div>
            {(meta?.confidenceGroups || []).map((g) => (
              <div
                key={g.group}
                style={{
                  display: "flex",
                  gap: 10,
                  alignItems: "center",
                  marginBottom: 6,
                }}
              >
                <span
                  style={{
                    width: 88,
                    fontFamily: "var(--mono)",
                    fontSize: 10,
                    color: "var(--ink2)",
                  }}
                >
                  {g.group}
                </span>
                <div
                  style={{
                    flex: 1,
                    height: 4,
                    background: "var(--surface2)",
                  }}
                >
                  <div
                    style={{
                      height: "100%",
                      width: `${g.confidence * 100}%`,
                      background:
                        g.confidence > 0.8 ? "var(--green)" : "var(--accent)",
                    }}
                  />
                </div>
                <span
                  style={{
                    fontFamily: "var(--mono)",
                    fontSize: 9,
                    color: "var(--ink3)",
                    width: 32,
                  }}
                >
                  {Math.round(g.confidence * 100)}%
                </span>
                <span
                  style={{
                    fontSize: 10,
                    color:
                      g.trend === "up" ? "var(--green)" : "var(--ink3)",
                  }}
                >
                  {g.trend === "up" ? "↑" : "—"}
                </span>
              </div>
            ))}
          </div>
        </div>

        <div style={{ marginBottom: 28 }}>
          <SectionHeader
            label="Evolution Timeline"
            sub={eventsLive ? "samskara log" : "no log yet"}
          />
          <div style={{ border: "1px solid var(--border)", background: "white" }}>
            {events.length === 0 && (
              <div
                style={{
                  padding: "20px",
                  fontFamily: "var(--mono)",
                  fontSize: 11,
                  color: "var(--ink3)",
                }}
              >
                No evolution events recorded yet.
              </div>
            )}
            {events.map((ev, i) => {
              const ic = typeIcon[ev.type] || "·";
              const tc = typeColor[ev.type] || "var(--ink3)";
              const bg = typeBg[ev.type] || "transparent";
              const isSelected = selectedEvt === ev.id;
              return (
                <div
                  key={ev.id}
                  onClick={() =>
                    setSelectedEvt(isSelected ? null : ev.id)
                  }
                  style={{
                    display: "flex",
                    gap: 14,
                    padding: "12px 18px",
                    borderBottom:
                      i < events.length - 1
                        ? "1px solid var(--surface2)"
                        : "none",
                    cursor: "pointer",
                    background: isSelected ? bg : "transparent",
                    transition: "background 0.12s",
                  }}
                >
                  <span
                    style={{
                      fontFamily: "var(--mono)",
                      fontSize: 9,
                      color: "var(--ink3)",
                      width: 120,
                      flexShrink: 0,
                      paddingTop: 2,
                    }}
                  >
                    {ev.time}
                  </span>
                  <span
                    style={{
                      width: 16,
                      textAlign: "center",
                      color: tc,
                      fontWeight: 700,
                      flexShrink: 0,
                    }}
                  >
                    {ic}
                  </span>
                  <div style={{ flex: 1 }}>
                    <div
                      style={{
                        fontSize: 13,
                        fontWeight: 500,
                        color: tc,
                        marginBottom: 3,
                      }}
                    >
                      {ev.label}
                    </div>
                    <div
                      style={{
                        fontSize: 12,
                        color: "var(--ink3)",
                        lineHeight: 1.5,
                      }}
                    >
                      {ev.detail}
                    </div>
                  </div>
                  <div
                    style={{
                      width: 7,
                      height: 7,
                      borderRadius: "50%",
                      background:
                        ev.impact === "positive"
                          ? "var(--green)"
                          : ev.impact === "negative"
                          ? "var(--rose)"
                          : "var(--border2)",
                      marginTop: 5,
                      flexShrink: 0,
                    }}
                  />
                </div>
              );
            })}
          </div>
        </div>

        {policies.length > 0 && (
          <div>
            <SectionHeader
              label="Intent Class · Expansion Rate → Depth"
              sub="orange = auto-tuned this session"
            />
            <div
              style={{
                display: "grid",
                gridTemplateColumns: `repeat(${Math.min(
                  6,
                  policies.length
                )}, 1fr)`,
                gap: 10,
              }}
            >
              {policies.slice(0, 6).map((p) => {
                const hi = p.expansionRate > 0.3;
                const lo = p.expansionRate < 0.05;
                return (
                  <div
                    key={`${p.tool}-${p.intent}`}
                    style={{
                      border: `1.5px solid ${
                        p.tuned ? "var(--accent)" : "var(--border)"
                      }`,
                      padding: "14px 10px",
                      background: "white",
                      textAlign: "center",
                    }}
                  >
                    <div
                      style={{
                        fontFamily: "var(--mono)",
                        fontSize: 9,
                        color: p.tuned ? "var(--accent)" : "var(--ink3)",
                        marginBottom: 10,
                        letterSpacing: "0.04em",
                      }}
                    >
                      {p.label.toUpperCase()}
                    </div>
                    <div
                      style={{
                        height: 64,
                        display: "flex",
                        alignItems: "flex-end",
                        justifyContent: "center",
                        gap: 3,
                        marginBottom: 10,
                      }}
                    >
                      {[1, 2, 3].map((d) => (
                        <div
                          key={d}
                          style={{
                            width: 10,
                            height: `${(d / 3) * 60}px`,
                            background:
                              d <= p.depth
                                ? p.tuned
                                  ? "var(--accent)"
                                  : "var(--ink2)"
                                : "var(--surface2)",
                          }}
                        />
                      ))}
                    </div>
                    <div
                      style={{
                        fontFamily: "var(--mono)",
                        fontSize: 14,
                        fontWeight: 700,
                        color: hi
                          ? "var(--rose)"
                          : lo
                          ? "var(--green)"
                          : "var(--ink)",
                        marginBottom: 2,
                      }}
                    >
                      {Math.round(p.expansionRate * 100)}%
                    </div>
                    <div
                      style={{
                        fontFamily: "var(--mono)",
                        fontSize: 9,
                        color: "var(--ink3)",
                      }}
                    >
                      depth {p.depth}
                    </div>
                    {p.tuned && p.depth !== p.prevDepth && (
                      <div
                        style={{
                          fontFamily: "var(--mono)",
                          fontSize: 8,
                          color: "var(--accent)",
                          marginTop: 4,
                        }}
                      >
                        {p.prevDepth}→{p.depth}
                      </div>
                    )}
                  </div>
                );
              })}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
