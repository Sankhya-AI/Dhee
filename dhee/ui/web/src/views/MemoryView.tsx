import { useEffect, useMemo, useState } from "react";
import { api } from "../api";
import { DecayBar } from "../components/ui/DecayBar";
import { SectionHeader } from "../components/ui/SectionHeader";
import { TierBadge } from "../components/ui/TierBadge";
import type {
  ActiveCaptureRecord,
  CaptureTimelineItem,
  Engram,
  MemoryNowSnapshot,
  Tier,
} from "../types";

const retentionInfo: Record<string, string> = {
  canonical: "forever",
  high: "180 days",
  medium: "60 days",
  "short-term": "7 days",
  avoid: "never evict",
};

export function MemoryView({
  onMemoryCountChange,
}: {
  onMemoryCountChange?: (n: number) => void;
}) {
  const [memories, setMemories] = useState<Engram[]>([]);
  const [live, setLive] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [selectedTier, setSelectedTier] = useState<Tier | "all">("all");
  const [search, setSearch] = useState("");
  const [expandedId, setExpandedId] = useState<string | null>(null);
  const [addMode, setAddMode] = useState(false);
  const [newText, setNewText] = useState("");
  const [rechunking, setRechunking] = useState<string | null>(null);
  const [memoryNow, setMemoryNow] = useState<MemoryNowSnapshot | null>(null);
  const [timeline, setTimeline] = useState<CaptureTimelineItem[]>([]);

  const reload = async () => {
    setLoading(true);
    try {
      const [res, now, captureTimeline] = await Promise.all([
        api.listMemories(),
        api.memoryNow().catch(() => null),
        api.captureTimeline(18).catch(() => null),
      ]);
      setMemories(res.engrams || []);
      setLive(res.live);
      setError(res.error || null);
      onMemoryCountChange?.(res.engrams?.length || 0);
      setMemoryNow(now);
      setTimeline(captureTimeline?.items || []);
    } catch (e) {
      setError(String(e));
      setLive(false);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    reload();
    const timer = window.setInterval(() => {
      void reload();
    }, 3500);
    return () => window.clearInterval(timer);
  }, []);

  const tierCounts = useMemo(() => {
    const c: Record<string, number> = {};
    memories.forEach((m) => {
      c[m.tier] = (c[m.tier] || 0) + 1;
    });
    return c;
  }, [memories]);

  const filtered = useMemo(() => {
    let m = memories;
    if (selectedTier !== "all") m = m.filter((x) => x.tier === selectedTier);
    if (search.trim())
      m = m.filter(
        (x) =>
          x.content.toLowerCase().includes(search.toLowerCase()) ||
          x.tags.some((t) => t.includes(search.toLowerCase()))
      );
    return m;
  }, [memories, selectedTier, search]);

  const triggerRechunk = (id: string) => {
    setRechunking(id);
    setTimeout(() => setRechunking(null), 1800);
  };

  const saveMemory = async () => {
    if (!newText.trim()) return;
    try {
      await api.remember(newText.trim(), "short-term", []);
      setNewText("");
      setAddMode(false);
      await reload();
    } catch (e) {
      setError(String(e));
    }
  };

  const archive = async (id: string) => {
    try {
      await api.archiveMemory(id);
      setMemories((m) => m.filter((x) => x.id !== id));
      onMemoryCountChange?.(memories.length - 1);
    } catch (e) {
      setError(String(e));
    }
  };

  const tierList: { id: Tier | "all"; label: string }[] = [
    { id: "all", label: "All memories" },
    { id: "canonical", label: "Canonical" },
    { id: "high", label: "High" },
    { id: "medium", label: "Medium" },
    { id: "short-term", label: "Short-term" },
    { id: "avoid", label: "Avoid" },
  ];

  const activeCapture = memoryNow?.activeCapture || [];
  const recentSessions = memoryNow?.sessions?.slice(0, 3) || [];
  const activeSurfaceCount = activeCapture.reduce(
    (sum, entry) => sum + (entry.graph?.surfaces?.length || 0),
    0
  );
  const recentCaptureItems = timeline
    .filter((item) =>
      ["action", "observation", "artifact", "event"].includes(item.kind)
    )
    .slice(0, 8);

  return (
    <div
      style={{ display: "flex", flexDirection: "column", height: "100%" }}
    >
      <div
        style={{
          borderBottom: "1px solid var(--border)",
          padding: "0 20px",
          height: 48,
          display: "flex",
          alignItems: "center",
          gap: 12,
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
          MEMORY
        </span>
        <span
          style={{
            fontFamily: "var(--mono)",
            fontSize: 10,
            color: "var(--ink3)",
          }}
        >
          {loading
            ? "loading…"
            : `${memories.length} engrams · ${
                tierCounts.canonical || 0
              } canonical · ${memories.reduce(
                (a, m) => a + m.tokens,
                0
              )} tokens indexed${
                activeCapture.length > 0
                  ? ` · ${activeCapture.length} live session${
                      activeCapture.length > 1 ? "s" : ""
                    } · ${activeSurfaceCount} surfaces`
                  : ""
              }`}
        </span>
        {!live && !loading && (
          <span
            style={{
              fontFamily: "var(--mono)",
              fontSize: 9,
              color: "var(--rose)",
              padding: "1px 6px",
              border: "1px solid var(--rose)",
            }}
            title={error || undefined}
          >
            BACKEND NOT LIVE
          </span>
        )}
        <div style={{ marginLeft: "auto", display: "flex", gap: 8 }}>
          <input
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="dhee recall…"
            style={{
              border: "1px solid var(--border)",
              padding: "4px 10px",
              fontFamily: "var(--mono)",
              fontSize: 11,
              width: 180,
              color: "var(--ink)",
              background: "transparent",
            }}
          />
          <button
            onClick={() => setAddMode((a) => !a)}
            style={{
              padding: "4px 12px",
              border: `1px solid ${addMode ? "var(--accent)" : "var(--border)"}`,
              fontFamily: "var(--mono)",
              fontSize: 10,
              color: addMode ? "var(--accent)" : "var(--ink2)",
              background: "transparent",
              cursor: "pointer",
            }}
          >
            + REMEMBER
          </button>
        </div>
      </div>

      {addMode && (
        <div
          style={{
            padding: "12px 20px",
            borderBottom: "1px solid var(--border)",
            background: "var(--surface)",
            display: "flex",
            gap: 10,
          }}
        >
          <textarea
            value={newText}
            onChange={(e) => setNewText(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) saveMemory();
            }}
            placeholder="What should Dhee remember? (⌘↵ to save)"
            rows={2}
            style={{
              flex: 1,
              border: "1px solid var(--border)",
              padding: "8px 10px",
              fontFamily: "var(--font)",
              fontSize: 13,
              color: "var(--ink)",
              background: "white",
              resize: "none",
              outline: "none",
            }}
          />
          <div
            style={{ display: "flex", flexDirection: "column", gap: 5 }}
          >
            <button
              onClick={saveMemory}
              style={{
                padding: "6px 14px",
                background: "var(--ink)",
                color: "var(--bg)",
                fontFamily: "var(--mono)",
                fontSize: 10,
                cursor: "pointer",
              }}
            >
              SAVE
            </button>
            <button
              onClick={() => {
                setAddMode(false);
                setNewText("");
              }}
              style={{
                padding: "6px 14px",
                border: "1px solid var(--border)",
                fontFamily: "var(--mono)",
                fontSize: 10,
                cursor: "pointer",
                color: "var(--ink3)",
                background: "transparent",
              }}
            >
              CANCEL
            </button>
          </div>
        </div>
      )}

      <div style={{ flex: 1, display: "flex", overflow: "hidden" }}>
        <div
          style={{
            width: 168,
            borderRight: "1px solid var(--border)",
            display: "flex",
            flexDirection: "column",
            flexShrink: 0,
            overflowY: "auto",
          }}
        >
          <div style={{ padding: "10px 0" }}>
            {tierList.map((t) => {
              const count =
                t.id === "all" ? memories.length : tierCounts[t.id] || 0;
              const active = selectedTier === t.id;
              return (
                <button
                  key={t.id}
                  onClick={() => setSelectedTier(t.id)}
                  style={{
                    width: "100%",
                    textAlign: "left",
                    padding: "8px 16px",
                    background: active ? "var(--surface)" : "transparent",
                    borderLeft: `3px solid ${
                      active ? "var(--accent)" : "transparent"
                    }`,
                    display: "flex",
                    justifyContent: "space-between",
                    alignItems: "center",
                    cursor: "pointer",
                  }}
                >
                  <span
                    style={{
                      fontSize: 12.5,
                      color: active ? "var(--ink)" : "var(--ink2)",
                    }}
                  >
                    {t.label}
                  </span>
                  <span
                    style={{
                      fontFamily: "var(--mono)",
                      fontSize: 10,
                      color: "var(--ink3)",
                    }}
                  >
                    {count}
                  </span>
                </button>
              );
            })}
          </div>
          <div
            style={{
              margin: "0 16px",
              borderTop: "1px solid var(--border)",
              paddingTop: 14,
            }}
          >
            <div
              style={{
                fontFamily: "var(--mono)",
                fontSize: 9,
                color: "var(--ink3)",
                letterSpacing: "0.06em",
                marginBottom: 8,
              }}
            >
              RETENTION
            </div>
            {Object.entries(retentionInfo).map(([t, r]) => (
              <div
                key={t}
                style={{
                  display: "flex",
                  justifyContent: "space-between",
                  marginBottom: 4,
                }}
              >
                <span style={{ fontSize: 11, color: "var(--ink3)" }}>{t}</span>
                <span
                  style={{
                    fontFamily: "var(--mono)",
                    fontSize: 10,
                    color: "var(--ink2)",
                  }}
                >
                  {r}
                </span>
              </div>
            ))}
          </div>
        </div>

        <div style={{ flex: 1, overflowY: "auto" }}>
          {(activeCapture.length > 0 || recentCaptureItems.length > 0) && (
            <div
              style={{
                padding: "18px 20px",
                borderBottom: "1px solid var(--border)",
                background:
                  "linear-gradient(180deg, oklch(0.98 0.015 80), transparent)",
              }}
            >
              <SectionHeader
                label="Live Capture"
                sub={
                  activeCapture.length > 0
                    ? `${activeCapture.length} active session${
                        activeCapture.length > 1 ? "s" : ""
                      }`
                    : "recent capture timeline"
                }
              />
              {activeCapture.length > 0 && (
                <div
                  style={{
                    display: "grid",
                    gridTemplateColumns: "repeat(auto-fit, minmax(240px, 1fr))",
                    gap: 12,
                    marginBottom: recentCaptureItems.length ? 14 : 0,
                  }}
                >
                  {activeCapture.map((entry) => (
                    <ActiveCaptureCard key={entry.session.id} entry={entry} />
                  ))}
                </div>
              )}
              {recentCaptureItems.length > 0 && (
                <div>
                  <SectionHeader label="Recent Events" />
                  <div style={{ display: "grid", gap: 8 }}>
                    {recentCaptureItems.map((item, index) => (
                      <div
                        key={`${item.kind}:${item.timestamp}:${index}`}
                        style={{
                          padding: "10px 12px",
                          border: "1px solid var(--border)",
                          background: "white",
                          display: "flex",
                          gap: 10,
                          alignItems: "baseline",
                        }}
                      >
                        <span
                          style={{
                            fontFamily: "var(--mono)",
                            fontSize: 9,
                            color: "var(--accent)",
                            minWidth: 82,
                            textTransform: "uppercase",
                          }}
                        >
                          {item.kind}
                        </span>
                        <div
                          style={{
                            flex: 1,
                            fontSize: 12.5,
                            lineHeight: 1.5,
                            color: "var(--ink2)",
                          }}
                        >
                          {_timelineSummary(item)}
                        </div>
                        <span
                          style={{
                            fontFamily: "var(--mono)",
                            fontSize: 9,
                            color: "var(--ink3)",
                          }}
                        >
                          {_clock(item.timestamp)}
                        </span>
                      </div>
                    ))}
                  </div>
                </div>
              )}
            </div>
          )}
          {activeCapture.length === 0 && recentSessions.length > 0 && (
            <div
              style={{
                padding: "14px 20px",
                borderBottom: "1px solid var(--border)",
                background: "var(--surface)",
              }}
            >
              <SectionHeader label="Recent Sessions" />
              <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
                {recentSessions.map((session) => (
                  <span
                    key={session.id}
                    style={{
                      padding: "5px 8px",
                      border: "1px solid var(--border)",
                      fontFamily: "var(--mono)",
                      fontSize: 10,
                      color: "var(--ink2)",
                      background: "white",
                    }}
                  >
                    {session.source_app} · {session.status} · {_clock(session.started_at)}
                  </span>
                ))}
              </div>
            </div>
          )}
          {!loading && filtered.length === 0 && (
            <div
              style={{
                padding: "40px 24px",
                color: "var(--ink3)",
                fontSize: 13,
                textAlign: "center",
              }}
            >
              {memories.length === 0
                ? live
                  ? "No engrams yet — remember something to seed Dhee."
                  : "Backend unreachable — is the Dhee FastAPI bridge running?"
                : "No engrams match — try a different filter or query"}
            </div>
          )}
          {filtered.map((eng) => {
            const isExpanded = expandedId === eng.id;
            return (
              <div
                key={eng.id}
                style={{ borderBottom: "1px solid var(--border)" }}
              >
                <div
                  onClick={() => setExpandedId(isExpanded ? null : eng.id)}
                  style={{
                    padding: "14px 20px",
                    cursor: "pointer",
                    display: "flex",
                    gap: 12,
                    alignItems: "flex-start",
                    transition: "background 0.1s",
                  }}
                  onMouseEnter={(e) =>
                    (e.currentTarget.style.background = "var(--surface)")
                  }
                  onMouseLeave={(e) =>
                    (e.currentTarget.style.background = "transparent")
                  }
                >
                  <TierBadge tier={eng.tier} />
                  <div style={{ flex: 1, minWidth: 0 }}>
                    <div
                      style={{
                        fontSize: 13.5,
                        lineHeight: 1.55,
                        marginBottom: 7,
                        color: "var(--ink)",
                      }}
                    >
                      {eng.content}
                    </div>
                    <div
                      style={{
                        display: "flex",
                        gap: 14,
                        alignItems: "center",
                        flexWrap: "wrap",
                      }}
                    >
                      <span
                        style={{
                          fontFamily: "var(--mono)",
                          fontSize: 9,
                          color: "var(--ink3)",
                        }}
                      >
                        {eng.id}
                      </span>
                      <span
                        style={{
                          fontFamily: "var(--mono)",
                          fontSize: 9,
                          color: "var(--ink3)",
                        }}
                      >
                        {eng.source}
                      </span>
                      <span
                        style={{
                          fontFamily: "var(--mono)",
                          fontSize: 9,
                          color: "var(--ink3)",
                        }}
                      >
                        {eng.created}
                      </span>
                      <DecayBar decay={eng.decay} />
                      {eng.reaffirmed > 0 && (
                        <span
                          style={{
                            fontFamily: "var(--mono)",
                            fontSize: 9,
                            color: "var(--green)",
                          }}
                        >
                          ↑ ×{eng.reaffirmed}
                        </span>
                      )}
                      <span
                        style={{
                          fontFamily: "var(--mono)",
                          fontSize: 9,
                          color: "var(--ink3)",
                        }}
                      >
                        ~{eng.tokens}t
                      </span>
                    </div>
                    {eng.tags.length > 0 && (
                      <div
                        style={{
                          marginTop: 6,
                          display: "flex",
                          gap: 4,
                          flexWrap: "wrap",
                        }}
                      >
                        {eng.tags.map((t) => (
                          <span
                            key={t}
                            style={{
                              padding: "1px 6px",
                              background: "var(--surface2)",
                              border: "1px solid var(--border)",
                              fontFamily: "var(--mono)",
                              fontSize: 9,
                              color: "var(--ink3)",
                            }}
                          >
                            {t}
                          </span>
                        ))}
                      </div>
                    )}
                  </div>
                  {rechunking === eng.id && (
                    <span
                      style={{
                        fontFamily: "var(--mono)",
                        fontSize: 9,
                        color: "var(--accent)",
                        flexShrink: 0,
                        paddingTop: 2,
                      }}
                    >
                      re-chunking…
                    </span>
                  )}
                </div>
                {isExpanded && (
                  <div
                    style={{
                      padding: "0 20px 14px",
                      borderTop: "1px solid var(--surface2)",
                    }}
                  >
                    <div
                      style={{
                        paddingTop: 10,
                        display: "flex",
                        gap: 6,
                        flexWrap: "wrap",
                        marginBottom: 10,
                      }}
                    >
                      {[
                        {
                          label: "↻ RE-CHUNK",
                          fn: () => triggerRechunk(eng.id),
                          color: "var(--ink2)",
                        },
                        {
                          label: "↑ PROMOTE",
                          fn: () => {},
                          color: "var(--green)",
                        },
                        {
                          label: "⊃ SUPERSEDE",
                          fn: () => {},
                          color: "var(--indigo)",
                        },
                        {
                          label: "✕ ARCHIVE",
                          fn: () => archive(eng.id),
                          color: "var(--rose)",
                        },
                      ].map((btn) => (
                        <button
                          key={btn.label}
                          onClick={btn.fn}
                          style={{
                            padding: "3px 9px",
                            border: `1px solid ${btn.color}`,
                            fontFamily: "var(--mono)",
                            fontSize: 9,
                            color: btn.color,
                            background: "transparent",
                            cursor: "pointer",
                          }}
                        >
                          {btn.label}
                        </button>
                      ))}
                    </div>
                    <div
                      style={{
                        padding: "8px 12px",
                        background: "oklch(0.1 0.01 260)",
                        fontFamily: "var(--mono)",
                        fontSize: 11,
                        lineHeight: 1.7,
                      }}
                    >
                      <div style={{ color: "oklch(0.5 0.01 260)" }}>
                        $ dhee why {eng.id}
                      </div>
                      <div
                        style={{
                          color: "oklch(0.75 0.01 260)",
                          marginTop: 4,
                        }}
                      >
                        source: {eng.source}
                        <br />
                        ingested → chunk:{eng.id} → tier:{eng.tier}
                        {eng.reaffirmed > 0
                          ? ` → reaffirmed ×${eng.reaffirmed}`
                          : ""}
                        <br />
                        decay: {Math.round(eng.decay * 100)}% · tokens:{" "}
                        {eng.tokens}
                      </div>
                    </div>
                  </div>
                )}
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
}

function ActiveCaptureCard({ entry }: { entry: ActiveCaptureRecord }) {
  const surfaces = entry.graph?.surfaces || [];
  const observations = entry.graph?.observations || [];
  const artifacts = entry.graph?.artifacts || [];
  return (
    <div
      style={{
        border: "1px solid var(--border)",
        background: "white",
        padding: "12px 14px",
      }}
    >
      <div
        style={{
          display: "flex",
          justifyContent: "space-between",
          gap: 10,
          marginBottom: 8,
        }}
      >
        <div>
          <div style={{ fontSize: 13, fontWeight: 600, marginBottom: 2 }}>
            {entry.session.source_app}
          </div>
          <div
            style={{
              fontFamily: "var(--mono)",
              fontSize: 9,
              color: "var(--ink3)",
            }}
          >
            {_clock(entry.session.started_at)} · {entry.session.namespace}
          </div>
        </div>
        <span
          style={{
            fontFamily: "var(--mono)",
            fontSize: 9,
            color: "var(--green)",
            border: "1px solid var(--green)",
            padding: "2px 6px",
            height: "fit-content",
          }}
        >
          ACTIVE
        </span>
      </div>
      <div
        style={{
          display: "flex",
          gap: 10,
          flexWrap: "wrap",
          marginBottom: 8,
          fontFamily: "var(--mono)",
          fontSize: 10,
          color: "var(--ink2)",
        }}
      >
        <span>{surfaces.length} surfaces</span>
        <span>{observations.length} observations</span>
        <span>{artifacts.length} artifacts</span>
      </div>
      <div style={{ display: "grid", gap: 6 }}>
        {surfaces.slice(0, 3).map((surface) => (
          <div
            key={surface.id}
            style={{
              borderLeft: "2px solid var(--accent)",
              paddingLeft: 8,
            }}
          >
            <div style={{ fontSize: 12.5, color: "var(--ink)" }}>
              {surface.title || surface.url || surface.app_path || surface.id}
            </div>
            <div
              style={{
                fontFamily: "var(--mono)",
                fontSize: 9,
                color: "var(--ink3)",
                marginTop: 2,
              }}
            >
              {surface.surface_type}
              {surface.path_hint?.length ? ` · ${surface.path_hint.join(" / ")}` : ""}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

function _timelineSummary(item: CaptureTimelineItem): string {
  const payload = item.item || {};
  const text =
    String(payload.text || payload.text_payload || payload.label || "").trim();
  if (text) return text.slice(0, 220);
  const actionType = String(payload.action_type || payload.actionType || "").trim();
  const title = String(payload.window_title || payload.title || "").trim();
  const url = String(payload.url || "").trim();
  return [actionType, title, url].filter(Boolean).join(" · ") || item.kind;
}

function _clock(value: string | undefined | null): string {
  if (!value) return "—";
  const dt = new Date(value);
  if (Number.isNaN(dt.getTime())) return "—";
  return dt.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}
