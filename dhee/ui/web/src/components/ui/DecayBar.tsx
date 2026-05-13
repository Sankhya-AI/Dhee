export function DecayBar({ decay, width = 56 }: { decay: number; width?: number }) {
  const color =
    decay > 0.8 ? "var(--green)" : decay > 0.5 ? "var(--accent)" : "var(--rose)";
  return (
    <div style={{ display: "flex", alignItems: "center", gap: 5 }}>
      <div
        style={{
          width,
          height: 3,
          background: "var(--surface2)",
          position: "relative",
        }}
      >
        <div
          style={{
            position: "absolute",
            top: 0,
            left: 0,
            height: "100%",
            width: `${decay * 100}%`,
            background: color,
          }}
        />
      </div>
      <span
        style={{
          fontFamily: "var(--mono)",
          fontSize: 9,
          color: "var(--ink3)",
        }}
      >
        {Math.round(decay * 100)}%
      </span>
    </div>
  );
}
