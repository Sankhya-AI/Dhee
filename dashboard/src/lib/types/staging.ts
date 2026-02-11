export interface StagingCommit {
  id: string;
  agent_id: string;
  content: string;
  status: "PENDING" | "APPROVED" | "REJECTED";
  checks_summary?: Record<string, unknown>;
  user_id?: string;
  created_at: string;
  resolved_at?: string;
  rejection_reason?: string;
}
