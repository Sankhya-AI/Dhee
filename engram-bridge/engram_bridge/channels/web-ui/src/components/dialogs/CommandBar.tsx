import { useState, useCallback, useEffect, useRef, useMemo } from "react";
import { useNavigate } from "react-router-dom";
import { Search, ArrowRight, Brain, LayoutDashboard, Workflow, Database } from "lucide-react";
import { useHotkeys } from "react-hotkeys-hook";
import { useProjectContext } from "@/contexts/ProjectContext";
import { PriorityIcon } from "@/components/primitives/PriorityIcon";
import { api } from "@/hooks/use-api";
import type { Issue, Priority, MemoryItem } from "@/types";

interface Props {
  open: boolean;
  onClose: () => void;
  onSelectIssue: (issue: Issue) => void;
  onCreateIssue: () => void;
}

type ResultItem =
  | { type: "action"; label: string; sublabel?: string; action: () => void }
  | { type: "issue"; issue: Issue; label: string; sublabel?: string }
  | { type: "memory"; memory: MemoryItem; label: string; sublabel?: string };

export function CommandBar({ open, onClose, onSelectIssue, onCreateIssue }: Props) {
  const { issues, statuses } = useProjectContext();
  const navigate = useNavigate();
  const [query, setQuery] = useState("");
  const [selectedIndex, setSelectedIndex] = useState(0);
  const [memoryResults, setMemoryResults] = useState<MemoryItem[]>([]);
  const inputRef = useRef<HTMLInputElement>(null);
  const searchTimerRef = useRef<ReturnType<typeof setTimeout>>(undefined);

  useEffect(() => {
    if (open) {
      setQuery("");
      setSelectedIndex(0);
      setMemoryResults([]);
      setTimeout(() => inputRef.current?.focus(), 50);
    }
  }, [open]);

  // Debounced memory search
  useEffect(() => {
    if (!query.trim() || query.length < 2) {
      setMemoryResults([]);
      return;
    }
    if (searchTimerRef.current) clearTimeout(searchTimerRef.current);
    searchTimerRef.current = setTimeout(() => {
      api.memorySearch(query, 5).then(setMemoryResults).catch(() => setMemoryResults([]));
    }, 300);
    return () => {
      if (searchTimerRef.current) clearTimeout(searchTimerRef.current);
    };
  }, [query]);

  // Close on Escape
  useHotkeys("escape", () => onClose(), { enabled: open, enableOnFormTags: true });

  const results = useMemo(() => {
    const items: ResultItem[] = [];
    const q = query.toLowerCase();

    // Navigation actions
    if (!query || "board".includes(q)) {
      items.push({
        type: "action",
        label: "Go to Board",
        sublabel: "View kanban board",
        action: () => { navigate("/"); onClose(); },
      });
    }
    if (!query || "workspace".includes(q)) {
      items.push({
        type: "action",
        label: "Go to Workspace",
        sublabel: "View task executions",
        action: () => { navigate("/workspace"); onClose(); },
      });
    }
    if (!query || "memory".includes(q)) {
      items.push({
        type: "action",
        label: "Go to Memory",
        sublabel: "Browse memories",
        action: () => { navigate("/memory"); onClose(); },
      });
    }

    if (!query || "create new issue".includes(q)) {
      items.push({
        type: "action",
        label: "Create New Issue",
        sublabel: "Add a new issue to the board",
        action: () => { onCreateIssue(); onClose(); },
      });
    }

    // Issues
    for (const issue of issues) {
      if (
        !query ||
        issue.title.toLowerCase().includes(q) ||
        String(issue.issue_number).includes(q) ||
        issue.description.toLowerCase().includes(q)
      ) {
        const status = statuses.find(s => s.id === issue.status_id);
        items.push({
          type: "issue",
          issue,
          label: issue.title,
          sublabel: status ? status.name : undefined,
        });
      }
    }

    // Memory results (from debounced search)
    for (const mem of memoryResults) {
      items.push({
        type: "memory",
        memory: mem,
        label: mem.memory.slice(0, 80) + (mem.memory.length > 80 ? "..." : ""),
        sublabel: `${mem.layer.toUpperCase()} · ${mem.strength.toFixed(2)}`,
      });
    }

    return items.slice(0, 25);
  }, [query, issues, statuses, memoryResults, navigate, onCreateIssue, onClose]);

  const handleSelect = useCallback((index: number) => {
    const item = results[index];
    if (!item) return;
    if (item.type === "action") {
      item.action();
    } else if (item.type === "issue") {
      onSelectIssue(item.issue);
      onClose();
    } else if (item.type === "memory") {
      navigate("/memory");
      onClose();
    }
  }, [results, onSelectIssue, onClose, navigate]);

  const handleKeyDown = useCallback((e: React.KeyboardEvent) => {
    switch (e.key) {
      case "ArrowDown":
        e.preventDefault();
        setSelectedIndex(i => Math.min(i + 1, results.length - 1));
        break;
      case "ArrowUp":
        e.preventDefault();
        setSelectedIndex(i => Math.max(i - 1, 0));
        break;
      case "Enter":
        e.preventDefault();
        handleSelect(selectedIndex);
        break;
    }
  }, [results.length, selectedIndex, handleSelect]);

  if (!open) return null;

  // Group results by type for section headers
  const actionItems = results.filter(r => r.type === "action");
  const issueItems = results.filter(r => r.type === "issue");
  const memItems = results.filter(r => r.type === "memory");

  return (
    <div className="fixed inset-0 z-50 flex items-start justify-center pt-[20vh]">
      <div className="absolute inset-0 bg-black/50" onClick={onClose} />
      <div className="relative w-full max-w-lg bg-popover border border-border rounded-xl shadow-2xl overflow-hidden">
        {/* Input */}
        <div className="flex items-center gap-2 px-4 py-3 border-b border-border/50">
          <Search className="h-4 w-4 text-muted-foreground flex-shrink-0" />
          <input
            ref={inputRef}
            value={query}
            onChange={e => { setQuery(e.target.value); setSelectedIndex(0); }}
            onKeyDown={handleKeyDown}
            placeholder="Search issues, memories, or type a command..."
            className="flex-1 bg-transparent text-sm focus:outline-none placeholder:text-muted-foreground/50"
          />
          <kbd className="text-[10px] text-muted-foreground bg-muted px-1.5 py-0.5 rounded font-mono">ESC</kbd>
        </div>

        {/* Results */}
        <div className="max-h-[400px] overflow-y-auto py-1">
          {results.length === 0 && (
            <div className="px-4 py-8 text-center text-sm text-muted-foreground">
              No results found
            </div>
          )}

          {/* Actions section */}
          {actionItems.length > 0 && (
            <>
              <div className="px-4 pt-2 pb-1 text-[10px] font-medium text-muted-foreground uppercase tracking-wider">
                Actions
              </div>
              {actionItems.map((item, i) => {
                const globalIdx = results.indexOf(item);
                return (
                  <button
                    key={`action-${i}`}
                    onClick={() => handleSelect(globalIdx)}
                    className={`w-full flex items-center gap-3 px-4 py-2 text-sm transition-colors ${
                      globalIdx === selectedIndex ? "bg-muted/50" : "hover:bg-muted/30"
                    }`}
                  >
                    <ArrowRight className="h-3.5 w-3.5 text-muted-foreground flex-shrink-0" />
                    <span className="flex-1 text-left">{item.label}</span>
                    <span className="text-[10px] text-muted-foreground">{item.sublabel}</span>
                  </button>
                );
              })}
            </>
          )}

          {/* Issues section */}
          {issueItems.length > 0 && (
            <>
              <div className="px-4 pt-2 pb-1 text-[10px] font-medium text-muted-foreground uppercase tracking-wider">
                Issues
              </div>
              {issueItems.map((item, i) => {
                if (item.type !== "issue") return null;
                const globalIdx = results.indexOf(item);
                return (
                  <button
                    key={`issue-${i}`}
                    onClick={() => handleSelect(globalIdx)}
                    className={`w-full flex items-center gap-3 px-4 py-2 text-sm transition-colors ${
                      globalIdx === selectedIndex ? "bg-muted/50" : "hover:bg-muted/30"
                    }`}
                  >
                    <PriorityIcon priority={(item.issue.priority || "medium") as Priority} className="h-3.5 w-3.5 flex-shrink-0" />
                    <span className="flex-1 text-left truncate">{item.label}</span>
                    {item.issue.issue_number > 0 && (
                      <span className="text-[10px] font-mono text-muted-foreground">#{item.issue.issue_number}</span>
                    )}
                    {item.sublabel && (
                      <span className="text-[10px] text-muted-foreground">{item.sublabel}</span>
                    )}
                  </button>
                );
              })}
            </>
          )}

          {/* Memory section */}
          {memItems.length > 0 && (
            <>
              <div className="px-4 pt-2 pb-1 text-[10px] font-medium text-muted-foreground uppercase tracking-wider">
                Memories
              </div>
              {memItems.map((item, i) => {
                if (item.type !== "memory") return null;
                const globalIdx = results.indexOf(item);
                return (
                  <button
                    key={`mem-${i}`}
                    onClick={() => handleSelect(globalIdx)}
                    className={`w-full flex items-center gap-3 px-4 py-2 text-sm transition-colors ${
                      globalIdx === selectedIndex ? "bg-muted/50" : "hover:bg-muted/30"
                    }`}
                  >
                    <Brain className="h-3.5 w-3.5 text-muted-foreground flex-shrink-0" />
                    <span className="flex-1 text-left truncate">{item.label}</span>
                    <span className="text-[10px] text-muted-foreground">{item.sublabel}</span>
                  </button>
                );
              })}
            </>
          )}
        </div>
      </div>
    </div>
  );
}
