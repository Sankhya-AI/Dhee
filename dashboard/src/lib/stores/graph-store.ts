import { create } from "zustand";

interface GraphState {
  zoom: number;
  offsetX: number;
  offsetY: number;
  selectedNodeId: string | null;
  showSml: boolean;
  showLml: boolean;
  setZoom: (zoom: number) => void;
  setOffset: (x: number, y: number) => void;
  selectNode: (id: string | null) => void;
  toggleSml: () => void;
  toggleLml: () => void;
  resetView: () => void;
}

export const useGraphStore = create<GraphState>((set) => ({
  zoom: 1,
  offsetX: 0,
  offsetY: 0,
  selectedNodeId: null,
  showSml: true,
  showLml: true,
  setZoom: (zoom) => set({ zoom }),
  setOffset: (offsetX, offsetY) => set({ offsetX, offsetY }),
  selectNode: (selectedNodeId) => set({ selectedNodeId }),
  toggleSml: () => set((s) => ({ showSml: !s.showSml })),
  toggleLml: () => set((s) => ({ showLml: !s.showLml })),
  resetView: () => set({ zoom: 1, offsetX: 0, offsetY: 0, selectedNodeId: null }),
}));
