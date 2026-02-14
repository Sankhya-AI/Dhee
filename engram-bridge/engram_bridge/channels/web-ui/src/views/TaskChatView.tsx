import { useState, useCallback, useEffect, useMemo, useRef } from "react";
import { useParams, useNavigate } from "react-router-dom";
import {
  Loader2,
  FileCode,
  Terminal,
  FolderTree,
  Play,
  Square,
  ArrowLeft,
  ChevronDown,
  GitBranch,
} from "lucide-react";
import { useProjectContext } from "@/contexts/ProjectContext";
import { useWsContext } from "@/contexts/WebSocketContext";
import { useTaskConversationStore } from "@/stores/useTaskConversationStore";
import { ConversationEntry } from "@/components/dashboard/conversation-entry";
import { ProcessesPanel } from "@/components/dashboard/processes-panel";
import { FilesPanel } from "@/components/dashboard/files-panel";
import { FollowUpInput } from "@/components/dashboard/follow-up-input";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { StatusDot } from "@/components/primitives/StatusDot";
import { api, useAgents } from "@/hooks/use-api";
import type { Issue, ConversationEntry as ConversationEntryType } from "@/types";

type RightTab = "changes" | "processes" | "files" | "subtasks";

function timeAgo(iso: string): string {
  const diff = Date.now() - new Date(iso).getTime();
  const mins = Math.floor(diff / 60000);
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins}m ago`;
  const hrs = Math.floor(mins / 60);
  if (hrs < 24) return `${hrs}h ago`;
  return `${Math.floor(hrs / 24)}d ago`;
}

function TaskListRow({
  issue,
  selected,
  onClick,
}: {
  issue: Issue;
  selected: boolean;
  onClick: () => void;
}) {
  const taskData = useTaskConversationStore((s) => s.tasks[issue.id]);
  const isExecuting =
    taskData?.isExecuting ||
    issue.conversation?.some((e) => e.type === "assistant" && e.streaming);

  return (
    <button
      onClick={onClick}
      className={`w-full text-left px-3 py-2.5 border-b border-border/40 transition-colors ${
        selected
          ? "bg-accent text-accent-foreground"
          : "hover:bg-muted/50"
      }`}
    >
      <div className="flex items-center gap-2">
        {isExecuting ? (
          <Loader2 className="h-3 w-3 animate-spin text-blue-500 flex-shrink-0" />
        ) : (
          <StatusDot
            color={
              issue.status === "done"
                ? "#22c55e"
                : issue.status === "active"
                  ? "#3b82f6"
                  : "#94a3b8"
            }
            size={6}
          />
        )}
        <span className="text-sm font-medium truncate flex-1">
          {issue.title}
        </span>
        {issue.issue_number > 0 && (
          <span className="text-[10px] font-mono text-muted-foreground">
            #{issue.issue_number}
          </span>
        )}
      </div>
      <div className="flex items-center gap-2 mt-1 text-[10px] text-muted-foreground">
        {issue.assigned_agent && (
          <span className="truncate">{issue.assigned_agent}</span>
        )}
        <span className="ml-auto">{timeAgo(issue.updated_at)}</span>
      </div>
    </button>
  );
}

export function TaskChatView() {
  const { taskId } = useParams<{ taskId: string }>();
  const navigate = useNavigate();
  const { issues, statuses } = useProjectContext();
  const { send } = useWsContext();
  const { agents } = useAgents();
  const [detail, setDetail] = useState<Issue | null>(null);
  const [rightTab, setRightTab] = useState<RightTab>("changes");
  const [loadingDetail, setLoadingDetail] = useState(false);
  const [selectedAgent, setSelectedAgent] = useState<string>("");
  const [showAgentMenu, setShowAgentMenu] = useState(false);
  const scrollRef = useRef<HTMLDivElement>(null);

  // Live data from store
  const taskData = useTaskConversationStore((s) =>
    taskId ? s.tasks[taskId] : undefined,
  );
  const liveConversation = taskData?.liveConversation ?? [];
  const liveProcesses = taskData?.liveProcesses ?? [];
  const liveFiles = taskData?.liveFiles ?? [];
  const isExecuting = taskData?.isExecuting ?? false;

  // Sub-tasks
  const subTasks = useMemo(
    () => issues.filter((i) => i.parent_task_id === taskId),
    [issues, taskId],
  );

  // Load full detail when taskId changes
  useEffect(() => {
    if (!taskId) return;
    setLoadingDetail(true);
    api
      .getIssue(taskId)
      .then((d) => {
        setDetail(d);
        setSelectedAgent(d.assigned_agent || "");
      })
      .catch(() => setDetail(null))
      .finally(() => setLoadingDetail(false));
  }, [taskId]);

  // Merge stored + live conversation
  const allConversation = useMemo(() => {
    const stored = detail?.conversation ?? [];
    return [...stored, ...liveConversation];
  }, [detail?.conversation, liveConversation]);

  const allProcesses = useMemo(() => {
    const stored = detail?.processes ?? [];
    return [...stored, ...liveProcesses];
  }, [detail?.processes, liveProcesses]);

  const allFiles = useMemo(() => {
    const stored = detail?.files_changed ?? [];
    return [...stored, ...liveFiles];
  }, [detail?.files_changed, liveFiles]);

  // Auto-scroll on new conversation entries
  useEffect(() => {
    const el = scrollRef.current;
    if (el) {
      el.scrollTop = el.scrollHeight;
    }
  }, [allConversation]);

  const handleFollowUp = useCallback(
    (text: string) => {
      if (!taskId) return;
      // Add user message to live store
      const store = useTaskConversationStore.getState();
      store.addConversationEntry(taskId, {
        id: `user-${Date.now()}`,
        type: "user",
        content: text,
        ts: new Date().toISOString(),
      });
      send({ type: "task_followup", task_id: taskId, text });
    },
    [taskId, send],
  );

  const handleRun = useCallback(() => {
    if (!taskId || !detail) return;
    const agent = selectedAgent || agents[0]?.name || "default";
    const store = useTaskConversationStore.getState();
    store.setExecuting(taskId, true);
    store.addConversationEntry(taskId, {
      id: `system-${Date.now()}`,
      type: "system",
      content: `Dispatching to ${agent}...`,
      ts: new Date().toISOString(),
    });
    send({
      type: "task_execute",
      task_id: taskId,
      agent,
      prompt: detail.description || detail.title,
    });
  }, [taskId, detail, selectedAgent, agents, send]);

  const handleStop = useCallback(() => {
    if (!taskId) return;
    send({ type: "task_stop", task_id: taskId });
    const store = useTaskConversationStore.getState();
    store.setExecuting(taskId, false);
  }, [taskId, send]);

  return (
    <div className="flex flex-1 overflow-hidden">
      {/* Left: Task list sidebar */}
      <div className="w-56 border-r border-border flex flex-col bg-sidebar overflow-hidden">
        <div className="px-3 py-2.5 border-b border-border">
          <h2 className="text-xs font-semibold text-muted-foreground uppercase tracking-wider">
            Tasks
          </h2>
        </div>
        <div className="flex-1 overflow-y-auto">
          {issues.length === 0 ? (
            <div className="p-4 text-center text-sm text-muted-foreground">
              No tasks yet.
            </div>
          ) : (
            issues.map((issue) => (
              <TaskListRow
                key={issue.id}
                issue={issue}
                selected={taskId === issue.id}
                onClick={() => navigate(`/task/${issue.id}`)}
              />
            ))
          )}
        </div>
      </div>

      {/* Center: Conversation */}
      <div className="flex-1 flex flex-col overflow-hidden">
        {!taskId || loadingDetail ? (
          <div className="flex-1 flex items-center justify-center">
            {loadingDetail ? (
              <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" />
            ) : (
              <div className="text-center text-muted-foreground">
                <Terminal className="h-10 w-10 mx-auto mb-3 opacity-30" />
                <p className="text-sm font-medium">Select a task</p>
                <p className="text-xs mt-1">
                  Choose a task to view its conversation
                </p>
              </div>
            )}
          </div>
        ) : detail ? (
          <>
            {/* Task header */}
            <div className="flex items-center justify-between px-4 py-2.5 border-b border-border">
              <div className="flex items-center gap-2 min-w-0">
                <Button
                  variant="ghost"
                  size="icon"
                  className="h-7 w-7 shrink-0"
                  onClick={() => navigate("/board")}
                >
                  <ArrowLeft className="h-4 w-4" />
                </Button>
                <h3 className="text-sm font-semibold truncate">
                  {detail.title}
                </h3>
                {detail.issue_number > 0 && (
                  <span className="text-[10px] font-mono text-muted-foreground">
                    #{detail.issue_number}
                  </span>
                )}
              </div>
              <div className="flex items-center gap-2">
                {/* Agent selector */}
                <div className="relative">
                  <button
                    className="flex items-center gap-1 px-2 py-1 text-xs rounded-md border border-border hover:bg-muted transition-colors"
                    onClick={() => setShowAgentMenu(!showAgentMenu)}
                  >
                    <span className="text-muted-foreground">
                      {selectedAgent || "Agent"}
                    </span>
                    <ChevronDown className="h-3 w-3 text-muted-foreground" />
                  </button>
                  {showAgentMenu && (
                    <div className="absolute top-full right-0 mt-1 w-40 bg-popover border border-border rounded-lg shadow-lg z-50">
                      <div className="py-1">
                        {agents.map((a) => (
                          <button
                            key={a.name}
                            onClick={() => {
                              setSelectedAgent(a.name);
                              setShowAgentMenu(false);
                            }}
                            className="flex items-center gap-2 w-full px-3 py-1.5 text-xs hover:bg-muted transition-colors"
                          >
                            {a.name}
                            <span className="text-[10px] text-muted-foreground ml-auto">
                              {a.type}
                            </span>
                          </button>
                        ))}
                      </div>
                    </div>
                  )}
                </div>

                {/* Run/Stop */}
                {isExecuting ? (
                  <Button
                    size="sm"
                    variant="outline"
                    className="h-7 text-xs gap-1 text-red-600 border-red-200 hover:bg-red-50"
                    onClick={handleStop}
                  >
                    <Square className="h-3 w-3" />
                    Stop
                  </Button>
                ) : (
                  <Button
                    size="sm"
                    className="h-7 text-xs gap-1"
                    onClick={handleRun}
                  >
                    <Play className="h-3 w-3" />
                    Run
                  </Button>
                )}

                {isExecuting && (
                  <Badge
                    variant="outline"
                    className="text-[10px] gap-1 bg-blue-50 text-blue-600 border-blue-200"
                  >
                    <Loader2 className="h-3 w-3 animate-spin" />
                    Running
                  </Badge>
                )}
              </div>
            </div>

            {/* Conversation entries */}
            <div ref={scrollRef} className="flex-1 overflow-y-auto">
              <div className="max-w-3xl mx-auto py-2">
                {/* Task description as context */}
                {detail.description && (
                  <div className="px-3 py-2 mb-2">
                    <div className="text-xs text-muted-foreground/60 mb-1">
                      Task Description
                    </div>
                    <div className="text-sm text-foreground/70">
                      {detail.description}
                    </div>
                  </div>
                )}
                {allConversation.map((entry, i) => (
                  <ConversationEntry key={entry.id || i} entry={entry} />
                ))}
              </div>
            </div>

            {/* Follow-up input */}
            <FollowUpInput
              onSend={handleFollowUp}
              isExecuting={isExecuting}
              placeholder="Follow up on this task..."
            />
          </>
        ) : (
          <div className="flex-1 flex items-center justify-center text-muted-foreground text-sm">
            Task not found
          </div>
        )}
      </div>

      {/* Right: Tabs panel */}
      {detail && (
        <div className="w-80 border-l border-border flex flex-col overflow-hidden">
          {/* Tab bar */}
          <div className="flex border-b border-border">
            {(
              [
                { key: "changes", label: "Changes", icon: FileCode },
                { key: "processes", label: "Processes", icon: Terminal },
                { key: "files", label: "Files", icon: FolderTree },
                { key: "subtasks", label: "Sub-tasks", icon: GitBranch },
              ] as const
            ).map(({ key, label, icon: Icon }) => (
              <button
                key={key}
                onClick={() => setRightTab(key)}
                className={`flex-1 flex items-center justify-center gap-1 px-1.5 py-2 text-[11px] font-medium transition-colors border-b-2 ${
                  rightTab === key
                    ? "border-primary text-foreground"
                    : "border-transparent text-muted-foreground hover:text-foreground"
                }`}
              >
                <Icon className="h-3.5 w-3.5" />
                {label}
                {key === "subtasks" && subTasks.length > 0 && (
                  <span className="text-[10px] bg-muted px-1 rounded">
                    {subTasks.length}
                  </span>
                )}
              </button>
            ))}
          </div>

          {/* Tab content */}
          <div className="flex-1 overflow-hidden">
            {rightTab === "changes" && (
              <FilesPanel files={allFiles} />
            )}
            {rightTab === "processes" && (
              <ProcessesPanel processes={allProcesses} />
            )}
            {rightTab === "files" && (
              <FileTreePanel files={allFiles} />
            )}
            {rightTab === "subtasks" && (
              <SubTasksPanel subTasks={subTasks} />
            )}
          </div>
        </div>
      )}
    </div>
  );
}

// ── File tree panel (grouped by directory) ──
function FileTreePanel({ files }: { files: Issue["files_changed"] }) {
  const tree = useMemo(() => {
    const dirs: Record<string, typeof files> = {};
    for (const f of files) {
      const parts = f.path.split("/");
      const dir = parts.slice(0, -1).join("/") || ".";
      if (!dirs[dir]) dirs[dir] = [];
      dirs[dir].push(f);
    }
    return Object.entries(dirs).sort(([a], [b]) => a.localeCompare(b));
  }, [files]);

  if (files.length === 0) {
    return (
      <div className="flex flex-col items-center justify-center h-full text-muted-foreground py-12">
        <FolderTree className="h-8 w-8 mb-2 opacity-30" />
        <p className="text-sm">No files changed</p>
      </div>
    );
  }

  return (
    <div className="p-3 overflow-y-auto h-full">
      {tree.map(([dir, dirFiles]) => (
        <div key={dir} className="mb-3">
          <div className="text-[11px] font-mono text-muted-foreground mb-1">
            {dir}/
          </div>
          {dirFiles.map((f) => {
            const name = f.path.split("/").pop() || f.path;
            return (
              <div
                key={f.path}
                className="flex items-center gap-2 pl-3 py-1 text-xs"
              >
                <FileCode className="h-3 w-3 text-muted-foreground" />
                <span className="font-mono">{name}</span>
                <span
                  className={`text-[10px] ${
                    f.action === "created"
                      ? "text-emerald-600"
                      : f.action === "deleted"
                        ? "text-red-500"
                        : "text-blue-500"
                  }`}
                >
                  {f.action}
                </span>
              </div>
            );
          })}
        </div>
      ))}
    </div>
  );
}

// ── Sub-tasks panel ──
function SubTasksPanel({ subTasks }: { subTasks: Issue[] }) {
  const navigate = useNavigate();

  if (subTasks.length === 0) {
    return (
      <div className="flex flex-col items-center justify-center h-full text-muted-foreground py-12">
        <GitBranch className="h-8 w-8 mb-2 opacity-30" />
        <p className="text-sm">No sub-tasks</p>
        <p className="text-xs mt-1">
          Sub-tasks will appear when the agent creates them
        </p>
      </div>
    );
  }

  return (
    <div className="overflow-y-auto h-full">
      {subTasks.map((task) => {
        const taskData = useTaskConversationStore.getState().tasks[task.id];
        const isActive = taskData?.isExecuting;

        return (
          <button
            key={task.id}
            onClick={() => navigate(`/task/${task.id}`)}
            className="w-full text-left px-4 py-3 border-b border-border/30 hover:bg-muted/50 transition-colors"
          >
            <div className="flex items-center gap-2">
              {isActive ? (
                <Loader2 className="h-3 w-3 animate-spin text-blue-500" />
              ) : (
                <StatusDot
                  color={
                    task.status === "done"
                      ? "#22c55e"
                      : task.status === "active"
                        ? "#3b82f6"
                        : "#94a3b8"
                  }
                  size={6}
                />
              )}
              <span className="text-sm font-medium truncate flex-1">
                {task.title}
              </span>
              {task.issue_number > 0 && (
                <span className="text-[10px] font-mono text-muted-foreground">
                  #{task.issue_number}
                </span>
              )}
            </div>
            <div className="flex items-center gap-2 mt-1 text-[10px] text-muted-foreground">
              {task.assigned_agent && <span>{task.assigned_agent}</span>}
              {isActive && (
                <Badge
                  variant="outline"
                  className="text-[9px] px-1 py-0 bg-blue-50 text-blue-600 border-blue-200"
                >
                  Running
                </Badge>
              )}
              <span className="ml-auto">{timeAgo(task.updated_at)}</span>
            </div>
          </button>
        );
      })}
    </div>
  );
}
