import { useState, useEffect, useCallback, useMemo } from "react";
import {
  Search,
  Brain,
  Database,
  Zap,
  Archive,
  Activity,
  Layers,
  Sparkles,
  ChevronRight,
  ChevronDown,
  FolderTree,
} from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import { api } from "@/hooks/use-api";
import type { MemoryItem, MemoryStats, MemoryCategory } from "@/types";

type RightTab = "detail" | "stats";

function StrengthBar({ value, max = 1 }: { value: number; max?: number }) {
  const pct = Math.min((value / max) * 100, 100);
  return (
    <div className="w-full h-1.5 bg-muted rounded-full overflow-hidden">
      <div
        className="h-full rounded-full bg-foreground/60 transition-all duration-500"
        style={{ width: `${pct}%` }}
      />
    </div>
  );
}

function MemoryCard({
  item,
  selected,
  onClick,
}: {
  item: MemoryItem;
  selected: boolean;
  onClick: () => void;
}) {
  return (
    <button
      onClick={onClick}
      className={`w-full text-left p-3 border-b border-border/40 transition-colors ${
        selected ? "bg-accent" : "hover:bg-muted/50"
      }`}
    >
      <p className="text-sm leading-snug line-clamp-2">{item.memory}</p>
      <div className="flex items-center gap-2 mt-2">
        <Badge
          variant="outline"
          className={`text-[10px] px-1.5 py-0 ${
            item.layer === "lml"
              ? "bg-emerald-50 text-emerald-700 border-emerald-200"
              : "bg-amber-50 text-amber-700 border-amber-200"
          }`}
        >
          {item.layer.toUpperCase()}
        </Badge>
        {item.echo_depth && item.echo_depth !== "none" && (
          <Badge variant="outline" className="text-[10px] px-1.5 py-0">
            {item.echo_depth}
          </Badge>
        )}
        <div className="flex-1">
          <StrengthBar value={item.strength} />
        </div>
        <span className="text-[10px] text-muted-foreground tabular-nums">
          {item.strength.toFixed(2)}
        </span>
      </div>
      {item.categories.length > 0 && (
        <div className="flex flex-wrap gap-1 mt-1.5">
          {item.categories.slice(0, 3).map((c) => (
            <span
              key={c}
              className="text-[9px] px-1.5 py-0.5 rounded-full bg-muted text-muted-foreground"
            >
              {c}
            </span>
          ))}
        </div>
      )}
    </button>
  );
}

function CategoryTreeNode({
  cat,
  selectedId,
  onSelect,
  depth = 0,
}: {
  cat: MemoryCategory;
  selectedId: string | null;
  onSelect: (id: string | null) => void;
  depth?: number;
}) {
  const [expanded, setExpanded] = useState(depth < 2);
  const hasChildren = (cat.children?.length ?? 0) > 0;

  return (
    <div>
      <button
        onClick={() => onSelect(selectedId === cat.id ? null : cat.id)}
        className={`w-full flex items-center gap-1.5 px-2 py-1.5 text-xs transition-colors rounded-md ${
          selectedId === cat.id
            ? "bg-accent text-accent-foreground"
            : "text-muted-foreground hover:text-foreground hover:bg-muted/50"
        }`}
        style={{ paddingLeft: `${depth * 12 + 8}px` }}
      >
        {hasChildren && (
          <button
            onClick={(e) => {
              e.stopPropagation();
              setExpanded(!expanded);
            }}
            className="p-0"
          >
            {expanded ? (
              <ChevronDown className="h-3 w-3" />
            ) : (
              <ChevronRight className="h-3 w-3" />
            )}
          </button>
        )}
        <span className="truncate flex-1 text-left">{cat.name}</span>
        <span className="text-[10px] text-muted-foreground tabular-nums">
          {cat.memory_count}
        </span>
      </button>
      {expanded &&
        cat.children?.map((child) => (
          <CategoryTreeNode
            key={child.id}
            cat={child}
            selectedId={selectedId}
            onSelect={onSelect}
            depth={depth + 1}
          />
        ))}
    </div>
  );
}

