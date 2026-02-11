import { create } from "zustand";

export type ClusterDimension =
  | "category"
  | "memory_type"
  | "layer"
  | "scene"
  | "echo_depth"
  | "strength"
  | "time"
  | "profile";

interface ClusterState {
  dimension: ClusterDimension;
  expandedCluster: string | null;
  transitioning: boolean;
  setDimension: (d: ClusterDimension) => void;
  expandCluster: (id: string | null) => void;
  setTransitioning: (t: boolean) => void;
}

export const useClusterStore = create<ClusterState>((set) => ({
  dimension: "category",
  expandedCluster: null,
  transitioning: false,
  setDimension: (dimension) => {
    set({ transitioning: true, expandedCluster: null });
    setTimeout(() => set({ dimension, transitioning: false }), 800);
  },
  expandCluster: (expandedCluster) => set({ expandedCluster }),
  setTransitioning: (transitioning) => set({ transitioning }),
}));
