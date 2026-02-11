import { create } from "zustand";

interface InspectorState {
  selectedMemoryId: string | null;
  isOpen: boolean;
  open: (memoryId: string) => void;
  close: () => void;
  toggle: (memoryId: string) => void;
}

export const useInspectorStore = create<InspectorState>((set, get) => ({
  selectedMemoryId: null,
  isOpen: false,
  open: (memoryId) => set({ selectedMemoryId: memoryId, isOpen: true }),
  close: () => set({ isOpen: false, selectedMemoryId: null }),
  toggle: (memoryId) => {
    const state = get();
    if (state.isOpen && state.selectedMemoryId === memoryId) {
      set({ isOpen: false, selectedMemoryId: null });
    } else {
      set({ selectedMemoryId: memoryId, isOpen: true });
    }
  },
}));
