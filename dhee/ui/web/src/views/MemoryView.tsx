import { useEffect, useMemo, useRef, useState } from "react";
import type { CSSProperties, KeyboardEvent, ReactNode } from "react";
import { api } from "../api";
import { Markdown } from "../components/ui/Markdown";
import type {
  BacklinksSnapshot,
  ContextUsageRow,
  ContextUsageSnapshot,
  ContextItem,
  Engram,
  OrgGraphSnapshot,
  RouterSessionRow,
  RouterSessionsPage,
  Viewer,
} from "../types";

type Selection =
  | { kind: "context"; id: string }
  | { kind: "memory"; id: string }
  | { kind: "new"; id: "new" };

type VaultSection =
  | {
      id: string;
      kind: "context";
      label: string;
      count: number;
      rows: ContextItem[];
    }
  | {
      id: string;
      kind: "memory";
      label: string;
      count: number;
      rows: Engram[];
    };

type VaultTreeRow =
  | { id: string; type: "section"; sectionId: string }
  | { id: string; type: "context"; sectionId: string; item: ContextItem }
  | { id: string; type: "memory"; sectionId: string; memory: Engram };

interface Draft {
  title: string;
  content: string;
  scope: string;
  kind: string;
  project_id: string;
  team_id: string;
  tags: string;
}

const EMPTY_DRAFT: Draft = {
  title: "",
  content: "",
  scope: "team",
  kind: "note",
  project_id: "",
  team_id: "",
  tags: "",
};

const SCOPE_ORDER = [
  "user",
  "company",
  "global_team",
  "project",
  "team",
  "agent",
] as const;

const EMPTY_ROUTER_SESSIONS: RouterSessionsPage = {
  items: [],
  next_cursor: null,
  active_only: false,
  totals: {
    tokens_saved: 0,
    estimated_cost_saved_usd: 0,
    router_calls: 0,
    sessions: 0,
  },
};

const EMPTY_CONTEXT_USAGE: ContextUsageSnapshot = {
  live: false,
  items: [],
  totals: {
    contexts: 0,
    used_contexts: 0,
    usage_count: 0,
    tokens_served: 0,
    proven_tokens_saved: 0,
    theoretical_api_value_usd: 0,
    realized_cost_saved_usd: 0,
  },
};

function normalizeItem(item: ContextItem): ContextItem {
  return { ...item, tags: Array.isArray(item.tags) ? item.tags : [] };
}

function defaultDraft(viewer: Viewer | null): Draft {
  if (viewer?.role === "manager" || viewer?.role === "admin") {
    if (viewer.team_id) {
      return {
        ...EMPTY_DRAFT,
        scope: "team",
        team_id: viewer.team_id,
        project_id: viewer.project_id || "",
      };
    }
    if (viewer.project_id) {
      return { ...EMPTY_DRAFT, scope: "project", project_id: viewer.project_id };
    }
    return { ...EMPTY_DRAFT, scope: "company" };
  }
  return {
    ...EMPTY_DRAFT,
    scope: "user",
    user_id: viewer?.user_id,
  } as Draft;
}

function draftFromItem(item: ContextItem): Draft {
  return {
    title: item.title || "Untitled context",
    content: item.content || item.summary || "",
    scope: item.scope || "team",
    kind: item.kind || "note",
    project_id: item.project_id || "",
    team_id: item.team_id || "",
    tags: (item.tags || []).join(", "),
  };
}

function draftFromMemory(memory: Engram): Draft {
  return {
    title: memoryHeading(memory),
    content: memory.content || "",
    scope: "memory",
    kind: memory.tier,
    project_id: "",
    team_id: "",
    tags: (memory.tags || []).join(", "),
  };
}

function canDirectWrite(viewer: Viewer | null, item?: ContextItem | null, draft?: Draft): boolean {
  if (viewer?.role === "admin" || viewer?.role === "manager") return true;
  const scope = item?.scope || draft?.scope;
  if (scope === "user") return !item?.user_id || item.user_id === viewer?.user_id;
  return false;
}

function statusTone(status?: string): string {
  if (status === "pending_review") return "var(--accent)";
  if (status === "rejected" || status === "inactive") return "var(--rose)";
  return "var(--green)";
}

function parseTags(value: string): string[] {
  return value
    .split(",")
    .map((tag) => tag.trim())
    .filter(Boolean);
}

function shortScope(item: ContextItem): string {
  if (item.team_id) return item.team_id;
  if (item.project_id) return item.project_id;
  if (item.user_id) return "mine";
  return item.scope;
}

function sectionLabel(scope: string): string {
  if (scope === "user") return "Mine";
  if (scope === "company") return "Company";
  if (scope === "global_team") return "Global Teams";
  if (scope === "project") return "Projects";
  if (scope === "team") return "Teams";
  if (scope === "agent") return "Agents";
  return scope;
}

function titleCase(value: string): string {
  return value
    .replace(/[-_]+/g, " ")
    .replace(/\s+/g, " ")
    .trim()
    .replace(/\b\w/g, (ch) => ch.toUpperCase());
}

function memoryHeading(memory: Engram): string {
  const fromContent =
    (memory.content || "")
      .split(/\r?\n/)
      .map((line) => line.trim())
      .find(Boolean) || "";
  return fromContent || memory.tags?.[0] || `${memory.tier} memory`;
}

function memoryCategory(memory: Engram): string {
  const heading = memoryHeading(memory);
  const scoped = heading.match(/^([^>]{3,72})\s*>\s*.+$/);
  if (scoped?.[1]) return scoped[1].trim();
  const hay = [heading, memory.source, ...(memory.tags || [])]
    .join(" ")
    .toLowerCase();
  if (hay.includes("repository guideline") || hay.includes("agents.md")) {
    return "Repository Guidelines";
  }
  if (hay.startsWith("edited ") || hay.includes("/users/") || hay.includes("/tmp/")) {
    return "File Edits";
  }
  if (hay.includes("session") || hay.includes("continuity")) {
    return "Session Continuity";
  }
  const tag = memory.tags?.find((value) => value && value !== memory.tier);
  if (tag) return titleCase(tag);
  return `${titleCase(memory.tier)} Memory`;
}

