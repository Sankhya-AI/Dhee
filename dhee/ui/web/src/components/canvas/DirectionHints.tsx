// ---------------------------------------------------------------------------
// DirectionHints — soft chevrons near each viewport edge when content
// extends off-screen in that direction. Taken from openswarm's
// DirectionHints but stripped of MUI / keyframe shake; we rely on a
// simple opacity ease so the indicators never compete with real content.
// ---------------------------------------------------------------------------

interface Props {
  hasLeft: boolean;
  hasRight: boolean;
  hasUp: boolean;
  hasDown: boolean;
  onPanTo: (direction: "left" | "right" | "up" | "down") => void;
}

const chevron: Record<string, string> = {
  left: "M15 6l-6 6 6 6",
  right: "M9 6l6 6-6 6",
  up: "M6 15l6-6 6 6",
  down: "M6 9l6 6 6-6",
};

const positions: Record<string, React.CSSProperties> = {
  left: { left: 16, top: "50%", transform: "translateY(-50%)" },
  right: { right: 16, top: "50%", transform: "translateY(-50%)" },
  up: { top: 16, left: "50%", transform: "translateX(-50%)" },
  down: { bottom: 78, left: "50%", transform: "translateX(-50%)" },
};

function Hint({ direction, onClick }: { direction: keyof typeof chevron; onClick: () => void }) {
  return (
    <button
      onClick={onClick}
      aria-label={`Pan ${direction}`}
      style={{
        position: "absolute",
        width: 32,
        height: 32,
        borderRadius: "50%",
        background: "rgba(255,255,255,0.9)",
        border: "1px solid rgba(20,16,10,0.12)",
        boxShadow: "0 2px 10px rgba(20,16,10,0.08)",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        color: "var(--ink3)",
        cursor: "pointer",
        opacity: 0.7,
        backdropFilter: "blur(8px)",
        WebkitBackdropFilter: "blur(8px)",
        transition: "opacity 0.18s ease, transform 0.18s ease, color 0.18s ease",
        padding: 0,
        ...positions[direction],
      }}
      onMouseEnter={(e) => {
        e.currentTarget.style.opacity = "1";
        e.currentTarget.style.color = "var(--accent)";
      }}
      onMouseLeave={(e) => {
        e.currentTarget.style.opacity = "0.7";
        e.currentTarget.style.color = "var(--ink3)";
      }}
    >
      <svg width={14} height={14} viewBox="0 0 24 24" fill="none">
        <path d={chevron[direction]} stroke="currentColor" strokeWidth={2} strokeLinecap="round" strokeLinejoin="round" />
      </svg>
    </button>
  );
}

export function DirectionHints({ hasLeft, hasRight, hasUp, hasDown, onPanTo }: Props) {
  return (
    <>
      {hasLeft ? <Hint direction="left" onClick={() => onPanTo("left")} /> : null}
      {hasRight ? <Hint direction="right" onClick={() => onPanTo("right")} /> : null}
      {hasUp ? <Hint direction="up" onClick={() => onPanTo("up")} /> : null}
      {hasDown ? <Hint direction="down" onClick={() => onPanTo("down")} /> : null}
    </>
  );
}
