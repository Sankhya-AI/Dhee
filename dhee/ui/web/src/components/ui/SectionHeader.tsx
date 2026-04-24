export function SectionHeader({ label, sub }: { label: string; sub?: string }) {
  return (
    <div
      style={{
        display: "flex",
        alignItems: "baseline",
        gap: 10,
        marginBottom: 12,
      }}
    >
      <span
        style={{
          fontFamily: "var(--mono)",
          fontSize: 9,
          fontWeight: 700,
          color: "var(--ink3)",
          letterSpacing: "0.1em",
          textTransform: "uppercase",
        }}
      >
        {label}
      </span>
      {sub && (
        <span
          style={{
            fontFamily: "var(--mono)",
            fontSize: 9,
            color: "var(--border2)",
          }}
        >
          {sub}
        </span>
      )}
    </div>
  );
}