function memoryTitle(memory: Engram): string {
  const heading = memoryHeading(memory);
  const parts = heading.split(/\s*>\s*/);
  const title = parts.length > 1 ? parts.slice(1).join(" > ") : heading;
  return title.slice(0, 72) || "Memory";
}

function formatCompactTokens(value: number): string {
  if (!value || value <= 0) return "0";
  return new Intl.NumberFormat("en", {
    notation: "compact",
    maximumFractionDigits: 1,
  }).format(value);
}

function formatSavedDollars(value?: number | null): string {
  const dollars = Math.max(0, Number(value || 0));
  if (dollars > 0 && dollars < 0.01) return "<$0.01";
  return new Intl.NumberFormat("en-US", {
    style: "currency",
    currency: "USD",
    maximumFractionDigits: dollars >= 10 ? 0 : 2,
  }).format(dollars);
}

function pricingTitle(row: RouterSessionRow): string {
  const pricing = row.pricing;
  if (!pricing) return "PAYG input-token estimate";
  const rate = pricing.input_cost_per_million;
  const model = pricing.model_family || row.model || "model";
  const provider = pricing.provider || row.runtime || row.agent || "provider";
  const rateText =
    typeof rate === "number" ? `$${rate}/1M input tokens` : "input-token rate";
  return `${provider} · ${model} · ${rateText}`;
}

function contextUsageMeta(item: ContextItem, usage?: ContextUsageRow): string {
  const uses = usage?.usage_count ?? item.usage_count ?? 0;
  const saved = usage?.proven_tokens_saved ?? 0;
  const parts = [`${item.kind} · ${shortScope(item)}`, `${uses} use${uses === 1 ? "" : "s"}`];
  parts.push(saved > 0 ? `${formatCompactTokens(saved)} proven saved` : "no proven $");
  return parts.join(" · ");
}

function runtimeTone(row: RouterSessionRow): string {
  const runtime = String(row.runtime || row.agent || "").toLowerCase();
  if (runtime === "codex") return "var(--indigo)";
  if (runtime === "claude-code" || runtime === "claude") return "var(--accent)";
  return "var(--green)";
}

function sessionTitle(row: RouterSessionRow): string {
  const title = (row.title || "").trim();
  if (title) return title;
  const folder = (row.cwd || row.repo_root || "").split("/").filter(Boolean).pop();
  return folder || row.session_id || "Session";
}

