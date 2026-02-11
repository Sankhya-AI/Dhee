export interface Conflict {
  id: string;
  stash_id: string;
  existing_memory_id: string;
  existing_content: string;
  proposed_content: string;
  similarity: number;
  resolution?: "KEEP_EXISTING" | "ACCEPT_PROPOSED" | "KEEP_BOTH";
  user_id?: string;
  created_at: string;
  resolved_at?: string;
}
