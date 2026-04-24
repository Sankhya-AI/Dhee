export type Tier = "canonical" | "high" | "medium" | "short-term" | "avoid";

export interface Engram {
  id: string;
  tier: Tier;
  content: string;
  source: string;
  created: string;
  tags: string[];
  decay: number;
  reaffirmed: number;
  tokens: number;
}

export interface ToolStats {
  name: string;
  calls: number;
  tokensSaved: number;
  avgDigest: number;
  avgRaw: number;
  expansions: number;
}

export interface AgentOption {
  id: string;
  label: string;
  calls: number;
  tokensSaved: number;
  bytesStored: number;
  expansionRate: number;
  sessions: number;
}

export interface CodexNativeStats {
  available: boolean;
  threadId?: string;
  title?: string;
  model?: string | null;
  updatedAt?: string | null;
  totalTokens?: number | null;
  inputTokens?: number | null;
  cachedInputTokens?: number | null;
  outputTokens?: number | null;
  reasoningOutputTokens?: number | null;
  lastTurnTokens?: number | null;
  lastTurnInputTokens?: number | null;
  lastTurnCachedInputTokens?: number | null;
  lastTurnOutputTokens?: number | null;
  contextWindow?: number | null;
  primaryUsedPercent?: number | null;
  secondaryUsedPercent?: number | null;
  resetAt?: string | null;
  secondaryResetAt?: string | null;
  rateLimits?: Record<string, unknown>;
}

export interface RouterStats {
  live: boolean;
  selectedAgent: string;
  sessionTokensSaved: number;
  totalCalls: number;
  expansionRate: number;
  sessionCost: number;
  estimatedFullCost: number;
  tools: ToolStats[];
  agents: AgentOption[];
  dailySavings: number[];
  days: string[];
  sessions: number;
  bytesStored: number;
  codexNative?: CodexNativeStats;
  error?: string;
}

export interface StoredKeyVersion {
  id: string;
  label: string;
  createdAt?: string;
  retiredAt?: string;
  preview: string;
  active: boolean;
}

export interface ApiKeyProviderStatus {
  provider: string;
  label: string;
  envVars: string[];
  hasEnvKey: boolean;
  hasStoredKey: boolean;
  activeSource: "env" | "stored" | "none";
  activeEnvVar?: string | null;
  activePreview: string;
  storedVersions: StoredKeyVersion[];
  storedVersionsCount: number;
  updatedAt?: string;
  rotatedAt?: string;
  note?: string;
}

export interface PolicyRow {
  intent: string;
  label: string;
  depth: number;
  prevDepth: number;
  expansionRate: number;
  tuned: boolean;
  tool: string;
}

export interface ConfidenceGroup {
  group: string;
  confidence: number;
  trend: "up" | "down" | "stable";
}

export interface MetaBuddhiSnapshot {
  live: boolean;
  status: string;
  strategy: string;
  sessionInsights: number;
  totalInsights: number;
  pendingProposals: number;
  lastGate: string;
  confidenceGroups: ConfidenceGroup[];
  error?: string;
}

export interface EvolutionEvent {
  id: string;
  time: string;
  type: "tune" | "commit" | "rollback" | "nididhyasana" | "promotion";
  label: string;
  detail: string;
  impact: "positive" | "negative" | "neutral";
}

export interface Belief {
  id: string;
  content: string;
  confidence: number;
  created: string;
  source: string;
  tier: Tier;
  domain?: string;
  freshness?: string | null;
  lifecycle?: string | null;
  truthStatus?: string | null;
  sourceMemoryIds?: string[];
  evidence?: {
    id?: string;
    content: string;
    supports?: boolean;
    source?: string;
    confidence?: number;
    created_at?: string;
    memory_id?: string;
    episode_id?: string;
  }[];
  history?: {
    event_type?: string;
    reason?: string;
    actor?: string;
    created_at?: string;
    payload?: Record<string, unknown>;
  }[];
}

export interface Conflict {
  id: string;
  kind?: string;
  severity: "high" | "medium" | "low";
  reason: string;
  resolutionOptions?: string[];
  belief_a: Belief;
  belief_b: Belief;
}

export interface ConflictSnapshot {
  live: boolean;
  supported: boolean;
  resolutionMode: "native" | "read-only" | "unavailable";
  conflicts: Conflict[];
}

export type TaskColor = "green" | "indigo" | "orange" | "rose";

export type TaskHarness = "claude-code" | "codex" | "both" | null;

export type TaskStatus =
  | "active"
  | "paused"
  | "completed"
  | "closed"
  | "abandoned";

export interface TaskMessage {
  id: string;
  role: "user" | "agent" | "component";
  content?: string;
  type?: string;
  [k: string]: unknown;
}