export function MemoryView({
  onMemoryCountChange,
  viewer,
  orgGraph,
  onInboxChanged,
}: {
  onMemoryCountChange?: (n: number) => void;
  viewer?: Viewer | null;
  orgGraph?: OrgGraphSnapshot | null;
  onInboxChanged?: () => Promise<void> | void;
}) {
  const [items, setItems] = useState<ContextItem[]>([]);
  const [memories, setMemories] = useState<Engram[]>([]);
  const [selection, setSelection] = useState<Selection | null>(null);
  const [draft, setDraft] = useState<Draft>(() => defaultDraft(viewer || null));
  const [editing, setEditing] = useState(false);
  const [query, setQuery] = useState("");
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [live, setLive] = useState(true);
  const [links, setLinks] = useState<BacklinksSnapshot | null>(null);
  const [sessionSavings, setSessionSavings] =
    useState<RouterSessionsPage>(EMPTY_ROUTER_SESSIONS);
  const [contextUsage, setContextUsage] =
    useState<ContextUsageSnapshot>(EMPTY_CONTEXT_USAGE);
  const [openSections, setOpenSections] = useState<Record<string, boolean>>({});
  const [focusedTreeId, setFocusedTreeId] = useState("");
  const treeRefs = useRef<Record<string, HTMLButtonElement | null>>({});

  const reload = async () => {
    try {
      const [memoryRes, contextRes, routerSessionsRes, contextUsageRes] = await Promise.all([
        api.listMemories().catch((exc) => ({
          live: false,
          engrams: [] as Engram[],
          count: 0,
          error: String(exc),
        })),
        api.contextItems({ limit: 500 }).catch((exc) => ({
          live: false,
          items: [] as ContextItem[],
          error: String(exc),
        })),
        api.routerSessions({ active: false, limit: 50 }).catch(
          () => EMPTY_ROUTER_SESSIONS
        ),
        api.contextUsage({ limit: 500 }).catch(() => EMPTY_CONTEXT_USAGE),
      ]);
      const rawItems =
        contextRes.items?.length || !orgGraph?.raw?.context_index
          ? contextRes.items || []
          : orgGraph.raw.context_index;
      const usageById = new Map(
        (contextUsageRes.items || []).map((row) => [row.context_id, row])
      );
      setItems(
        rawItems.map(normalizeItem).map((item) => {
          const usage = usageById.get(item.context_id);
          return usage
            ? {
                ...item,
                usage_count: usage.usage_count,
                last_used_at: usage.last_used_at,
                token_cost: usage.token_cost,
              }
            : item;
        })
      );
      setMemories(memoryRes.engrams || []);
      setSessionSavings(routerSessionsRes);
      setContextUsage(contextUsageRes);
      setLive(Boolean(memoryRes.live || contextRes.live || orgGraph?.live));
      setError(contextRes.error || memoryRes.error || null);
      onMemoryCountChange?.(memoryRes.engrams?.length || 0);
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : String(exc));
      setLive(false);
    }
  };

  useEffect(() => {
    void reload();
    const timer = window.setInterval(() => void reload(), 6000);
    return () => window.clearInterval(timer);
    // orgGraph is intentionally not a reload trigger; it is a fallback source.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    if (selection) return;
    const hash = decodeURIComponent(window.location.hash || "");
    const teamFromHash = hash.startsWith("#vault/")
      ? hash.replace("#vault/", "").trim()
      : "";
    const byHash = teamFromHash
      ? items.find((item) => item.team_id === teamFromHash)
      : null;
    if (byHash) {
      setSelection({ kind: "context", id: byHash.context_id });
      return;
    }
    if (items[0]) {
      setSelection({ kind: "context", id: items[0].context_id });
      return;
    }
    if (memories[0]) setSelection({ kind: "memory", id: memories[0].id });
  }, [items, memories, selection]);

  const selectedItem =
    selection?.kind === "context"
      ? items.find((item) => item.context_id === selection.id) || null
      : null;
  const selectedMemory =
    selection?.kind === "memory"
      ? memories.find((memory) => memory.id === selection.id) || null
      : null;

  useEffect(() => {
    if (selectedItem) {
      setDraft(draftFromItem(selectedItem));
      setEditing(false);
      return;
    }
    if (selectedMemory) {
      setDraft(draftFromMemory(selectedMemory));
      setEditing(false);
      return;
    }
    if (selection?.kind === "new") {
      setDraft(defaultDraft(viewer || null));
      setEditing(true);
    }
  }, [selectedItem, selectedMemory, selection?.kind, viewer]);

  useEffect(() => {
    if (!selectedItem) {
      setLinks(null);
      return;
    }
    let mounted = true;
    api
      .backlinks(selectedItem.context_id)
      .then((snapshot) => {
        if (mounted) setLinks(snapshot);
      })
      .catch(() => {
        if (mounted) setLinks(null);
      });
    return () => {
      mounted = false;
    };
  }, [selectedItem?.context_id]);

  const filteredItems = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return items;
    return items.filter((item) => {
      const blob = [
        item.title,
        item.summary,
        item.content,
        item.kind,
        item.scope,
        item.team_id,
        item.project_id,
        ...(item.tags || []),
      ]
        .filter(Boolean)
        .join(" ")
        .toLowerCase();
      return blob.includes(q);
    });
  }, [items, query]);

  const groupedItems = useMemo(() => {
    const groups = new Map<string, ContextItem[]>();
    for (const scope of SCOPE_ORDER) groups.set(scope, []);
    for (const item of filteredItems) {
      const key = item.scope || "team";
      groups.set(key, [...(groups.get(key) || []), item]);
    }
    return groups;
  }, [filteredItems]);

  const filteredMemories = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return memories;
    return memories.filter((memory) => {
      const blob = [
        memory.content,
        memory.source,
        memory.tier,
        ...(memory.tags || []),
      ]
        .filter(Boolean)
        .join(" ")
        .toLowerCase();
      return blob.includes(q);
    });
  }, [memories, query]);

  const groupedMemories = useMemo(() => {
    const groups = new Map<string, Engram[]>();
    for (const memory of filteredMemories) {
      const key = memoryCategory(memory);
      groups.set(key, [...(groups.get(key) || []), memory]);
    }
    return new Map(
      [...groups.entries()].sort(([a], [b]) => a.localeCompare(b))
    );
  }, [filteredMemories]);

  const vaultSections = useMemo<VaultSection[]>(() => {
    const sections: VaultSection[] = [];
    for (const [scope, rows] of groupedItems.entries()) {
      if (rows.length === 0) continue;
      sections.push({
        id: `context:${scope}`,
        kind: "context",
        label: sectionLabel(scope),
        count: rows.length,
        rows,
      });
    }
    for (const [category, rows] of groupedMemories.entries()) {
      if (rows.length === 0) continue;
      sections.push({
        id: `memory:${category}`,
        kind: "memory",
        label: category,
        count: rows.length,
        rows,
      });
    }
    return sections;
  }, [groupedItems, groupedMemories]);

  const visibleTreeRows = useMemo<VaultTreeRow[]>(() => {
    const rows: VaultTreeRow[] = [];
    for (const section of vaultSections) {
      rows.push({
        id: `section:${section.id}`,
        type: "section",
        sectionId: section.id,
      });
      if (!openSections[section.id]) continue;
      if (section.kind === "context") {
        for (const item of section.rows) {
          rows.push({
            id: `context-item:${item.context_id}`,
            type: "context",
            sectionId: section.id,
            item,
          });
        }
      } else {
        for (const memory of section.rows) {
          rows.push({
            id: `memory-item:${memory.id}`,
            type: "memory",
            sectionId: section.id,
            memory,
          });
        }
      }
    }
    return rows;
  }, [openSections, vaultSections]);

  useEffect(() => {
    if (query.trim()) setOpenSections({});
  }, [query]);

  useEffect(() => {
    if (!visibleTreeRows.length) {
      if (focusedTreeId) setFocusedTreeId("");
      return;
    }
    if (!focusedTreeId || !visibleTreeRows.some((row) => row.id === focusedTreeId)) {
      setFocusedTreeId(visibleTreeRows[0].id);
    }
  }, [focusedTreeId, visibleTreeRows]);

  useEffect(() => {
    if (!focusedTreeId) return;
    treeRefs.current[focusedTreeId]?.focus();
  }, [focusedTreeId]);

  const toggleSection = (sectionId: string, next?: boolean) => {
    setOpenSections((current) => ({
      ...current,
      [sectionId]: typeof next === "boolean" ? next : !current[sectionId],
    }));
  };

  const selectContextItem = (item: ContextItem) => {
    setSelection({ kind: "context", id: item.context_id });
    window.location.hash = `#vault/item/${item.context_id}`;
  };

  const selectMemory = (memory: Engram) => {
    setSelection({ kind: "memory", id: memory.id });
  };

  const focusTreeRow = (rowId: string) => {
    setFocusedTreeId(rowId);
  };

  const focusTreeOffset = (fromId: string, delta: number) => {
    const idx = visibleTreeRows.findIndex((row) => row.id === fromId);
    if (idx < 0) return;
    const next = visibleTreeRows[Math.max(0, Math.min(visibleTreeRows.length - 1, idx + delta))];
    if (next) focusTreeRow(next.id);
  };

  const handleTreeKeyDown = (
    event: KeyboardEvent<HTMLButtonElement>,
    row: VaultTreeRow,
  ) => {
    if (event.key === "ArrowDown") {
      event.preventDefault();
      focusTreeOffset(row.id, 1);
      return;
    }
    if (event.key === "ArrowUp") {
      event.preventDefault();
      focusTreeOffset(row.id, -1);
      return;
    }
    if (event.key === "Home") {
      event.preventDefault();
      if (visibleTreeRows[0]) focusTreeRow(visibleTreeRows[0].id);
      return;
    }
    if (event.key === "End") {
      event.preventDefault();
      const last = visibleTreeRows[visibleTreeRows.length - 1];
      if (last) focusTreeRow(last.id);
      return;
    }
    if (event.key === "ArrowRight" && row.type === "section") {
      event.preventDefault();
      if (!openSections[row.sectionId]) {
        toggleSection(row.sectionId, true);
      } else {
        const child = visibleTreeRows.find(
          (candidate) => candidate.sectionId === row.sectionId && candidate.type !== "section"
        );
        if (child) focusTreeRow(child.id);
      }
      return;
    }
    if (event.key === "ArrowLeft") {
      event.preventDefault();
      if (row.type === "section") {
        if (openSections[row.sectionId]) toggleSection(row.sectionId, false);
      } else {
        focusTreeRow(`section:${row.sectionId}`);
      }
      return;
    }
    if ((event.key === "Enter" || event.key === " ") && row.type === "section") {
      event.preventDefault();
      toggleSection(row.sectionId);
    }
  };

  const wikiResolve = (title: string) => {
    const found = items.find(
      (item) => item.title.toLowerCase() === title.toLowerCase()
    );
    return found ? `#vault/item/${found.context_id}` : null;
  };

  const startNew = () => {
    setSelection({ kind: "new", id: "new" });
  };

  const saveDraft = async () => {
    if (!draft.title.trim() || !draft.content.trim()) {
      setError("Title and content are required.");
      return;
    }
    setBusy("save");
    setError(null);
    const payload = {
      title: draft.title.trim(),
      content: draft.content,
      scope: draft.scope,
      kind: draft.kind || "note",
      project_id: draft.project_id || viewer?.project_id || undefined,
      team_id: draft.team_id || viewer?.team_id || undefined,
      tags: parseTags(draft.tags),
      metadata: { source: "sankhya-vault" },
    };
    try {
      if (canDirectWrite(viewer || null, selectedItem, draft)) {
        const res = await api.upsertContext({
          ...payload,
          context_id: selectedItem?.context_id,
          user_id: draft.scope === "user" ? viewer?.user_id : undefined,
        });
        setSelection({ kind: "context", id: res.item.context_id });
      } else {
        const res = await api.proposeContext({
          ...payload,
          proposed_by_user_id: viewer?.user_id || "developer",
          supersedes_id: selectedItem?.context_id,
        });
        setSelection({ kind: "context", id: res.proposal.context_id });
        await onInboxChanged?.();
      }
      await reload();
      setEditing(false);
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : String(exc));
    } finally {
      setBusy(null);
    }
  };

  const decideProposal = async (decision: "approve" | "reject") => {
    if (!selectedItem) return;
    setBusy(decision);
    try {
      if (decision === "approve") {
        await api.approveProposal(selectedItem.context_id, viewer?.user_id || "manager");
      } else {
        await api.rejectProposal(selectedItem.context_id, viewer?.user_id || "manager");
      }
      await reload();
      await onInboxChanged?.();
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : String(exc));
    } finally {
      setBusy(null);
    }
  };

  const selectedReadOnly = selection?.kind === "memory";
  const directWrite = canDirectWrite(viewer || null, selectedItem, draft);
  const savedSessionRows = useMemo(() => {
    const rows = [...(sessionSavings.items || [])].sort((a, b) => {
      const tokenDelta = (b.tokens_saved || 0) - (a.tokens_saved || 0);
      if (tokenDelta !== 0) return tokenDelta;
      return String(b.updated_at || "").localeCompare(String(a.updated_at || ""));
    });
    return rows.filter((row) => row.tokens_saved > 0).slice(0, 4);
  }, [sessionSavings.items]);
  const contextUsageById = useMemo(
    () => new Map((contextUsage.items || []).map((row) => [row.context_id, row])),
    [contextUsage.items]
  );
  const selectedUsage =
    selectedItem ? contextUsageById.get(selectedItem.context_id) || null : null;
  const frequentContextRows = useMemo(
    () =>
      [...(contextUsage.items || [])]
        .filter((row) => (row.usage_count || 0) > 0)
        .sort((a, b) => {
          const uses = (b.usage_count || 0) - (a.usage_count || 0);
          if (uses !== 0) return uses;
          return (b.proven_tokens_saved || 0) - (a.proven_tokens_saved || 0);
        })
        .slice(0, 5),
    [contextUsage.items]
  );

  return (
    <div className="vault-shell">
      <aside
        className="vault-nav"
      >
        <div className="vault-nav-head">
          <div
            style={{
              display: "flex",
              alignItems: "center",
              justifyContent: "space-between",
              gap: 8,
              marginBottom: 10,
            }}
          >
            <div>
              <div
                style={{
                  fontFamily: "var(--mono)",
                  fontSize: 10,
                  color: "var(--ink3)",
                  letterSpacing: "0.08em",
                  textTransform: "uppercase",
                }}
              >
                Context
              </div>
              <div style={{ fontSize: 14, fontWeight: 600, color: "var(--ink)" }}>
                Manager
              </div>
            </div>
            <button
              onClick={startNew}
              style={{
                padding: "5px 8px",
                border: "1px solid var(--accent)",
                background: "var(--accent-dim)",
                color: "var(--accent)",
                fontFamily: "var(--mono)",
                fontSize: 10,
                borderRadius: 3,
              }}
            >
              NEW
            </button>
          </div>
          <input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="search context or memory"
            style={{
              width: "100%",
              boxSizing: "border-box",
              border: "1px solid var(--border)",
              background: "var(--surface)",
              color: "var(--ink)",
              padding: "8px 9px",
              fontSize: 12,
            }}
          />
          <div
            style={{
              marginTop: 8,
            }}
          >
            <ContextImpactPanel
              rows={frequentContextRows}
              totals={contextUsage.totals}
              onSelect={(contextId) => {
                setSelection({ kind: "context", id: contextId });
                window.location.hash = `#vault/item/${contextId}`;
              }}
            />
            <SessionSavingsPanel
              rows={savedSessionRows}
              totalTokens={sessionSavings.totals?.tokens_saved || 0}
              totalCost={
                sessionSavings.totals?.realized_cost_saved_usd ??
                sessionSavings.totals?.estimated_cost_saved_usd ??
                0
              }
              apiValue={
                sessionSavings.totals?.theoretical_api_value_usd ??
                sessionSavings.totals?.estimated_cost_saved_usd ??
                0
              }
              totalSessions={sessionSavings.totals?.sessions || 0}
              budget={sessionSavings.budget}
            />
          </div>
        </div>
        <div
          className="vault-tree"
          role="tree"
          aria-label="Context categories"
        >
          {vaultSections.map((section) => {
            const sectionRow: VaultTreeRow = {
              id: `section:${section.id}`,
              type: "section",
              sectionId: section.id,
            };
            const isOpen = Boolean(openSections[section.id]);
            const hasSelected =
              section.kind === "context"
                ? section.rows.some(
                    (item) =>
                      selection?.kind === "context" && selection.id === item.context_id
                  )
                : section.rows.some(
                    (memory) =>
                      selection?.kind === "memory" && selection.id === memory.id
                  );
            return (
              <TreeSection
                key={section.id}
                label={section.label}
                count={section.count}
                open={isOpen}
                active={hasSelected}
                focused={focusedTreeId === sectionRow.id}
                buttonRef={(el) => {
                  treeRefs.current[sectionRow.id] = el;
                }}
                onFocus={() => setFocusedTreeId(sectionRow.id)}
                onToggle={() => toggleSection(section.id)}
                onKeyDown={(event) => handleTreeKeyDown(event, sectionRow)}
              >
                {section.kind === "context"
                  ? section.rows.map((item) => {
                      const row: VaultTreeRow = {
                        id: `context-item:${item.context_id}`,
                        type: "context",
                        sectionId: section.id,
                        item,
                      };
                      return (
                        <TreeButton
                          key={item.context_id}
                          treeId={row.id}
                          buttonRef={(el) => {
                            treeRefs.current[row.id] = el;
                          }}
                          focused={focusedTreeId === row.id}
                          active={selection?.kind === "context" && selection.id === item.context_id}
                          dot={statusTone(item.proposal_status || item.status)}
                          title={item.title}
                          meta={contextUsageMeta(item, contextUsageById.get(item.context_id))}
                          onFocus={() => setFocusedTreeId(row.id)}
                          onKeyDown={(event) => handleTreeKeyDown(event, row)}
                          onClick={() => selectContextItem(item)}
                        />
                      );
                    })
                  : section.rows.map((memory) => {
                      const row: VaultTreeRow = {
                        id: `memory-item:${memory.id}`,
                        type: "memory",
                        sectionId: section.id,
                        memory,
                      };
                      return (
                        <TreeButton
                          key={memory.id}
                          treeId={row.id}
                          buttonRef={(el) => {
                            treeRefs.current[row.id] = el;
                          }}
                          focused={focusedTreeId === row.id}
                          active={selection?.kind === "memory" && selection.id === memory.id}
                          dot="var(--indigo)"
                          title={memoryTitle(memory)}
                          meta={`${memory.tier} · ${memory.tokens || 0} tok`}
                          onFocus={() => setFocusedTreeId(row.id)}
                          onKeyDown={(event) => handleTreeKeyDown(event, row)}
                          onClick={() => selectMemory(memory)}
                        />
                      );
                    })}
              </TreeSection>
            );
          })}
        </div>
      </aside>

      <main
        className="vault-main"
      >
        <section className="vault-editor">
          <header
            className="vault-editor-header"
          >
            <div style={{ minWidth: 0 }}>
              <div
                style={{
                  fontFamily: "var(--mono)",
                  fontSize: 9,
                  letterSpacing: "0.08em",
                  color: "var(--ink3)",
                  textTransform: "uppercase",
                }}
              >
                {selectedMemory ? "Legacy memory" : draft.scope || "context"}
                {live ? "" : " · offline"}
              </div>
              <div
                style={{
                  fontSize: 16,
                  fontWeight: 600,
                  color: "var(--ink)",
                  overflow: "hidden",
                  textOverflow: "ellipsis",
                  whiteSpace: "nowrap",
                }}
              >
                {draft.title || "New context item"}
              </div>
            </div>
            <div className="vault-editor-actions">
              {selectedItem?.proposal_status === "pending_review" &&
              (viewer?.role === "manager" || viewer?.role === "admin") ? (
                <>
                  <ActionButton
                    label="APPROVE"
                    tone="green"
                    busy={busy === "approve"}
                    onClick={() => void decideProposal("approve")}
                  />
                  <ActionButton
                    label="REJECT"
                    tone="rose"
                    busy={busy === "reject"}
                    onClick={() => void decideProposal("reject")}
                  />
                </>
              ) : null}
              {!selectedReadOnly ? (
                <>
                  <ActionButton
                    label={editing ? "PREVIEW" : "EDIT"}
                    onClick={() => setEditing((v) => !v)}
                  />
                  <ActionButton
                    label={directWrite ? "SAVE" : "REQUEST"}
                    tone={directWrite ? "accent" : "indigo"}
                    busy={busy === "save"}
                    onClick={() => void saveDraft()}
                  />
                </>
              ) : null}
            </div>
          </header>

          <div
            className={`vault-body${editing && !selectedReadOnly ? " vault-body--editing" : ""}`}
          >
            {editing && !selectedReadOnly ? (
              <div style={{ display: "grid", gap: 10, alignContent: "start" }}>
                <input
                  value={draft.title}
                  onChange={(e) => setDraft((d) => ({ ...d, title: e.target.value }))}
                  placeholder="Context title"
                  style={inputStyle}
                />
                <div className="vault-form-grid">
                  <select
                    value={draft.scope}
                    onChange={(e) => setDraft((d) => ({ ...d, scope: e.target.value }))}
                    style={inputStyle}
                  >
                    <option value="company">company</option>
                    <option value="project">project</option>
                    <option value="global_team">global_team</option>
                    <option value="team">team</option>
                    <option value="user">user</option>
                    <option value="agent">agent</option>
                  </select>
                  <input
                    value={draft.kind}
                    onChange={(e) => setDraft((d) => ({ ...d, kind: e.target.value }))}
                    placeholder="kind: runbook / policy / decision"
                    style={inputStyle}
                  />
                </div>
                <div className="vault-form-grid">
                  <input
                    value={draft.project_id}
                    onChange={(e) => setDraft((d) => ({ ...d, project_id: e.target.value }))}
                    placeholder="project id"
                    style={inputStyle}
                  />
                  <input
                    value={draft.team_id}
                    onChange={(e) => setDraft((d) => ({ ...d, team_id: e.target.value }))}
                    placeholder="team id"
                    style={inputStyle}
                  />
                </div>
                <input
                  value={draft.tags}
                  onChange={(e) => setDraft((d) => ({ ...d, tags: e.target.value }))}
                  placeholder="tags, comma separated"
                  style={inputStyle}
                />
                <textarea
                  value={draft.content}
                  onChange={(e) => setDraft((d) => ({ ...d, content: e.target.value }))}
                  rows={20}
                  placeholder="Write markdown context here..."
                  style={{
                    ...inputStyle,
                    minHeight: 420,
                    resize: "vertical",
                    lineHeight: 1.55,
                    fontFamily: "var(--mono)",
                  }}
                />
              </div>
            ) : null}
            <article
              className="vault-preview"
            >
              <Markdown
                source={draft.content || "_No context selected._"}
                wikiResolve={wikiResolve}
              />
            </article>
          </div>
        </section>

        <aside
          className="vault-meta"
        >
          <MetaBlock label="Scope">
            <Pill>{draft.scope}</Pill>
            {draft.kind ? <Pill>{draft.kind}</Pill> : null}
            {selectedItem?.proposal_status ? (
              <Pill color={statusTone(selectedItem.proposal_status)}>
                {selectedItem.proposal_status}
              </Pill>
            ) : null}
          </MetaBlock>
          <MetaBlock label="Ownership">
            <KV k="org" v={viewer?.org_id} />
            <KV k="project" v={draft.project_id || viewer?.project_id} />
            <KV k="team" v={draft.team_id || viewer?.team_id} />
            <KV k="viewer" v={viewer?.user_id} />
            <KV k="role" v={viewer?.role || "developer"} />
          </MetaBlock>
          <MetaBlock label="Health">
            <KV k="quality" v={selectedItem?.quality_score} />
            <KV k="freshness" v={selectedItem?.freshness_score} />
            <KV k="confidence" v={selectedItem?.confidence} />
            <KV k="token cost" v={selectedItem?.token_cost} />
            <KV k="updated" v={selectedItem?.updated_at || selectedMemory?.created} />
          </MetaBlock>
          {selectedItem ? (
            <MetaBlock label="Usage">
              <KV k="uses" v={selectedUsage?.usage_count ?? selectedItem.usage_count ?? 0} />
              <KV k="last used" v={selectedUsage?.last_used_at ?? selectedItem.last_used_at} />
              <KV k="tokens served" v={selectedUsage?.tokens_served ?? 0} />
              <KV k="proven saved" v={selectedUsage?.proven_tokens_saved ?? 0} />
              <KV
                k="proven $"
                v={formatSavedDollars(selectedUsage?.realized_cost_saved_usd)}
              />
              <KV
                k="evidence"
                v={
                  selectedUsage?.evidence?.has_direct_savings_evidence
                    ? "direct attribution"
                    : "usage only"
                }
              />
            </MetaBlock>
          ) : null}
          <MetaBlock label="Tags">
            {parseTags(draft.tags).length ? (
              parseTags(draft.tags).map((tag) => <Pill key={tag}>{tag}</Pill>)
            ) : (
              <span style={{ color: "var(--ink3)", fontSize: 12 }}>none</span>
            )}
          </MetaBlock>
          <MetaBlock label="Backlinks">
            {links?.backlinks?.length ? (
              links.backlinks.map((link) => (
                <button
                  key={`${link.src}:${link.edge_type}`}
                  onClick={() => setSelection({ kind: "context", id: link.src })}
                  style={linkButtonStyle}
                >
                  {link.src_title || link.src}
                </button>
              ))
            ) : (
              <span style={{ color: "var(--ink3)", fontSize: 12 }}>none</span>
            )}
          </MetaBlock>
          <MetaBlock label="Shares">
            {links?.shares?.length ? (
              links.shares.map((share, idx) => (
                <Pill key={idx}>{String(share.scope || share.team_id || "share")}</Pill>
              ))
            ) : (
              <span style={{ color: "var(--ink3)", fontSize: 12 }}>private</span>
            )}
          </MetaBlock>
          {error ? (
            <div
              style={{
                border: "1px solid var(--rose)",
                color: "var(--rose)",
                background: "var(--rose-dim)",
                padding: 10,
                fontFamily: "var(--mono)",
                fontSize: 10,
                lineHeight: 1.5,
              }}
            >
              {error}
            </div>
          ) : null}
        </aside>
      </main>
    </div>
  );
}

