export interface ConstellationNode {
  id: string;
  label: string;
  layer: "sml" | "lml";
  strength: number;
  category?: string;
  x?: number;
  y?: number;
}

export interface ConstellationEdge {
  source: string;
  target: string;
  type: "scene" | "category" | "entity";
  weight?: number;
}

export interface ConstellationData {
  nodes: ConstellationNode[];
  edges: ConstellationEdge[];
}