export interface SankhyaTask {
  id: string;
  color: TaskColor;
  title: string;
  created: string;
  updatedAt?: string | null;
  status?: TaskStatus | string;
  links: string[];
  pos: { x: number; y: number };
  harness: TaskHarness;
  source?: string;
  messages: TaskMessage[];
}

export interface SharedTaskResult {
  id: string;
  packet_kind: string;
  tool_name: string;
  result_status: string;
  source_path?: string | null;
  ptr?: string | null;
  artifact_id?: string | null;
  digest?: string | null;
  metadata?: Record<string, unknown>;
  harness?: string | null;
  agent_id?: string | null;
  created_at?: string | null;
  updated_at?: string | null;
}

export interface SessionAsset {
  id: string;
  project_id?: string | null;
  workspace_id?: string | null;
  session_id: string;
  artifact_id?: string | null;
  storage_path: string;
  name: string;
  mime_type?: string | null;
  size_bytes?: number;
  metadata?: Record<string, unknown>;
  created_at?: string | null;
  updated_at?: string | null;
}

export interface ProjectAsset {
  id: string;
  workspace_id: string;
  project_id?: string | null;
  user_id?: string;
  artifact_id?: string | null;
  folder?: string | null;
  storage_path: string;
  name: string;
  mime_type?: string | null;
  size_bytes?: number;
  checksum?: string | null;
  metadata?: Record<string, unknown>;
  created_at?: string | null;
  updated_at?: string | null;
  results?: SharedTaskResult[];
}

export interface FileContextSummary {
  path: string;
  workspaceId?: string | null;
  results: SharedTaskResult[];
  memories: Engram[];
  summary: string;
}

export interface AssetContextSummary {
  asset: SessionAsset;
  session?: AgentSessionSummary | null;
  artifact?: Record<string, unknown> | null;
  summary?: string;
  chunks?: { chunk_index?: number; content: string }[];
}

export interface WorkspaceFolderMount {
  path: string;
  label: string;
  primary?: boolean;
}

export interface ProjectScopeRule {
  id: string;
  pathPrefix: string;
  label?: string;
}

export interface WorkspaceLineMessage {
  id: string;
  workspace_id: string;
  project_id?: string | null;
  target_project_id?: string | null;
  channel: string;
  session_id?: string | null;
  task_id?: string | null;
  message_kind: string;
  title?: string | null;
  body?: string | null;
  metadata?: Record<string, unknown>;
  created_at?: string | null;
  updated_at?: string | null;
}

export interface WorkspaceChannel {
  id: string;
  type: "workspace" | "project";
  label: string;
  workspaceId: string;
  projectId?: string | null;
}

export interface AgentSessionSummary {
  id: string;
  nativeSessionId: string;
  projectId?: string | null;
  workspaceId?: string | null;
  taskId?: string | null;
  runtime: "claude-code" | "codex" | string;
  title: string;
  state: string;
  isCurrent?: boolean;
  model?: string | null;
  cwd?: string | null;
  rolloutPath?: string | null;
  startedAt?: string | null;
  updatedAt?: string | null;
  permissionMode?: string | null;
  preview?: string;
  messages: TaskMessage[];
  recentTools?: string[];
  plan?: { step: string; status: string }[];
  touchedFiles?: string[];
  rateLimits?: Record<string, unknown>;
  taskStatus?: string | null;
}

export interface ProjectSummary {
  id: string;
  workspaceId?: string | null;
  name: string;
  label?: string;
  description?: string | null;
  defaultRuntime?: string | null;
  color?: string | null;
  icon?: string | null;
  updatedAt?: string | null;
  scopeRules?: ProjectScopeRule[];
  sessions: AgentSessionSummary[];
}

export interface WorkspaceSummary {
  id: string;
  name: string;
  label?: string;
  description?: string | null;
  rootPath: string;
  workspacePath: string;
  folders?: WorkspaceFolderMount[];
  mounts?: WorkspaceFolderMount[];
  updatedAt?: string | null;
  projects: ProjectSummary[];
  sessions: AgentSessionSummary[];
  sessionCount?: number;
}

export interface TaskDetailSnapshot {
  live: boolean;
  task: SankhyaTask;
  results: SharedTaskResult[];
  runtime: {
    live: boolean;
    repo: string;
    runtimes: RuntimeStatusCard[];
  };
}

export interface WorkspaceGraphNode {
  id: string;
  type:
    | "workspace"
    | "project"
    | "channel"
    | "session"
    | "task"
    | "result"
    | "file"
    | "asset"
    | "broadcast"
    | string;
  label: string;
  subLabel?: string;
  body?: string;
  accent?: string;
  status?: string;
  val?: number;
  meta?: Record<string, unknown>;
}

export interface WorkspaceGraphEdge {
  id: string;
  source: string;
  target: string;
  label: string;
  curvature?: number;
}