const inputStyle: CSSProperties = {
  width: "100%",
  boxSizing: "border-box",
  border: "1px solid var(--border)",
  background: "white",
  color: "var(--ink)",
  padding: "8px 9px",
  fontSize: 12,
};

const linkButtonStyle: CSSProperties = {
  display: "block",
  width: "100%",
  textAlign: "left",
  border: "1px solid var(--border)",
  background: "var(--surface)",
  color: "var(--ink2)",
  padding: "7px 8px",
  fontSize: 12,
  cursor: "pointer",
};

function TreeSection({
  label,
  count,
  open,
  active,
  focused,
  buttonRef,
  onFocus,
  onToggle,
  onKeyDown,
  children,
}: {
  label: string;
  count: number;
  open: boolean;
  active: boolean;
  focused: boolean;
  buttonRef: (el: HTMLButtonElement | null) => void;
  onFocus: () => void;
  onToggle: () => void;
  onKeyDown: (event: KeyboardEvent<HTMLButtonElement>) => void;
  children: ReactNode;
}) {
  return (
    <div style={{ marginBottom: 6 }}>
      <button
        ref={buttonRef}
        type="button"
        role="treeitem"
        aria-expanded={open}
        aria-selected={active}
        tabIndex={focused ? 0 : -1}
        onFocus={onFocus}
        onClick={onToggle}
        onKeyDown={onKeyDown}
        style={{
          width: "100%",
          border: `1px solid ${active ? "var(--accent)" : "var(--border)"}`,
          background: active ? "var(--accent-dim)" : "var(--surface)",
          color: "var(--ink)",
          borderRadius: 4,
          padding: "7px 8px",
          cursor: "pointer",
          display: "grid",
          gridTemplateColumns: "12px minmax(0, 1fr) auto",
          alignItems: "center",
          gap: 8,
          textAlign: "left",
          fontFamily: "var(--mono)",
          fontSize: 10,
          letterSpacing: "0.04em",
          textTransform: "uppercase",
          outline: focused ? "2px solid var(--accent-dim)" : "none",
        }}
      >
        <span style={{ color: "var(--ink3)" }}>{open ? "v" : ">"}</span>
        <span
          style={{
            overflow: "hidden",
            textOverflow: "ellipsis",
            whiteSpace: "nowrap",
          }}
        >
          {label}
        </span>
        <span style={{ color: "var(--ink3)" }}>{count}</span>
      </button>
      {open ? (
        <div role="group" style={{ display: "grid", gap: 4, margin: "4px 0 8px 14px" }}>
          {children}
        </div>
      ) : null}
    </div>
  );
}

