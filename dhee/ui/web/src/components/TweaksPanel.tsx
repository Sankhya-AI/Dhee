import type { Tweaks } from "../types";

export function TweaksPanel({
  tweaks,
  setTweaks,
  visible,
}: {
  tweaks: Tweaks;
  setTweaks: (t: Tweaks) => void;
  visible: boolean;
}) {
  if (!visible) return null;
  const set = <K extends keyof Tweaks>(k: K, v: Tweaks[K]) => {
    const next = { ...tweaks, [k]: v };
    setTweaks(next);
  };
  return (
    <div
      style={{
        position: "fixed",
        bottom: 20,
        right: 20,
        width: 236,
        border: "1px solid var(--border)",
        background: "white",
        zIndex: 1000,
        boxShadow: "0 8px 32px rgba(0,0,0,0.1)",
      }}
    >
      <div
        style={{
          padding: "9px 14px",
          borderBottom: "1px solid var(--border)",
          fontFamily: "var(--mono)",
          fontSize: 10,
          fontWeight: 700,
          letterSpacing: "0.06em",
        }}
      >
        TWEAKS
      </div>
      <div style={{ padding: "14px" }}>
        <div style={{ marginBottom: 14 }}>
          <div
            style={{
              fontFamily: "var(--mono)",
              fontSize: 9,
              color: "var(--ink3)",
              marginBottom: 6,
              textTransform: "uppercase",
            }}
          >
            Accent hue
          </div>
          <input
            type="range"
            min="0"
            max="360"
            value={tweaks.accentHue}
            onChange={(e) => {
              const h = e.target.value;
              set("accentHue", h);
              document.documentElement.style.setProperty(
                "--accent",
                `oklch(0.64 0.18 ${h})`
              );
              document.documentElement.style.setProperty(
                "--accent-dim",
                `oklch(0.97 0.04 ${h})`
              );
            }}
            style={{ width: "100%" }}
          />
          <div
            style={{
              fontFamily: "var(--mono)",
              fontSize: 9,
              color: "var(--ink3)",
              marginTop: 2,
            }}
          >
            hue {tweaks.accentHue}°
          </div>
        </div>
        <div style={{ marginBottom: 14 }}>
          <div
            style={{
              fontFamily: "var(--mono)",
              fontSize: 9,
              color: "var(--ink3)",
              marginBottom: 6,
              textTransform: "uppercase",
            }}
          >
            Compact nav
          </div>
          <button
            onClick={() => set("compactNav", !tweaks.compactNav)}
            style={{
              padding: "4px 10px",
              border: "1px solid var(--border)",
              fontFamily: "var(--mono)",
              fontSize: 10,
              background: tweaks.compactNav ? "var(--ink)" : "transparent",
              color: tweaks.compactNav ? "var(--bg)" : "var(--ink)",
              cursor: "pointer",
            }}
          >
            {tweaks.compactNav ? "ON" : "OFF"}
          </button>
        </div>
        <div style={{ marginBottom: 14 }}>
          <div
            style={{
              fontFamily: "var(--mono)",
              fontSize: 9,
              color: "var(--ink3)",
              marginBottom: 6,
              textTransform: "uppercase",
            }}
          >
            Canvas style
          </div>
          <div style={{ display: "flex", gap: 5 }}>
            {(["dots", "grid"] as const).map((s) => (
              <button
                key={s}
                onClick={() => set("canvasStyle", s)}
                style={{
                  padding: "4px 10px",
                  border: "1px solid var(--border)",
                  fontFamily: "var(--mono)",
                  fontSize: 9,
                  background:
                    tweaks.canvasStyle === s ? "var(--ink)" : "transparent",
                  color: tweaks.canvasStyle === s ? "var(--bg)" : "var(--ink)",
                  cursor: "pointer",
                }}
              >
                {s}
              </button>
            ))}
          </div>
        </div>
        <div>
          <div
            style={{
              fontFamily: "var(--mono)",
              fontSize: 9,
              color: "var(--ink3)",
              marginBottom: 6,
              textTransform: "uppercase",
            }}
          >
            Timestamps
          </div>
          <button
            onClick={() => set("showTimestamps", !tweaks.showTimestamps)}
            style={{
              padding: "4px 10px",
              border: "1px solid var(--border)",
              fontFamily: "var(--mono)",
              fontSize: 10,
              background: tweaks.showTimestamps ? "var(--ink)" : "transparent",
              color: tweaks.showTimestamps ? "var(--bg)" : "var(--ink)",
              cursor: "pointer",
            }}
          >
            {tweaks.showTimestamps ? "ON" : "OFF"}
          </button>
        </div>
      </div>
    </div>
  );
}
