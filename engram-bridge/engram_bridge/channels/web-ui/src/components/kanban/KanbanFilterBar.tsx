import { Search, Plus, SlidersHorizontal, ArrowUpDown } from "lucide-react";
import { useUiPreferencesStore } from "@/stores/useUiPreferencesStore";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import type { SortField } from "@/types";

interface Props {
  onCreateClick: () => void;
  totalCount: number;
}

const SORT_OPTIONS: { field: SortField; label: string }[] = [
  { field: "sort_order", label: "Manual" },
  { field: "priority", label: "Priority" },
  { field: "created_at", label: "Created" },
  { field: "updated_at", label: "Updated" },
  { field: "title", label: "Title" },
];

export function KanbanFilterBar({ onCreateClick, totalCount }: Props) {
  const { filters, setFilters, sort, setSort } = useUiPreferencesStore();

  return (
    <div className="flex items-center gap-3 px-4 py-2.5 border-b border-border/40">
      {/* Search */}
      <div className="relative flex-1 max-w-sm">
        <Search className="absolute left-2.5 top-1/2 -translate-y-1/2 h-3.5 w-3.5 text-muted-foreground" />
        <Input
          placeholder="Search issues..."
          value={filters.search}
          onChange={e => setFilters({ search: e.target.value })}
          className="pl-8 h-8 text-sm bg-muted/30 border-border/40"
        />
      </div>

      {/* Sort dropdown */}
      <div className="flex items-center gap-1">
        <ArrowUpDown className="h-3.5 w-3.5 text-muted-foreground" />
        <select
          value={sort.field}
          onChange={e => setSort({ ...sort, field: e.target.value as SortField })}
          className="text-xs bg-transparent border-none text-muted-foreground focus:outline-none cursor-pointer"
        >
          {SORT_OPTIONS.map(o => (
            <option key={o.field} value={o.field}>{o.label}</option>
          ))}
        </select>
      </div>

      {/* Count */}
      <span className="text-xs text-muted-foreground">
        {totalCount} issue{totalCount !== 1 ? "s" : ""}
      </span>

      {/* Create */}
      <Button
        size="sm"
        onClick={onCreateClick}
        className="h-8 gap-1.5 text-xs"
      >
        <Plus className="h-3.5 w-3.5" />
        New Issue
      </Button>
    </div>
  );
}