function ContextImpactPanel({
  rows,
  totals,
  onSelect,
}: {
  rows: ContextUsageRow[];
  totals: ContextUsageSnapshot["totals"];
  onSelect: (contextId: string) => void;
}) {
  return (
    <div
      style={{
        border: "1px solid var(--border)",
        background: "white",
        borderRadius: 4,
        padding: 8,
        marginBottom: 8,
      }}
    >
      <div
        style={{
          display: "flex",
          alignItems: "baseline",
          justifyContent: "space-between",
          gap: 8,
        }}
      >
        <span
          style={{
            fontFamily: "var(--mono)",
            fontSize: 9,
            color: "var(--ink3)",
            letterSpacing: "0.04em",
            textTransform: "uppercase",
          }}
        >
          Frequent context
        </span>
        <span style={{ fontSize: 13, fontWeight: 700, color: "var(--ink)" }}>
          {formatCompactTokens(totals?.usage_count || 0)} uses
        </span>
      </div>
      <div
        style={{
          fontFamily: "var(--mono)",
          fontSize: 10,
          color: "var(--ink3)",
          marginTop: 2,
          lineHeight: 1.35,
        }}
      >
        {formatCompactTokens(totals?.tokens_served || 0)} served ·{" "}
        {formatCompactTokens(totals?.proven_tokens_saved || 0)} proven saved ·{" "}
        {formatSavedDollars(totals?.realized_cost_saved_usd)}
      </div>
      <div style={{ display: "grid", gap: 5, marginTop: 8 }}>
        {rows.length ? (
          rows.map((row) => (
            <button
              key={row.context_id}
              type="button"
              onClick={() => onSelect(row.context_id)}
              style={{
                border: "0",
                borderTop: "1px solid var(--border)",
                background: "transparent",
                padding: "6px 0 0",
                textAlign: "left",
                cursor: "pointer",
                display: "grid",
                gridTemplateColumns: "minmax(0, 1fr) auto",
                gap: 8,
              }}
            >
              <span style={{ minWidth: 0 }}>
                <span
                  style={{
                    display: "block",
                    color: "var(--ink)",
                    fontSize: 11,
                    overflow: "hidden",
                    textOverflow: "ellipsis",
                    whiteSpace: "nowrap",
                  }}
                  title={row.title}
                >
                  {row.title}
                </span>
                <span
                  style={{
                    display: "block",
                    fontFamily: "var(--mono)",
                    color: "var(--ink3)",
                    fontSize: 9,
                    marginTop: 2,
                  }}
                >
                  {row.usage_count} uses · {formatCompactTokens(row.tokens_served)} served
                </span>
              </span>
              <span style={{ textAlign: "right", fontFamily: "var(--mono)" }}>
                <span style={{ display: "block", color: "var(--green)", fontSize: 11 }}>
                  {formatCompactTokens(row.proven_tokens_saved)}
                </span>
                <span style={{ display: "block", color: "var(--ink3)", fontSize: 9 }}>
                  {formatSavedDollars(row.realized_cost_saved_usd)}
                </span>
              </span>
            </button>
          ))
        ) : (
          <div
            style={{
              borderTop: "1px solid var(--border)",
              paddingTop: 7,
              fontSize: 11,
              color: "var(--ink3)",
              lineHeight: 1.4,
            }}
          >
            No context has been injected yet.
          </div>
        )}
      </div>
      <div
        style={{
          borderTop: "1px solid var(--border)",
          marginTop: 8,
          paddingTop: 6,
          color: "var(--ink3)",
          fontSize: 10,
          lineHeight: 1.35,
        }}
      >
        Per-context dollars are shown only when Dhee has direct attribution.
      </div>
    </div>
  );
}

