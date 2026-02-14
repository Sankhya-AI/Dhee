// ── Core types for Vibe Kanban + Engram ──

export type Priority = "urgent" | "high" | "medium" | "low";

export interface ProjectStatus {
  id: string;
  project_id: string;
  name: string;
  color: string;
  sort_order: number;
  hidden: boolean;
  created_at: string;
}

export interface ProjectTag {
  id: string;
  project_id: string;
  name: string;
  color: string;
  created_at: string;
}

export interface Project {
  id: string;
  name: string;
  color: string;
  description: string;
  identifier: string;
  issue_counter: number;
  created_at: string;
}

export interface Reaction {
  user_id: string;
  emoji: string;
}

export interface Comment {
  id: string;
  agent: string;
  text: string;
  timestamp: string;
  reactions: Reaction[];
}

export interface Relationship {
  related_task_id: string;
  type: "blocking" | "related" | "duplicate";
  created_at: string;
}

export interface ConversationEntry {
  id: string;
  type: "user" | "assistant" | "tool_use" | "tool_result" | "error" | "system";
  content: string;
  ts: string;
  agent?: string;
  tool?: string;
  file_path?: string;
  streaming?: boolean;
}

export interface ProcessEntry {
  id: string;
  name: string;
  status: "running" | "completed" | "failed";
  started_at: string;
  completed_at?: string;
  duration_ms?: number;
  output?: string;
  exit_code?: number;
  agent?: string;
}

export interface FileChange {
  path: string;
  action: "created" | "modified" | "deleted";
  additions?: number;
  deletions?: number;
  diff?: string;
  ts: string;
}

export interface Issue {
  id: string;
  title: string;
  description: string;
  priority: Priority;
  status: string;
  assigned_agent: string | null;
  tags: string[];
  due_date: string | null;
  created_at: string;
  updated_at: string;
  comments: Comment[];
  conversation: ConversationEntry[];
  processes: ProcessEntry[];
  files_changed: FileChange[];
  memory_strength: number;
  categories: string[];
  custom: Record<string, unknown>;
  // Kanban fields
  project_id: string;
  status_id: string | null;
  assignee_ids: string[];
  tag_ids: string[];
  start_date: string | null;
  target_date: string | null;
  parent_task_id: string | null;
  sort_order: number;
  relationships: Relationship[];
  issue_number: number;
  completed_at: string | null;
}

export interface FeedEvent {
  id: string;
  event: string;
  ts: string;
  task_id?: string;
  title?: string;
  agent?: string;
  from?: string;
  to?: string;
  text?: string;
}

export interface SystemInfo {
  version: string;
  has_memory: boolean;
  has_projects: boolean;
  connections: number;
}

// ── Memory types ──

export interface MemoryItem {
  id: string;
  memory: string;
  metadata: Record<string, unknown>;
  layer: "sml" | "lml";
  strength: number;
  categories: string[];
  echo_depth?: "shallow" | "medium" | "deep" | "none";
  echo_encodings?: {
    paraphrase?: string;
    keywords?: string[];
    implications?: string[];
    question_form?: string;
  };
  access_count?: number;
  created_at: string;
  updated_at: string;
}

export interface MemoryCategory {
  id: string;
  name: string;
  parent_id?: string;
  memory_count: number;
  children?: MemoryCategory[];
}

export interface MemoryStats {
  total: number;
  sml_count: number;
  lml_count: number;
  avg_strength: number;
  echo_stats: {
    shallow: number;
    medium: number;
    deep: number;
    none: number;
  };
  echo_enabled: boolean;
}

// ── Coordination types ──

export interface CoordinationAgent {
  id: string;
  name: string;
  type: string;
  model: string;
  description: string;
  capabilities: string[];
  max_concurrent: number;
  status: "available" | "busy" | "offline";
  active_tasks: string[];
  registered_at: string;
  last_seen: string;
  similarity?: number;
}

export interface CoordinationEvent {
  id: string;
  type: string;
  timestamp: string;
  details: Record<string, unknown>;
  content: string;
}

export interface RouteResult {
  routed: number;
  tasks: Issue[];
}

// ── Filter/sort types ──

export interface KanbanFilters {
  search: string;
  priorities: Priority[];
  assignees: string[];
  tagIds: string[];
  hideCompleted: boolean;
}

export type SortField = "sort_order" | "priority" | "created_at" | "updated_at" | "title";
export type SortDirection = "asc" | "desc";

export interface KanbanSort {
  field: SortField;
  direction: SortDirection;
}