export interface CanvasSelectionState {
  nodeId?: string | null;
  nodeType?: string | null;
}

export interface SuggestedTask {
  id: string;
  title: string;
  status: string;
  projectId?: string | null;
  workspaceId?: string | null;
}

export interface WorkspaceSessionSnapshot {
  id: string;
  title: string;
  cwd: string;
  model?: string | null;
  updatedAt?: string | null;
  updatedAtLabel?: string;
  rolloutPath?: string;
  isCurrent?: boolean;
  preview?: string;
  messages?: { role: string; content: string; timestamp?: string }[];
  recentTools?: string[];
  plan?: { step: string; status: string }[];
  touchedFiles?: string[];
  rateLimits?: Record<string, unknown>;
}

export interface WorkspaceGraphSnapshot {
  live: boolean;
  repo: string;
  workspace?: WorkspaceSummary | null;
  graph: {
    nodes: WorkspaceGraphNode[];
    links: WorkspaceGraphEdge[];
  };
  sessions: AgentSessionSummary[];
  tasks: SankhyaTask[];
  files: WorkspaceGraphNode[];
  currentSessionId?: string;
  currentProjectId?: string;
  currentWorkspaceId?: string;
  workspaces?: WorkspaceSummary[];
  line?: { messages: WorkspaceLineMessage[] };
  runtime?: {
    live: boolean;
    repo: string;
    runtimes: RuntimeStatusCard[];
  };
  error?: string;
}

export interface ProjectIndexSnapshot {
  live: boolean;
  workspaces: WorkspaceSummary[];
  currentProjectId?: string;
  currentWorkspaceId?: string;
  currentSessionId?: string;
  error?: string;
}

export interface WorkspaceDetailSnapshot {
  live: boolean;
  workspace: WorkspaceSummary;
  projects?: ProjectSummary[];
  sessions: AgentSessionSummary[];
  line?: { messages: WorkspaceLineMessage[] };
  runtime: {
    live: boolean;
    repo: string;
    runtimes: RuntimeStatusCard[];
  };
}

export interface SessionDetailSnapshot {
  live: boolean;
  project?: ProjectSummary | null;
  workspace?: WorkspaceSummary | null;
  session: AgentSessionSummary;
  task?: SankhyaTask | null;
  results: SharedTaskResult[];
  assets: SessionAsset[];
  files: FileContextSummary[];
  line?: { messages: WorkspaceLineMessage[] };
  runtime: {
    live: boolean;
    repo: string;
    runtimes: RuntimeStatusCard[];
  };
}

export interface RuntimeLimitStatus {
  supported: boolean;
  state: string;
  lastHitAt?: string | null;
  resetAt?: string | null;
  model?: string | null;
  note?: string;
}

export interface RuntimeSession {
  id: string;
  cwd: string;
  pid?: number;
  title?: string | null;
  model?: string | null;
  version?: string | null;
  entrypoint?: string | null;
  startedAt?: string | null;
  updatedAt?: string | null;
  rolloutPath?: string | null;
  state: string;
  note?: string;
}

export interface RuntimeStatusCard {
  id: "claude-code" | "codex";
  label: string;
  installed: boolean;
  state: string;
  configured: Record<string, unknown>;
  currentSession?: RuntimeSession | null;
  limits: RuntimeLimitStatus;
}

export interface CaptureSessionRecord {
  id: string;
  user_id: string;
  source_app: string;
  namespace: string;
  status: string;
  started_at: string;
  ended_at?: string | null;
  metadata?: Record<string, unknown>;
}

export interface CaptureSurfaceRecord {
  id: string;
  session_id: string;
  source_app: string;
  surface_type: string;
  title: string;
  url: string;
  app_path: string;
  path_hint: string[];
  first_seen_at: string;
  last_seen_at: string;
  metadata?: Record<string, unknown>;
}

export interface CaptureGraphRecord {
  manifest?: Record<string, unknown>;
  surfaces: CaptureSurfaceRecord[];
  actions: Record<string, unknown>[];
  observations: Record<string, unknown>[];
  artifacts: Record<string, unknown>[];
  links: Record<string, unknown>[];
}

export interface ActiveCaptureRecord {
  session: CaptureSessionRecord;
  graph: CaptureGraphRecord;
}

export interface MemoryNowSnapshot {
  live: boolean;
  sessions: CaptureSessionRecord[];
  events: Record<string, unknown>[];
  activeCapture: ActiveCaptureRecord[];
  memories: Record<string, unknown>[];
  transitions: Record<string, unknown>[];
}

export interface CaptureTimelineItem {
  kind: string;
  timestamp: string;
  item: Record<string, unknown>;
}

export interface Tweaks {
  accentHue: string;
  compactNav: boolean;
  showTimestamps: boolean;
  canvasStyle: "dots" | "grid";
}