function SessionSavingsPanel({
  rows,
  totalTokens,
  totalCost,
  apiValue,
  totalSessions,
  budget,
}: {
  rows: RouterSessionRow[];
  totalTokens: number;
  totalCost: number;
  apiValue: number;
  totalSessions: number;
  budget?: RouterSessionsPage["budget"];
}) {
  return (
    <div
      style={{
        border: "1px solid var(--border)",
        background: "white",
        borderRadius: 4,
        padding: 8,
      }}
    >
      <div
        style={{
          display: "flex",
          alignItems: "baseline",
          justifyContent: "space-between",
          gap: 8,
        }}
      >
        <span
          style={{
            fontFamily: "var(--mono)",
            fontSize: 9,
            color: "var(--ink3)",
            letterSpacing: "0.04em",
            textTransform: "uppercase",
          }}
        >
          Budget-capped savings
        </span>
        <span style={{ fontSize: 14, fontWeight: 700, color: "var(--green)" }}>
          {formatSavedDollars(totalCost)}
        </span>
      </div>
      <div
        style={{
          fontFamily: "var(--mono)",
          fontSize: 10,
          color: "var(--ink3)",
          marginTop: 2,
        }}
      >
        {formatCompactTokens(totalTokens)} input tokens avoided across {totalSessions} sessions
      </div>
      <div
        style={{
          fontFamily: "var(--mono)",
          fontSize: 9,
          color: "var(--ink3)",
          marginTop: 2,
          lineHeight: 1.35,
        }}
      >
        API value {formatSavedDollars(apiValue)}
        {budget?.monthly_budget_usd
          ? `; monthly cap ${formatSavedDollars(budget.monthly_budget_usd)}`
          : ""}
      </div>
      <div style={{ display: "grid", gap: 5, marginTop: 8 }}>
        {rows.length ? (
          rows.map((row) => {
            const tone = runtimeTone(row);
            return (
              <div
                key={row.session_id}
                style={{
                  display: "grid",
                  gridTemplateColumns: "minmax(0, 1fr) auto",
                  gap: 8,
                  borderTop: "1px solid var(--border)",
                  paddingTop: 6,
                }}
              >
                <div style={{ minWidth: 0 }}>
                  <div
                    style={{
                      display: "flex",
                      alignItems: "center",
                      gap: 6,
                      minWidth: 0,
                    }}
                  >
                    <span
                      style={{
                        width: 6,
                        height: 6,
                        borderRadius: 99,
                        background: tone,
                        flexShrink: 0,
                      }}
                    />
                    <span
                      style={{
                        fontSize: 11,
                        color: "var(--ink)",
                        overflow: "hidden",
                        textOverflow: "ellipsis",
                        whiteSpace: "nowrap",
                      }}
                    >
                      {sessionTitle(row)}
                    </span>
                  </div>
                  <div
                    style={{
                      fontFamily: "var(--mono)",
                      fontSize: 9,
                      color: tone,
                      marginTop: 2,
                    }}
                  >
                    {row.runtime || row.agent || "agent"} · {row.router_calls} calls
                  </div>
                </div>
                <div style={{ textAlign: "right" }} title={pricingTitle(row)}>
                  <div style={{ fontSize: 12, fontWeight: 700, color: "var(--ink)" }}>
                    {formatSavedDollars(row.estimated_cost_saved_usd)}
                  </div>
                  <div
                    style={{
                      fontFamily: "var(--mono)",
                      fontSize: 9,
                      color: "var(--ink3)",
                    }}
                  >
                    {formatCompactTokens(row.tokens_saved)} tok
                  </div>
                </div>
              </div>
            );
          })
        ) : (
          <div
            style={{
              borderTop: "1px solid var(--border)",
              paddingTop: 7,
              fontSize: 11,
              color: "var(--ink3)",
              lineHeight: 1.4,
            }}
          >
            No session-level savings recorded yet.
          </div>
        )}
      </div>
    </div>
  );
}