function StatsPanel({ stats }: { stats: MemoryStats | null }) {
  if (!stats) {
    return (
      <div className="flex flex-col items-center justify-center h-full text-muted-foreground py-12">
        <Brain className="h-8 w-8 mb-2 opacity-30" />
        <p className="text-sm">No memory data</p>
      </div>
    );
  }

  return (
    <div className="p-4 space-y-4 overflow-y-auto h-full">
      <h3 className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">
        Memory Statistics
      </h3>

      <div className="space-y-2">
        <div className="flex items-center justify-between py-1.5">
          <span className="flex items-center gap-2 text-sm text-muted-foreground">
            <Database className="h-3.5 w-3.5" />
            Total
          </span>
          <span className="text-sm font-semibold tabular-nums">
            {stats.total}
          </span>
        </div>
        <div className="flex items-center justify-between py-1.5">
          <span className="flex items-center gap-2 text-sm text-muted-foreground">
            <Zap className="h-3.5 w-3.5 text-amber-500" />
            Short-term (SML)
          </span>
          <span className="text-sm font-medium tabular-nums">
            {stats.sml_count}
          </span>
        </div>
        <div className="flex items-center justify-between py-1.5">
          <span className="flex items-center gap-2 text-sm text-muted-foreground">
            <Archive className="h-3.5 w-3.5 text-emerald-500" />
            Long-term (LML)
          </span>
          <span className="text-sm font-medium tabular-nums">
            {stats.lml_count}
          </span>
        </div>
      </div>

      <div className="border-t border-border pt-3">
        <div className="flex items-center justify-between mb-1.5">
          <span className="flex items-center gap-2 text-sm text-muted-foreground">
            <Activity className="h-3.5 w-3.5" />
            Avg Strength
          </span>
          <span className="text-sm font-medium tabular-nums">
            {stats.avg_strength.toFixed(3)}
          </span>
        </div>
        <StrengthBar value={stats.avg_strength} />
      </div>

      {/* Layer distribution */}
      {stats.total > 0 && (
        <div className="border-t border-border pt-3">
          <h4 className="flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wider text-muted-foreground mb-2">
            <Layers className="h-3 w-3" />
            Layer Distribution
          </h4>
          <div className="flex h-3 rounded-full overflow-hidden bg-muted">
            {stats.sml_count > 0 && (
              <div
                className="h-full bg-amber-400 transition-all"
                style={{
                  width: `${(stats.sml_count / stats.total) * 100}%`,
                }}
                title={`SML: ${stats.sml_count}`}
              />
            )}
            {stats.lml_count > 0 && (
              <div
                className="h-full bg-emerald-500 transition-all"
                style={{
                  width: `${(stats.lml_count / stats.total) * 100}%`,
                }}
                title={`LML: ${stats.lml_count}`}
              />
            )}
          </div>
          <div className="flex justify-between text-[10px] text-muted-foreground mt-1">
            <span className="flex items-center gap-1">
              <span className="w-2 h-2 rounded-full bg-amber-400" />
              SML {Math.round((stats.sml_count / stats.total) * 100)}%
            </span>
            <span className="flex items-center gap-1">
              <span className="w-2 h-2 rounded-full bg-emerald-500" />
              LML {Math.round((stats.lml_count / stats.total) * 100)}%
            </span>
          </div>
        </div>
      )}

      {/* Echo stats */}
      {stats.echo_enabled && (
        <div className="border-t border-border pt-3">
          <h4 className="flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wider text-muted-foreground mb-2">
            <Sparkles className="h-3 w-3" />
            Echo Encoding
          </h4>
          <div className="grid grid-cols-2 gap-2">
            {(["deep", "medium", "shallow", "none"] as const).map(
              (depth) => (
                <div
                  key={depth}
                  className="text-center p-2 rounded-lg bg-muted/50 border border-border/50"
                >
                  <div className="text-lg font-semibold tabular-nums">
                    {stats.echo_stats[depth]}
                  </div>
                  <div className="text-[10px] uppercase tracking-wider text-muted-foreground">
                    {depth}
                  </div>
                </div>
              ),
            )}
          </div>
        </div>
      )}
    </div>
  );
}

