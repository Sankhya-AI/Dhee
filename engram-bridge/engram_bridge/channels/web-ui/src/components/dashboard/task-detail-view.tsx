import { useCallback, useEffect, useRef, useState } from "react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import {
  ArrowLeft,
  Play,
  Square,
  MessageSquare,
  Terminal,
  FileCode,
  Clock,
  Bot,
  Trash2,
} from "lucide-react";
import { ConversationEntry } from "@/components/dashboard/conversation-entry";
import { FollowUpInput } from "@/components/dashboard/follow-up-input";
import { ProcessesPanel } from "@/components/dashboard/processes-panel";
import { FilesPanel } from "@/components/dashboard/files-panel";
import type {
  Task,
  AgentInfo,
  ConversationEntry as ConversationEntryType,
  ProcessEntry,
  FileChange,
  TaskStatus,
} from "@/types/dashboard";
import { PRIORITY_COLORS } from "@/types/dashboard";

interface TaskDetailViewProps {
  task: Task;
  agents: AgentInfo[];
  onBack: () => void;
  onUpdateTask: (id: string, data: Partial<Task>) => void;
  onDeleteTask: (id: string) => void;
  onExecuteTask: (taskId: string, agentName: string, prompt: string) => void;
  onFollowUp: (taskId: string, text: string) => void;
  /** Externally-pushed conversation entries (from WS) */
  liveConversation: ConversationEntryType[];
  liveProcesses: ProcessEntry[];
  liveFiles: FileChange[];
  isExecuting: boolean;
}