function TreeButton({
  treeId,
  buttonRef,
  focused,
  active,
  dot,
  title,
  meta,
  onFocus,
  onKeyDown,
  onClick,
}: {
  treeId: string;
  buttonRef: (el: HTMLButtonElement | null) => void;
  focused: boolean;
  active: boolean;
  dot: string;
  title: string;
  meta: string;
  onFocus: () => void;
  onKeyDown: (event: KeyboardEvent<HTMLButtonElement>) => void;
  onClick: () => void;
}) {
  return (
    <button
      ref={buttonRef}
      type="button"
      data-tree-id={treeId}
      role="treeitem"
      aria-selected={active}
      tabIndex={focused ? 0 : -1}
      onFocus={onFocus}
      onKeyDown={onKeyDown}
      onClick={onClick}
      style={{
        textAlign: "left",
        border: `1px solid ${active ? "var(--accent)" : "transparent"}`,
        background: active ? "var(--accent-dim)" : "transparent",
        color: "var(--ink)",
        padding: "7px 8px",
        borderRadius: 4,
        cursor: "pointer",
        display: "grid",
        gridTemplateColumns: "8px minmax(0, 1fr)",
        gap: 8,
        alignItems: "start",
        outline: focused ? "2px solid var(--accent-dim)" : "none",
      }}
    >
      <span
        style={{
          width: 6,
          height: 6,
          borderRadius: 99,
          background: dot,
          marginTop: 5,
        }}
      />
      <span style={{ minWidth: 0 }}>
        <span
          style={{
            display: "block",
            overflow: "hidden",
            textOverflow: "ellipsis",
            whiteSpace: "nowrap",
            fontSize: 12,
          }}
        >
          {title}
        </span>
        <span
          style={{
            display: "block",
            fontFamily: "var(--mono)",
            fontSize: 9,
            color: "var(--ink3)",
            marginTop: 2,
          }}
        >
          {meta}
        </span>
      </span>
    </button>
  );
}

