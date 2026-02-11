export interface Memory {
  id: string;
  content: string;
  layer: "sml" | "lml";
  strength: number;
  user_id: string;
  agent_id?: string;
  categories: string[];
  access_count: number;
  created_at: string;
  updated_at: string;
  last_accessed?: string;
  memory_type?: "episodic" | "semantic";
  scene_id?: string;
  s_fast?: number;
  s_mid?: number;
  s_slow?: number;
  metadata?: MemoryMetadata;
}

export interface MemoryMetadata {
  echo_depth?: string;
  echo_paraphrases?: string[];
  echo_keywords?: string[];
  echo_implications?: string[];
  echo_questions?: string[];
  echo_importance?: number;
  source_type?: string;
  source_app?: string;
  related_memories?: string[];
  [key: string]: unknown;
}

export interface MemoryHistoryEntry {
  event: string;
  timestamp: string;
  details?: Record<string, unknown>;
}

export interface MemoryListResponse {
  memories: Memory[];
  count: number;
}

export interface MemoryUpdatePayload {
  content?: string;
  categories?: string[];
  metadata?: Record<string, unknown>;
}
