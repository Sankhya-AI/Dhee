export interface Stats {
  total_memories: number;
  sml_count: number;
  lml_count: number;
  categories: Record<string, number>;
  storage_mb?: number;
}