function timeAgo(iso: string): string {
  const diff = Date.now() - new Date(iso).getTime();
  const mins = Math.floor(diff / 60000);
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins}m ago`;
  const hrs = Math.floor(mins / 60);
  if (hrs < 24) return `${hrs}h ago`;
  return `${Math.floor(hrs / 24)}d ago`;
}

const STATUS_OPTIONS: { key: TaskStatus; label: string; color: string }[] = [
  { key: "inbox", label: "Inbox", color: "bg-zinc-500" },
  { key: "assigned", label: "Assigned", color: "bg-yellow-500" },
  { key: "active", label: "Active", color: "bg-blue-500" },
  { key: "review", label: "Review", color: "bg-purple-500" },
  { key: "done", label: "Done", color: "bg-emerald-500" },
];

export function TaskDetailView({
  task,
  agents,
  onBack,
  onUpdateTask,
  onDeleteTask,
  onExecuteTask,
  onFollowUp,
  liveConversation,
  liveProcesses,
  liveFiles,
  isExecuting,
}: TaskDetailViewProps) {
  const conversationEndRef = useRef<HTMLDivElement>(null);
  const [activeTab, setActiveTab] = useState("conversation");

  // Combine stored + live conversation
  const conversation = [
    ...(task.conversation || []),
    ...liveConversation,
  ];
  const processes = [
    ...(task.processes || []),
    ...liveProcesses,
  ];
  const files = [
    ...(task.files_changed || []),
    ...liveFiles,
  ];

  // Auto-scroll conversation
  useEffect(() => {
    if (activeTab === "conversation") {
      requestAnimationFrame(() => {
        conversationEndRef.current?.scrollIntoView({ behavior: "smooth" });
      });
    }
  }, [conversation.length, activeTab]);

  const handleExecute = useCallback(() => {
    const agent = task.assigned_agent || agents.find((a) => a.status !== "offline")?.name;
    if (!agent) return;
    onExecuteTask(task.id, agent, task.description || task.title);
  }, [task, agents, onExecuteTask]);

  const handleFollowUp = useCallback(
    (text: string) => {
      onFollowUp(task.id, text);
    },
    [task.id, onFollowUp]
  );

  const priorityClass = PRIORITY_COLORS[task.priority] || PRIORITY_COLORS.normal;
  const currentStatus = STATUS_OPTIONS.find((s) => s.key === task.status);

  return (
    <div className="flex flex-col h-full">
      {/* Header */}
      <div className="flex items-center gap-3 px-4 py-3 border-b border-border bg-card/30">
        <Button
          variant="ghost"
          size="icon"
          className="h-7 w-7"
          onClick={onBack}
        >
          <ArrowLeft className="h-4 w-4" />
        </Button>

        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2 mb-0.5">
            <h2 className="text-sm font-semibold truncate">{task.title}</h2>
            <Badge variant="outline" className={`text-[10px] px-1.5 py-0 uppercase ${priorityClass}`}>
              {task.priority}
            </Badge>
          </div>
          <div className="flex items-center gap-3 text-[11px] text-muted-foreground">
            <span className="flex items-center gap-1">
              <Clock className="h-3 w-3" />
              {timeAgo(task.created_at)}
            </span>
            {task.assigned_agent && (
              <span className="flex items-center gap-1">
                <Bot className="h-3 w-3" />
                {task.assigned_agent}
              </span>
            )}
            {task.tags.map((tag) => (
              <Badge key={tag} variant="outline" className="text-[10px] px-1.5 py-0 text-muted-foreground">
                {tag}
              </Badge>
            ))}
          </div>
        </div>

        {/* Status selector */}
        <div className="flex items-center gap-1.5">
          {STATUS_OPTIONS.map((s) => (
            <button
              key={s.key}
              className={`flex items-center gap-1 px-2 py-1 rounded text-[11px] transition-colors ${
                task.status === s.key
                  ? "bg-primary/15 text-foreground font-medium"
                  : "text-muted-foreground/60 hover:text-foreground hover:bg-secondary/30"
              }`}
              onClick={() => onUpdateTask(task.id, { status: s.key })}
            >
              <div className={`w-1.5 h-1.5 rounded-full ${s.color}`} />
              {s.label}
            </button>
          ))}
        </div>

        {/* Actions */}
        <div className="flex items-center gap-1.5 ml-2">
          {!isExecuting ? (
            <Button
              size="sm"
              className="h-7 text-xs gap-1"
              onClick={handleExecute}
              disabled={!task.assigned_agent && agents.length === 0}
            >
              <Play className="h-3 w-3" />
              Run
            </Button>
          ) : (
            <Button
              size="sm"
              variant="destructive"
              className="h-7 text-xs gap-1"
            >
              <Square className="h-3 w-3" />
              Stop
            </Button>
          )}
          <Button
            variant="ghost"
            size="icon"
            className="h-7 w-7 text-muted-foreground hover:text-red-400"
            onClick={() => onDeleteTask(task.id)}
          >
            <Trash2 className="h-3.5 w-3.5" />
          </Button>
        </div>
      </div>

      {/* Main content area — split view */}
      <div className="flex flex-1 overflow-hidden">
        {/* Left: Conversation panel */}
        <div className="flex-1 flex flex-col min-w-0 border-r border-border">
          {/* Conversation header */}
          <div className="flex items-center gap-2 px-4 py-2 border-b border-border/50">
            <MessageSquare className="h-3.5 w-3.5 text-muted-foreground" />
            <span className="text-xs font-semibold text-muted-foreground uppercase tracking-wider">
              Conversation
            </span>
            <Badge variant="outline" className="text-[10px]">
              {conversation.length}
            </Badge>
          </div>

          {/* Conversation messages */}
          <div className="flex-1 overflow-y-auto">
            {conversation.length === 0 && !task.description ? (
              <div className="flex flex-col items-center justify-center h-full text-muted-foreground/40 py-12">
                <MessageSquare className="h-8 w-8 mb-2" />
                <p className="text-sm">No conversation yet</p>
                <p className="text-xs">Click "Run" to start the agent or send a follow-up</p>
              </div>
            ) : (
              <div className="py-2">
                {/* Show task description as initial context */}
                {task.description && conversation.length === 0 && (
                  <div className="px-4 py-3 mx-3 my-2 rounded-lg bg-muted/20 border border-border/30">
                    <p className="text-xs font-semibold text-muted-foreground mb-1">Task Description</p>
                    <p className="text-sm text-foreground/80">{task.description}</p>
                  </div>
                )}

                {conversation.map((entry) => (
                  <ConversationEntry key={entry.id} entry={entry} />
                ))}
                <div ref={conversationEndRef} />
              </div>
            )}
          </div>

          {/* Follow-up input */}
          <FollowUpInput
            onSend={handleFollowUp}
            isExecuting={isExecuting}
          />
        </div>

        {/* Right: Tabbed panel (processes, files, details) */}
        <div className="w-[400px] flex-shrink-0 flex flex-col">
          <Tabs value={activeTab} onValueChange={setActiveTab} className="flex flex-col h-full">
            <TabsList className="w-full justify-start rounded-none border-b border-border bg-transparent px-2 h-auto py-0">
              <TabsTrigger
                value="conversation"
                className="rounded-none border-b-2 border-transparent data-[state=active]:border-primary data-[state=active]:bg-transparent text-xs py-2 px-3"
              >
                <MessageSquare className="h-3 w-3 mr-1.5" />
                Details
              </TabsTrigger>
              <TabsTrigger
                value="processes"
                className="rounded-none border-b-2 border-transparent data-[state=active]:border-primary data-[state=active]:bg-transparent text-xs py-2 px-3"
              >
                <Terminal className="h-3 w-3 mr-1.5" />
                Processes
                {processes.filter((p) => p.status === "running").length > 0 && (
                  <span className="ml-1 w-1.5 h-1.5 rounded-full bg-blue-400 animate-pulse" />
                )}
              </TabsTrigger>
              <TabsTrigger
                value="files"
                className="rounded-none border-b-2 border-transparent data-[state=active]:border-primary data-[state=active]:bg-transparent text-xs py-2 px-3"
              >
                <FileCode className="h-3 w-3 mr-1.5" />
                Files
                {files.length > 0 && (
                  <Badge variant="outline" className="ml-1.5 text-[9px] px-1 py-0 h-4">
                    {files.length}
                  </Badge>
                )}
              </TabsTrigger>
            </TabsList>

            <TabsContent value="conversation" className="flex-1 overflow-y-auto mt-0 p-4">
              {/* Task meta details */}
              <div className="space-y-4">
                <div>
                  <label className="text-xs font-semibold text-muted-foreground block mb-1">Description</label>
                  <p className="text-sm text-foreground/80">{task.description || "No description"}</p>
                </div>

                <div>
                  <label className="text-xs font-semibold text-muted-foreground block mb-1">Assigned Agent</label>
                  <div className="flex items-center gap-2">
                    {task.assigned_agent ? (
                      <>
                        <div className="w-5 h-5 rounded-full bg-primary/20 flex items-center justify-center text-[10px] font-bold text-primary">
                          {task.assigned_agent[0].toUpperCase()}
                        </div>
                        <span className="text-sm">{task.assigned_agent}</span>
                        <select
                          value={task.assigned_agent}
                          onChange={(e) => onUpdateTask(task.id, { assigned_agent: e.target.value || null })}
                          className="ml-auto bg-input rounded px-2 py-1 text-xs outline-none"
                        >
                          <option value="">Unassign</option>
                          {agents.map((a) => (
                            <option key={a.name} value={a.name}>
                              {a.name} ({a.type})
                            </option>
                          ))}
                        </select>
                      </>
                    ) : (
                      <select
                        value=""
                        onChange={(e) => onUpdateTask(task.id, { assigned_agent: e.target.value, status: "assigned" })}
                        className="bg-input rounded px-2 py-1 text-xs outline-none"
                      >
                        <option value="">Select agent...</option>
                        {agents.map((a) => (
                          <option key={a.name} value={a.name}>
                            {a.name} ({a.type})
                          </option>
                        ))}
                      </select>
                    )}
                  </div>
                </div>

                {/* Comments */}
                {task.comments.length > 0 && (
                  <div>
                    <label className="text-xs font-semibold text-muted-foreground block mb-2">
                      Comments ({task.comments.length})
                    </label>
                    <div className="space-y-2">
                      {task.comments.map((c) => (
                        <div key={c.id} className="rounded-md bg-muted/20 border border-border/30 p-2.5">
                          <div className="flex items-center gap-2 mb-1">
                            <span className="text-xs font-medium">{c.agent}</span>
                            <span className="text-[10px] text-muted-foreground/60">{timeAgo((c as unknown as Record<string, string>).ts || c.timestamp)}</span>
                          </div>
                          <p className="text-xs text-foreground/80">{c.text}</p>
                        </div>
                      ))}
                    </div>
                  </div>
                )}

                {/* Timeline summary */}
                <div>
                  <label className="text-xs font-semibold text-muted-foreground block mb-1">Timeline</label>
                  <div className="space-y-1 text-xs text-muted-foreground">
                    <div className="flex justify-between">
                      <span>Created</span>
                      <span>{new Date(task.created_at).toLocaleString()}</span>
                    </div>
                    <div className="flex justify-between">
                      <span>Updated</span>
                      <span>{new Date(task.updated_at).toLocaleString()}</span>
                    </div>
                  </div>
                </div>
              </div>
            </TabsContent>

            <TabsContent value="processes" className="flex-1 overflow-hidden mt-0">
              <ProcessesPanel processes={processes} />
            </TabsContent>

            <TabsContent value="files" className="flex-1 overflow-hidden mt-0">
              <FilesPanel files={files} />
            </TabsContent>
          </Tabs>
        </div>
      </div>
    </div>
  );
}
