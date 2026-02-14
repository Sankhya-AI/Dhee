import { create } from "zustand";
import type { KanbanFilters, KanbanSort } from "@/types";

interface UiPreferencesState {
  filters: KanbanFilters;
  sort: KanbanSort;
  sidebarOpen: boolean;
  setFilters: (filters: Partial<KanbanFilters>) => void;
  resetFilters: () => void;
  setSort: (sort: KanbanSort) => void;
  toggleSidebar: () => void;
}

const DEFAULT_FILTERS: KanbanFilters = {
  search: "",
  priorities: [],
  assignees: [],
  tagIds: [],
  hideCompleted: false,
};

export const useUiPreferencesStore = create<UiPreferencesState>((set) => ({
  filters: DEFAULT_FILTERS,
  sort: { field: "sort_order", direction: "asc" },
  sidebarOpen: true,
  setFilters: (partial) =>
    set((s) => ({ filters: { ...s.filters, ...partial } })),
  resetFilters: () => set({ filters: DEFAULT_FILTERS }),
  setSort: (sort) => set({ sort }),
  toggleSidebar: () => set((s) => ({ sidebarOpen: !s.sidebarOpen })),
}));