export function MemoryView() {
  const [memories, setMemories] = useState<MemoryItem[]>([]);
  const [stats, setStats] = useState<MemoryStats | null>(null);
  const [categories, setCategories] = useState<MemoryCategory[]>([]);
  const [selectedMemory, setSelectedMemory] = useState<MemoryItem | null>(
    null,
  );
  const [selectedCategory, setSelectedCategory] = useState<string | null>(
    null,
  );
  const [searchQuery, setSearchQuery] = useState("");
  const [rightTab, setRightTab] = useState<RightTab>("detail");
  const [loading, setLoading] = useState(false);

  // Load initial data
  useEffect(() => {
    api.memoryStats().then(setStats).catch(() => {});
    api.memoryCategories().then(setCategories).catch(() => {});
    setLoading(true);
    api
      .memoryAll({ limit: 50 })
      .then(setMemories)
      .catch(() => {})
      .finally(() => setLoading(false));
  }, []);

  // Search
  useEffect(() => {
    if (!searchQuery.trim()) {
      api
        .memoryAll({ limit: 50, category: selectedCategory || undefined })
        .then(setMemories)
        .catch(() => {});
      return;
    }
    const timer = setTimeout(() => {
      setLoading(true);
      api
        .memorySearch(searchQuery, 20)
        .then(setMemories)
        .catch(() => {})
        .finally(() => setLoading(false));
    }, 300);
    return () => clearTimeout(timer);
  }, [searchQuery, selectedCategory]);

  // Category filter
  const handleCategorySelect = useCallback(
    (id: string | null) => {
      setSelectedCategory(id);
      if (!searchQuery.trim()) {
        setLoading(true);
        api
          .memoryAll({ limit: 50, category: id || undefined })
          .then(setMemories)
          .catch(() => {})
          .finally(() => setLoading(false));
      }
    },
    [searchQuery],
  );

  return (
    <div className="flex flex-1 overflow-hidden">
      {/* Left: Search + Category Tree */}
      <div className="w-56 border-r border-border flex flex-col bg-sidebar overflow-hidden">
        <div className="p-3 border-b border-border">
          <div className="relative">
            <Search className="absolute left-2.5 top-1/2 -translate-y-1/2 h-3.5 w-3.5 text-muted-foreground" />
            <Input
              placeholder="Search memories..."
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              className="pl-8 h-8 text-sm"
            />
          </div>
        </div>
        <div className="flex-1 overflow-y-auto p-2">
          <div className="mb-2">
            <span className="text-[11px] font-medium text-muted-foreground uppercase tracking-wider px-2">
              Categories
            </span>
          </div>
          {categories.length === 0 ? (
            <div className="px-2 text-xs text-muted-foreground">
              No categories
            </div>
          ) : (
            <>
              <button
                onClick={() => handleCategorySelect(null)}
                className={`w-full flex items-center gap-1.5 px-2 py-1.5 text-xs rounded-md mb-0.5 ${
                  !selectedCategory
                    ? "bg-accent text-accent-foreground"
                    : "text-muted-foreground hover:bg-muted/50"
                }`}
              >
                <FolderTree className="h-3 w-3" />
                <span className="flex-1 text-left">All</span>
              </button>
              {categories.map((cat) => (
                <CategoryTreeNode
                  key={cat.id}
                  cat={cat}
                  selectedId={selectedCategory}
                  onSelect={handleCategorySelect}
                />
              ))}
            </>
          )}
        </div>
      </div>

      {/* Center: Memory List */}
      <div className="flex-1 flex flex-col overflow-hidden">
        <div className="flex items-center justify-between px-4 py-2.5 border-b border-border">
          <h3 className="text-sm font-semibold">
            Memories
            {memories.length > 0 && (
              <span className="text-muted-foreground font-normal ml-2">
                ({memories.length})
              </span>
            )}
          </h3>
        </div>
        <div className="flex-1 overflow-y-auto">
          {loading ? (
            <div className="flex items-center justify-center h-32">
              <div className="text-sm text-muted-foreground">Loading...</div>
            </div>
          ) : memories.length === 0 ? (
            <div className="flex flex-col items-center justify-center h-full text-muted-foreground py-12">
              <Brain className="h-8 w-8 mb-2 opacity-30" />
              <p className="text-sm">No memories found</p>
              <p className="text-xs mt-1">
                {searchQuery
                  ? "Try a different search"
                  : "Memories will appear as agents work"}
              </p>
            </div>
          ) : (
            memories.map((item) => (
              <MemoryCard
                key={item.id}
                item={item}
                selected={selectedMemory?.id === item.id}
                onClick={() => setSelectedMemory(item)}
              />
            ))
          )}
        </div>
      </div>

      {/* Right: Detail / Stats */}
      <div className="w-80 border-l border-border flex flex-col overflow-hidden">
        <div className="flex border-b border-border">
          <button
            onClick={() => setRightTab("detail")}
            className={`flex-1 px-3 py-2 text-xs font-medium border-b-2 transition-colors ${
              rightTab === "detail"
                ? "border-primary text-foreground"
                : "border-transparent text-muted-foreground hover:text-foreground"
            }`}
          >
            Detail
          </button>
          <button
            onClick={() => setRightTab("stats")}
            className={`flex-1 px-3 py-2 text-xs font-medium border-b-2 transition-colors ${
              rightTab === "stats"
                ? "border-primary text-foreground"
                : "border-transparent text-muted-foreground hover:text-foreground"
            }`}
          >
            Stats
          </button>
        </div>

        <div className="flex-1 overflow-hidden">
          {rightTab === "stats" ? (
            <StatsPanel stats={stats} />
          ) : selectedMemory ? (
            <div className="p-4 overflow-y-auto h-full space-y-4">
              <div>
                <h4 className="text-xs font-semibold uppercase tracking-wider text-muted-foreground mb-2">
                  Content
                </h4>
                <p className="text-sm leading-relaxed">
                  {selectedMemory.memory}
                </p>
              </div>

              <div className="border-t border-border pt-3">
                <h4 className="text-xs font-semibold uppercase tracking-wider text-muted-foreground mb-2">
                  Properties
                </h4>
                <div className="space-y-1.5 text-sm">
                  <div className="flex justify-between">
                    <span className="text-muted-foreground">Layer</span>
                    <Badge
                      variant="outline"
                      className={`text-[10px] ${
                        selectedMemory.layer === "lml"
                          ? "bg-emerald-50 text-emerald-700 border-emerald-200"
                          : "bg-amber-50 text-amber-700 border-amber-200"
                      }`}
                    >
                      {selectedMemory.layer.toUpperCase()}
                    </Badge>
                  </div>
                  <div className="flex justify-between">
                    <span className="text-muted-foreground">Strength</span>
                    <span className="font-medium tabular-nums">
                      {selectedMemory.strength.toFixed(3)}
                    </span>
                  </div>
                  <StrengthBar value={selectedMemory.strength} />
                  {selectedMemory.access_count !== undefined && (
                    <div className="flex justify-between">
                      <span className="text-muted-foreground">Accessed</span>
                      <span className="tabular-nums">
                        {selectedMemory.access_count}x
                      </span>
                    </div>
                  )}
                  {selectedMemory.echo_depth &&
                    selectedMemory.echo_depth !== "none" && (
                      <div className="flex justify-between">
                        <span className="text-muted-foreground">
                          Echo Depth
                        </span>
                        <span className="capitalize">
                          {selectedMemory.echo_depth}
                        </span>
                      </div>
                    )}
                </div>
              </div>

              {/* Echo encodings */}
              {selectedMemory.echo_encodings && (
                <div className="border-t border-border pt-3">
                  <h4 className="flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wider text-muted-foreground mb-2">
                    <Sparkles className="h-3 w-3" />
                    Echo Encodings
                  </h4>
                  <div className="space-y-2 text-sm">
                    {selectedMemory.echo_encodings.paraphrase && (
                      <div>
                        <span className="text-[10px] text-muted-foreground uppercase">
                          Paraphrase
                        </span>
                        <p className="text-xs mt-0.5">
                          {selectedMemory.echo_encodings.paraphrase}
                        </p>
                      </div>
                    )}
                    {selectedMemory.echo_encodings.keywords &&
                      selectedMemory.echo_encodings.keywords.length > 0 && (
                        <div>
                          <span className="text-[10px] text-muted-foreground uppercase">
                            Keywords
                          </span>
                          <div className="flex flex-wrap gap-1 mt-0.5">
                            {selectedMemory.echo_encodings.keywords.map(
                              (kw) => (
                                <span
                                  key={kw}
                                  className="text-[10px] px-1.5 py-0.5 rounded-full bg-muted"
                                >
                                  {kw}
                                </span>
                              ),
                            )}
                          </div>
                        </div>
                      )}
                    {selectedMemory.echo_encodings.question_form && (
                      <div>
                        <span className="text-[10px] text-muted-foreground uppercase">
                          Question Form
                        </span>
                        <p className="text-xs mt-0.5 italic">
                          {selectedMemory.echo_encodings.question_form}
                        </p>
                      </div>
                    )}
                    {selectedMemory.echo_encodings.implications &&
                      selectedMemory.echo_encodings.implications.length >
                        0 && (
                        <div>
                          <span className="text-[10px] text-muted-foreground uppercase">
                            Implications
                          </span>
                          <ul className="text-xs mt-0.5 space-y-0.5">
                            {selectedMemory.echo_encodings.implications.map(
                              (imp, i) => (
                                <li key={i} className="text-muted-foreground">
                                  {imp}
                                </li>
                              ),
                            )}
                          </ul>
                        </div>
                      )}
                  </div>
                </div>
              )}

              {/* Categories */}
              {selectedMemory.categories.length > 0 && (
                <div className="border-t border-border pt-3">
                  <h4 className="text-xs font-semibold uppercase tracking-wider text-muted-foreground mb-2">
                    Categories
                  </h4>
                  <div className="flex flex-wrap gap-1">
                    {selectedMemory.categories.map((c) => (
                      <span
                        key={c}
                        className="text-[10px] px-2 py-0.5 rounded-full bg-muted text-muted-foreground"
                      >
                        {c}
                      </span>
                    ))}
                  </div>
                </div>
              )}
            </div>
          ) : (
            <div className="flex flex-col items-center justify-center h-full text-muted-foreground py-12">
              <Brain className="h-8 w-8 mb-2 opacity-30" />
              <p className="text-sm">Select a memory</p>
              <p className="text-xs mt-1">Click a memory to view details</p>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
