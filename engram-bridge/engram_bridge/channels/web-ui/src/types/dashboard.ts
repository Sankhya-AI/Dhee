// Re-export from new types for backward compatibility
export type { Issue as Task, Comment as TaskComment, ConversationEntry, ProcessEntry, FileChange, FeedEvent } from "./index";
export type { ProjectStatus } from "./index";
export type TaskStatus = "inbox" | "assigned" | "active" | "review" | "blocked" | "done";

export interface AgentInfo {
  name: string;
  type: "claude" | "codex" | "custom";
  model: string;
  status: "active" | "idle" | "offline";
  repo: string | null;
}

export const STATUS_COLUMNS: { key: TaskStatus; label: string }[] = [
  { key: "inbox", label: "Inbox" },
  { key: "assigned", label: "Assigned" },
  { key: "active", label: "Active" },
  { key: "review", label: "Review" },
  { key: "done", label: "Done" },
];

export const PRIORITY_COLORS: Record<string, string> = {
  urgent: "bg-red-500/20 text-red-400 border-red-500/30",
  high: "bg-orange-500/20 text-orange-400 border-orange-500/30",
  medium: "bg-blue-500/20 text-blue-400 border-blue-500/30",
  normal: "bg-blue-500/20 text-blue-400 border-blue-500/30",
  low: "bg-muted text-muted-foreground border-border",
};

export const AGENT_TYPE_ICONS: Record<string, string> = {
  claude: "C",
  codex: "X",
  custom: "?",
};

export const STATUS_COLORS: Record<string, string> = {
  active: "bg-emerald-500",
  idle: "bg-yellow-500",
  offline: "bg-zinc-500",
};
