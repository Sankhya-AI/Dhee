import { useEffect, useRef, useState } from "react";
import { api } from "../api";
import type { RouterStats, Viewer } from "../types";

function formatCompact(n: number | undefined | null): string {
  if (!n || n <= 0) return "0";
  return new Intl.NumberFormat("en", {
    notation: "compact",
    maximumFractionDigits: 1,
  }).format(n);
}

function tokensSavedTotal(stats: RouterStats | null): number {
  if (!stats) return 0;
  const session = Number(stats.sessionTokensSaved || 0);
  const enterprise = Number(stats.enterpriseSavedTokens || 0);
  return session + enterprise;
}

function savedPct(stats: RouterStats | null): number {
  if (!stats) return 0;
  const ent = Number(stats.enterpriseSavedPct || 0);
  return ent;
}

interface TopBarProps {
  viewer: Viewer | null;
  routerStats: RouterStats | null;
  onRefresh: () => void;
  onOpenTweaks: () => void;
  onResetWorkspace?: () => void;
}

export function TopBar({
  viewer,
  routerStats,
  onRefresh,
  onOpenTweaks,
  onResetWorkspace,
}: TopBarProps) {
  const [menuOpen, setMenuOpen] = useState(false);
  const [fallbackStats, setFallbackStats] = useState<RouterStats | null>(null);
  const menuRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    if (!menuOpen) return;
    const onClick = (e: MouseEvent) => {
      if (!menuRef.current) return;
      if (!menuRef.current.contains(e.target as Node)) setMenuOpen(false);
    };
    window.addEventListener("mousedown", onClick);
    return () => window.removeEventListener("mousedown", onClick);
  }, [menuOpen]);

  useEffect(() => {
    if (routerStats) {
      setFallbackStats(null);
      return;
    }
    let cancelled = false;
    const load = async () => {
      try {
        const stats = await api.routerStats();
        if (!cancelled) setFallbackStats(stats);
      } catch {}
    };
    void load();
    const timer = window.setInterval(load, 5000);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [routerStats]);

  const orgLabel = viewer?.org_id || "default";
  const projectLabel = viewer?.project_id || null;
  const teamLabel = viewer?.team_id || null;
  const breadcrumb = [orgLabel, projectLabel, teamLabel]
    .filter(Boolean)
    .join(" · ");
  const effectiveStats = routerStats || fallbackStats;
  const totalSaved = tokensSavedTotal(effectiveStats);
  const pct = savedPct(effectiveStats);
  const tooltip = (() => {
    if (!effectiveStats) return "loading";
    const session = Number(effectiveStats.sessionTokensSaved || 0);
    const ent = Number(effectiveStats.enterpriseSavedTokens || 0);
    const raw = Number(effectiveStats.enterpriseRawTokens || 0);
    const summary = Number(effectiveStats.enterpriseSummaryTokens || 0);
    const fallbacks = Number(effectiveStats.enterpriseRawFallbacks || 0);
    const gates = Number(effectiveStats.enterpriseGateSuggestions || 0);
    return `Session: ${formatCompact(session)} · Repo index: ${formatCompact(ent)} · Raw avoided: ${formatCompact(raw)} -> ${formatCompact(summary)} · Fallbacks: ${fallbacks} · Gates: ${gates}`;
  })();

  return (
    <div
      style={{
        height: 32,
        borderBottom: "1px solid var(--border)",
        background: "var(--bg)",
        display: "flex",
        alignItems: "center",
        padding: "0 12px",
        gap: 10,
        flexShrink: 0,
        zIndex: 15,
      }}
    >
      <div
        className="workspace-pill"
        title={breadcrumb}
        style={{
          display: "inline-flex",
          alignItems: "center",
          gap: 6,
          padding: "3px 9px",
          borderRadius: 4,
          background: "var(--surface)",
          border: "1px solid var(--border)",
          fontFamily: "var(--mono)",
          fontSize: 10,
          color: "var(--ink2)",
          letterSpacing: "0.04em",
        }}
      >
        <span
          style={{
            width: 5,
            height: 5,
            borderRadius: "50%",
            background: viewer?.live ? "var(--green)" : "var(--ink3)",
          }}
        />
        <span>{breadcrumb || "no workspace"}</span>
        {viewer?.role ? (
          <span
            style={{
              marginLeft: 6,
              padding: "1px 5px",
              borderRadius: 3,
              background: "var(--surface2)",
              color: "var(--ink2)",
              fontSize: 9,
            }}
          >
            {String(viewer.role).toUpperCase()}
          </span>
        ) : null}
      </div>

      <div style={{ flex: 1 }} />

      <div
        className="tokens-chip"
        title={tooltip}
        style={{
          display: "inline-flex",
          alignItems: "center",
          gap: 6,
          padding: "3px 9px",
          borderRadius: 4,
          background: "var(--accent-dim)",
          border: "1px solid var(--accent)",
          color: "var(--accent)",
          fontFamily: "var(--mono)",
          fontSize: 10,
          letterSpacing: "0.04em",
        }}
      >
        <span style={{ fontSize: 11 }}>↯</span>
        <span>{formatCompact(totalSaved)} saved</span>
        {pct > 0 ? (
          <span style={{ color: "var(--ink3)" }}>· {pct.toFixed(0)}%</span>
        ) : null}
      </div>

      <div
        ref={menuRef}
        style={{ position: "relative", display: "inline-block" }}
      >
        <button
          aria-label="Menu"
          onClick={() => setMenuOpen((v) => !v)}
          style={{
            width: 22,
            height: 22,
            borderRadius: 4,
            background: menuOpen ? "var(--surface2)" : "var(--surface)",
            border: "1px solid var(--border)",
            color: "var(--ink2)",
            fontSize: 12,
            lineHeight: 1,
            display: "inline-flex",
            alignItems: "center",
            justifyContent: "center",
          }}
        >
          ⋮
        </button>
        {menuOpen ? (
          <div
            style={{
              position: "absolute",
              top: "calc(100% + 4px)",
              right: 0,
              minWidth: 180,
              background: "var(--bg)",
              border: "1px solid var(--border)",
              borderRadius: 4,
              boxShadow: "0 6px 18px rgba(20,16,10,0.08)",
              zIndex: 30,
              padding: 4,
              fontFamily: "var(--mono)",
              fontSize: 10,
              letterSpacing: "0.04em",
            }}
          >
            <MenuItem
              label="REFRESH"
              onClick={() => {
                setMenuOpen(false);
                onRefresh();
              }}
            />
            <MenuItem
              label="TWEAKS"
              hint="⌘K"
              onClick={() => {
                setMenuOpen(false);
                onOpenTweaks();
              }}
            />
            {onResetWorkspace ? (
              <>
                <div
                  style={{
                    height: 1,
                    background: "var(--border)",
                    margin: "3px 0",
                  }}
                />
                <MenuItem
                  label="RESET WORKSPACE"
                  onClick={() => {
                    setMenuOpen(false);
                    onResetWorkspace();
                  }}
                  danger
                />
              </>
            ) : null}
            <div
              style={{
                height: 1,
                background: "var(--border)",
                margin: "3px 0",
              }}
            />
            <MenuItem
              label="USER ID"
              hint={viewer?.user_id || "—"}
              onClick={() => setMenuOpen(false)}
              dim
            />
          </div>
        ) : null}
      </div>
    </div>
  );
}

function MenuItem({
  label,
  hint,
  onClick,
  dim,
  danger,
}: {
  label: string;
  hint?: string;
  onClick: () => void;
  dim?: boolean;
  danger?: boolean;
}) {
  return (
    <button
      onClick={onClick}
      style={{
        width: "100%",
        textAlign: "left",
        padding: "5px 8px",
        borderRadius: 3,
        background: "transparent",
        color: danger
          ? "var(--rose)"
          : dim
            ? "var(--ink3)"
            : "var(--ink2)",
        display: "flex",
        justifyContent: "space-between",
        alignItems: "center",
        gap: 8,
      }}
      onMouseEnter={(e) => {
        e.currentTarget.style.background = danger
          ? "var(--rose-dim)"
          : "var(--surface)";
      }}
      onMouseLeave={(e) => {
        e.currentTarget.style.background = "transparent";
      }}
    >
      <span>{label}</span>
      {hint ? (
        <span style={{ color: "var(--ink3)", fontSize: 9 }}>{hint}</span>
      ) : null}
    </button>
  );
}