function ActionButton({
  label,
  onClick,
  tone = "default",
  busy,
}: {
  label: string;
  onClick: () => void;
  tone?: "default" | "accent" | "indigo" | "green" | "rose";
  busy?: boolean;
}) {
  const color =
    tone === "accent"
      ? "var(--accent)"
      : tone === "indigo"
        ? "var(--indigo)"
        : tone === "green"
          ? "var(--green)"
          : tone === "rose"
            ? "var(--rose)"
            : "var(--ink2)";
  return (
    <button
      onClick={onClick}
      disabled={busy}
      style={{
        padding: "7px 10px",
        border: `1px solid ${color}`,
        background: tone === "default" ? "white" : "var(--surface)",
        color,
        fontFamily: "var(--mono)",
        fontSize: 10,
        letterSpacing: "0.06em",
        borderRadius: 3,
        cursor: busy ? "wait" : "pointer",
      }}
    >
      {busy ? "..." : label}
    </button>
  );
}

function MetaBlock({ label, children }: { label: string; children: ReactNode }) {
  return (
    <section style={{ display: "grid", gap: 8 }}>
      <div
        style={{
          fontFamily: "var(--mono)",
          fontSize: 9,
          letterSpacing: "0.08em",
          color: "var(--ink3)",
          textTransform: "uppercase",
        }}
      >
        {label}
      </div>
      <div style={{ display: "flex", flexWrap: "wrap", gap: 6 }}>{children}</div>
    </section>
  );
}

function Pill({
  children,
  color = "var(--ink2)",
}: {
  children: ReactNode;
  color?: string;
}) {
  return (
    <span
      style={{
        display: "inline-flex",
        border: "1px solid var(--border)",
        background: "var(--surface)",
        color,
        padding: "3px 7px",
        borderRadius: 3,
        fontFamily: "var(--mono)",
        fontSize: 10,
      }}
    >
      {children}
    </span>
  );
}

function KV({ k, v }: { k: string; v: unknown }) {
  if (v === undefined || v === null || v === "") return null;
  return (
    <div
      style={{
        display: "grid",
        gridTemplateColumns: "76px minmax(0, 1fr)",
        gap: 6,
        width: "100%",
        fontSize: 12,
      }}
    >
      <span style={{ fontFamily: "var(--mono)", fontSize: 10, color: "var(--ink3)" }}>
        {k}
      </span>
      <span style={{ color: "var(--ink2)", wordBreak: "break-word" }}>
        {String(v)}
      </span>
    </div>
  );
}
