import { create } from "zustand";

interface FilterState {
  layer: "all" | "sml" | "lml";
  strengthRange: [number, number];
  categories: string[];
  searchQuery: string;
  setLayer: (layer: "all" | "sml" | "lml") => void;
  setStrengthRange: (range: [number, number]) => void;
  setCategories: (categories: string[]) => void;
  setSearchQuery: (query: string) => void;
  reset: () => void;
}

export const useFilterStore = create<FilterState>((set) => ({
  layer: "all",
  strengthRange: [0, 1],
  categories: [],
  searchQuery: "",
  setLayer: (layer) => set({ layer }),
  setStrengthRange: (strengthRange) => set({ strengthRange }),
  setCategories: (categories) => set({ categories }),
  setSearchQuery: (searchQuery) => set({ searchQuery }),
  reset: () =>
    set({ layer: "all", strengthRange: [0, 1], categories: [], searchQuery: "" }),
}));
