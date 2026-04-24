import { useEffect, useMemo, useState } from "react";
import { api } from "../api";
import { TierBadge } from "../components/ui/TierBadge";
import type { Conflict, ConflictSnapshot } from "../types";

const sevColor: Record<string, string> = {
  high: "var(--rose)",
  medium: "var(--accent)",
  low: "var(--indigo)",
};

const ACTIONS = [
  { id: "KEEP A", label: "keep a" },
  { id: "KEEP B", label: "keep b" },
  { id: "MERGE", label: "merge" },
  { id: "ARCHIVE BOTH", label: "archive both" },
] as const;

const EMPTY_SNAPSHOT: ConflictSnapshot = {
  live: false,
  supported: false,
  resolutionMode: "unavailable",
  conflicts: [],
};

export function ConflictView() {
  const [snapshot, setSnapshot] = useState<ConflictSnapshot>(EMPTY_SNAPSHOT);
  const [selected, setSelected] = useState<string | null>(null);
  const [resolved, setResolved] = useState<Record<string, string>>({});
  const [busyAction, setBusyAction] = useState<string | null>(null);
  const [mergeContent, setMergeContent] = useState("");
  const [resolutionReason, setResolutionReason] = useState("");
  const [error, setError] = useState<string | null>(null);

  const loadConflicts = async () => {
    try {
      const response = await api.conflicts();
      setSnapshot(response);
      setSelected((current) => {
        if (!current) return response.conflicts?.[0]?.id || null;
        return response.conflicts.some((item) => item.id === current) ? current : response.conflicts?.[0]?.id || null;
      });
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : String(exc));
    }
  };

  useEffect(() => {
    (async () => {
      await loadConflicts();
    })();
  }, []);

  const conflicts = snapshot.conflicts || [];
  const active = selected ? conflicts.find((c) => c.id === selected) : null;
  const unresolvedCount = useMemo(
    () => conflicts.filter((c) => !resolved[c.id]).length,
    [conflicts, resolved]
  );
  const canResolve = snapshot.supported && snapshot.resolutionMode === "native";

  const resolve = async (id: string, action: string) => {
    if (!canResolve) return;
    if (action === "MERGE" && !mergeContent.trim()) {
      setError("Merged content is required before saving a merge.");
      return;
    }
    setBusyAction(action);
    setError(null);
    try {
      await api.resolveConflictDetailed(id, {
        action,
        merged_content: action === "MERGE" ? mergeContent.trim() : undefined,
        reason: resolutionReason.trim() || undefined,
      });
      setResolved((current) => ({ ...current, [id]: action }));
      setMergeContent("");
      setResolutionReason("");
      await loadConflicts();
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : String(exc));
    } finally {
      setBusyAction(null);
    }
  };

  return (
    <div style={{ display: "flex", height: "100%" }}>
      <div
        style={{
          width: 320,
          borderRight: "1px solid var(--border)",
          display: "flex",
          flexDirection: "column",
          flexShrink: 0,
          background: "white",
        }}
      >
        <div
          style={{
            borderBottom: "1px solid var(--border)",
            padding: "0 16px",
            height: 48,
            display: "flex",
            alignItems: "center",
            gap: 8,
          }}
        >
          <span
            style={{
              fontFamily: "var(--mono)",
              fontSize: 11,
              fontWeight: 700,
            }}
          >
            CONFLICTS
          </span>
          {unresolvedCount > 0 && (
            <span
              style={{
                fontFamily: "var(--mono)",
                fontSize: 9,
                color: "var(--rose)",
                padding: "1px 6px",
                border: "1px solid var(--rose)",
              }}
            >
              {unresolvedCount} open
            </span>
          )}
          <span
            style={{
              marginLeft: "auto",
              fontFamily: "var(--mono)",
              fontSize: 9,
              color: snapshot.live ? "var(--green)" : "var(--ink3)",
            }}
            title={snapshot.live ? "live conflict adapter" : "no live adapter"}
          >
            {snapshot.live ? snapshot.resolutionMode : "offline"}
          </span>
        </div>
        <div style={{ flex: 1, overflowY: "auto" }}>
          {conflicts.length === 0 && (
            <div
              style={{
                padding: "20px",
                fontFamily: "var(--mono)",
                fontSize: 11,
                color: "var(--ink3)",
              }}
            >
              No conflicts detected.
            </div>
          )}
          {conflicts.map((conflict) => {
            const color = sevColor[conflict.severity] || "var(--ink3)";
            const isResolved = Boolean(resolved[conflict.id]);
            return (
              <div
                key={conflict.id}
                onClick={() => {
                  if (isResolved) return;
                  setSelected(selected === conflict.id ? null : conflict.id);
                  setError(null);
                }}
                style={{
                  padding: "14px 16px",
                  borderBottom: "1px solid var(--border)",
                  cursor: isResolved ? "default" : "pointer",
                  borderLeft: `3px solid ${isResolved ? "var(--border)" : color}`,
                  background: selected === conflict.id ? "var(--surface)" : "transparent",
                  opacity: isResolved ? 0.55 : 1,
                  transition: "all 0.1s",
                }}
              >
                <div
                  style={{
                    display: "flex",
                    justifyContent: "space-between",
                    marginBottom: 5,
                  }}
                >
                  <span
                    style={{
                      fontFamily: "var(--mono)",
                      fontSize: 9,
                      color,
                      textTransform: "uppercase",
                      letterSpacing: "0.06em",
                    }}
                  >
                    {conflict.severity}
                  </span>
                  {isResolved && (
                    <span
                      style={{
                        fontFamily: "var(--mono)",
                        fontSize: 9,
                        color: "var(--green)",
                      }}
                    >
                      ✓ {resolved[conflict.id]}
                    </span>
                  )}
                </div>
                <div
                  style={{
                    fontSize: 12.5,
                    lineHeight: 1.45,
                    marginBottom: 5,
                  }}
                >
                  {conflict.reason}
                </div>
                <div
                  style={{
                    fontFamily: "var(--mono)",
                    fontSize: 9,
                    color: "var(--ink3)",
                  }}
                >
                  {conflict.belief_a.source} ↔ {conflict.belief_b.source}
                </div>
              </div>
            );
          })}
        </div>
      </div>

      <div
        style={{
          flex: 1,
          display: "flex",
          flexDirection: "column",
          overflow: "hidden",
          background: "var(--bg)",
        }}
      >
        <div
          style={{
            padding: "16px 24px",
            borderBottom: "1px solid var(--border)",
            background: "white",
          }}
        >
          <div
            style={{
              fontFamily: "var(--mono)",
              fontSize: 10,
              color: "var(--ink3)",
              marginBottom: 6,
            }}
          >
            CONFLICT RESOLUTION
          </div>
          {!canResolve ? (
            <div
              style={{
                border: "1px solid var(--accent)",
                background: "rgba(224, 107, 63, 0.05)",
                color: "var(--ink2)",
                padding: "10px 12px",
                fontSize: 12,
                lineHeight: 1.5,
              }}
            >
              This runtime can surface conflicts, but it cannot persist manual resolutions yet.
              The screen is read-only until Dhee exposes a native conflict resolver for the
              active memory backend.
            </div>
          ) : (
            <div
              style={{
                border: "1px solid var(--green)",
                background: "rgba(29, 128, 52, 0.05)",
                color: "var(--ink2)",
                padding: "10px 12px",
                fontSize: 12,
                lineHeight: 1.5,
              }}
            >
              Native conflict resolution is available in this runtime. Actions below will be
              written back to the underlying Dhee memory backend.
            </div>
          )}
          {error && (
            <div
              style={{
                marginTop: 10,
                border: "1px solid var(--rose)",
                background: "rgba(200, 54, 86, 0.06)",
                color: "var(--rose)",
                padding: "10px 12px",
                fontFamily: "var(--mono)",
                fontSize: 10,
                lineHeight: 1.5,
                whiteSpace: "pre-wrap",
              }}
            >
              {error}
            </div>
          )}
        </div>

        {!active ? (
          <div
            style={{
              flex: 1,
              display: "flex",
              flexDirection: "column",
              alignItems: "center",
              justifyContent: "center",
              gap: 8,
            }}
          >
            <div
              style={{
                fontFamily: "var(--mono)",
                fontSize: 11,
                color: "var(--ink3)",
              }}
            >
              SELECT A CONFLICT TO REVIEW
            </div>
            {Object.keys(resolved).length > 0 && (
              <div style={{ fontSize: 12, color: "var(--green)" }}>
                ✓ {Object.keys(resolved).length} resolved in this session
              </div>
            )}
            {unresolvedCount === 0 && conflicts.length > 0 && (
              <div
                style={{
                  fontSize: 13,
                  color: "var(--green)",
                  marginTop: 4,
                }}
              >
                All conflicts resolved
              </div>
            )}
          </div>
        ) : (
          <div style={{ flex: 1, overflowY: "auto", padding: "24px" }}>
            <div
              style={{
                fontFamily: "var(--mono)",
                fontSize: 9,
                color: "var(--ink3)",
                letterSpacing: "0.06em",
                marginBottom: 18,
                textTransform: "uppercase",
              }}
            >
              Conflict · {active.reason}
            </div>
            <div
              style={{
                display: "grid",
                gridTemplateColumns: "1fr 40px 1fr",
                gap: 0,
                marginBottom: 22,
              }}
            >
              {[active.belief_a, active.belief_b].map((belief, index) => (
                <span key={belief.id} style={{ display: "contents" }}>
                  <div
                    style={{
                      border: "1px solid var(--border)",
                      background: "white",
                      padding: 18,
                    }}
                  >
                    <div
                      style={{
                        display: "flex",
                        justifyContent: "space-between",
                        gap: 12,
                        marginBottom: 12,
                        alignItems: "center",
                      }}
                    >
                      <TierBadge tier={belief.tier} />
                      <span
                        style={{
                          fontFamily: "var(--mono)",
                          fontSize: 9,
                          color: "var(--ink3)",
                        }}
                      >
                        {Math.round((belief.confidence || 0) * 100)}%
                      </span>
                    </div>
                    <div
                      style={{
                        fontSize: 14,
                        lineHeight: 1.7,
                        marginBottom: 16,
                        whiteSpace: "pre-wrap",
                      }}
                    >
                      {belief.content}
                    </div>
                    {(belief.evidence || []).length > 0 && (
                      <div style={{ marginBottom: 12 }}>
                        <div
                          style={{
                            fontFamily: "var(--mono)",
                            fontSize: 9,
                            color: "var(--ink3)",
                            marginBottom: 6,
                          }}
                        >
                          EVIDENCE
                        </div>
                        {(belief.evidence || []).slice(0, 3).map((item) => (
                          <div
                            key={item.id || `${belief.id}:${item.content.slice(0, 12)}`}
                            style={{
                              fontSize: 11,
                              color: "var(--ink2)",
                              lineHeight: 1.5,
                              marginBottom: 6,
                            }}
                          >
                            {item.content}
                          </div>
                        ))}
                      </div>
                    )}
                    <div
                      style={{
                        fontFamily: "var(--mono)",
                        fontSize: 9,
                        color: "var(--ink3)",
                        display: "flex",
                        flexDirection: "column",
                        gap: 4,
                      }}
                    >
                      <span>source · {belief.source}</span>
                      {belief.domain && <span>domain · {belief.domain}</span>}
                      {belief.truthStatus && <span>truth · {belief.truthStatus}</span>}
                      {belief.freshness && <span>freshness · {belief.freshness}</span>}
                      <span>created · {belief.created}</span>
                    </div>
                  </div>
                  {index === 0 && (
                    <div
                      style={{
                        display: "flex",
                        alignItems: "center",
                        justifyContent: "center",
                        fontFamily: "var(--mono)",
                        fontSize: 10,
                        color: "var(--ink3)",
                      }}
                    >
                      ↔
                    </div>
                  )}
                </span>
              ))}
            </div>

            <div style={{ marginBottom: 18 }}>
              <div
                style={{
                  fontFamily: "var(--mono)",
                  fontSize: 10,
                  color: "var(--ink3)",
                  marginBottom: 8,
                }}
              >
                RESOLUTION NOTES
              </div>
              <textarea
                value={resolutionReason}
                onChange={(e) => setResolutionReason(e.target.value)}
                placeholder="Why are you choosing this resolution?"
                rows={2}
                style={{
                  width: "100%",
                  border: "1px solid var(--border)",
                  background: "white",
                  padding: "10px 12px",
                  fontSize: 13,
                  lineHeight: 1.5,
                  marginBottom: 10,
                }}
              />
              <textarea
                value={mergeContent}
                onChange={(e) => setMergeContent(e.target.value)}
                placeholder="For MERGE, write the canonical merged belief Dhee should keep."
                rows={4}
                style={{
                  width: "100%",
                  border: "1px solid var(--border)",
                  background: "white",
                  padding: "10px 12px",
                  fontSize: 13,
                  lineHeight: 1.6,
                }}
              />
            </div>

            <div style={{ display: "flex", gap: 10, flexWrap: "wrap" }}>
              {ACTIONS.map((action) => (
                <button
                  key={action.id}
                  onClick={() => void resolve(active.id, action.id)}
                  disabled={!canResolve || busyAction !== null}
                  style={{
                    padding: "10px 12px",
                    border: "1px solid var(--border)",
                    background: !canResolve
                      ? "var(--surface)"
                      : busyAction === action.id
                        ? "var(--ink)"
                        : "white",
                    color: !canResolve
                      ? "var(--ink3)"
                      : busyAction === action.id
                        ? "white"
                        : "var(--ink)",
                    fontFamily: "var(--mono)",
                    fontSize: 10,
                    cursor: !canResolve || busyAction !== null ? "not-allowed" : "pointer",
                    opacity: !canResolve ? 0.75 : 1,
                  }}
                >
                  {busyAction === action.id ? "saving..." : action.label}
                </button>
              ))}
            </div>

            <div style={{ marginTop: 22, display: "grid", gap: 18 }}>
              {[
                { label: "belief a history", belief: active.belief_a },
                { label: "belief b history", belief: active.belief_b },
              ].map((entry) => (
                <div key={entry.label} style={{ border: "1px solid var(--border)", background: "white", padding: 14 }}>
                  <div
                    style={{
                      fontFamily: "var(--mono)",
                      fontSize: 10,
                      color: "var(--ink3)",
                      marginBottom: 10,
                    }}
                  >
                    {entry.label}
                  </div>
                  {(entry.belief.history || []).length === 0 && (
                    <div style={{ fontSize: 11, color: "var(--ink3)" }}>No history recorded yet.</div>
                  )}
                  {(entry.belief.history || []).slice(0, 6).map((item, index) => (
                    <div key={`${entry.belief.id}:${index}`} style={{ marginBottom: 8 }}>
                      <div style={{ fontSize: 12, fontWeight: 600 }}>{item.event_type || "event"}</div>
                      <div style={{ fontSize: 11, color: "var(--ink2)", lineHeight: 1.5 }}>
                        {item.reason || "No reason recorded."}
                      </div>
                      <div style={{ fontFamily: "var(--mono)", fontSize: 9, color: "var(--ink3)", marginTop: 2 }}>
                        {item.actor || "system"} · {item.created_at || "—"}
                      </div>
                    </div>
                  ))}
                </div>
              ))}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
