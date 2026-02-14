import { useCallback, useState } from "react";
import { AgentRoster } from "@/components/dashboard/agent-roster";
import { KanbanBoard } from "@/components/dashboard/kanban-board";
import { LiveFeed } from "@/components/dashboard/live-feed";
import { NewTaskDialog } from "@/components/dashboard/new-task-dialog";
import { TaskDetailView } from "@/components/dashboard/task-detail-view";
import { useAgents } from "@/hooks/use-api";
import { useTasks } from "@/hooks/use-api";
import { useFeed } from "@/hooks/use-api";
import type {
  Task,
  TaskStatus,
  FeedEvent,
  ConversationEntry,
  ProcessEntry,
  FileChange,
} from "@/types/dashboard";
import type { WsMessage } from "@/hooks/use-websocket";

interface DashboardProps {
  onWsMessage?: (msg: WsMessage) => void;
  send?: (data: Record<string, unknown>) => void;
}

export function Dashboard({ send }: DashboardProps) {
  const { agents } = useAgents();
  const { tasks, create, update, remove } = useTasks();
  const { feed, addEvent } = useFeed();
  const [newTaskOpen, setNewTaskOpen] = useState(false);
  const [selectedTask, setSelectedTask] = useState<Task | null>(null);

  // Per-task live data from WebSocket
  const [liveConversation, setLiveConversation] = useState<ConversationEntry[]>([]);
  const [liveProcesses, setLiveProcesses] = useState<ProcessEntry[]>([]);
  const [liveFiles, setLiveFiles] = useState<FileChange[]>([]);
  const [isExecuting, setIsExecuting] = useState(false);

  const activeCount = agents.filter((a) => a.status === "active").length;

  const handleMoveTask = useCallback(
    async (taskId: string, newStatus: TaskStatus) => {
      await update(taskId, { status: newStatus });
    },
    [update]
  );

  const handleCreateTask = useCallback(
    async (data: Partial<Task>) => {
      await create(data);
    },
    [create]
  );

  const handleSelectTask = useCallback(
    (task: Task) => {
      setSelectedTask(task);
      // Reset live data when opening a different task
      setLiveConversation([]);
      setLiveProcesses([]);
      setLiveFiles([]);
      setIsExecuting(false);
    },
    []
  );

  const handleBack = useCallback(() => {
    setSelectedTask(null);
    setLiveConversation([]);
    setLiveProcesses([]);
    setLiveFiles([]);
    setIsExecuting(false);
  }, []);

  const handleUpdateTask = useCallback(
    async (id: string, data: Partial<Task>) => {
      const updated = await update(id, data);
      // Refresh selected task view
      if (selectedTask?.id === id && updated) {
        setSelectedTask(updated);
      }
    },
    [update, selectedTask]
  );

  const handleDeleteTask = useCallback(
    async (id: string) => {
      await remove(id);
      setSelectedTask(null);
    },
    [remove]
  );

  const handleExecuteTask = useCallback(
    (taskId: string, agentName: string, prompt: string) => {
      setIsExecuting(true);
      // Add initial user entry to conversation
      const now = new Date().toISOString();
      setLiveConversation((prev) => [
        ...prev,
        {
          id: `user-${Date.now()}`,
          type: "user",
          content: prompt,
          ts: now,
        },
        {
          id: `system-${Date.now()}`,
          type: "system",
          content: `Dispatching to ${agentName}...`,
          ts: now,
        },
      ]);

      // Update task status to active
      update(taskId, { status: "active" });

      // Send execute command via WebSocket
      if (send) {
        send({
          type: "task_execute",
          task_id: taskId,
          agent: agentName,
          prompt,
        });
      }
    },
    [update, send]
  );

  const handleFollowUp = useCallback(
    (taskId: string, text: string) => {
      const now = new Date().toISOString();
      setLiveConversation((prev) => [
        ...prev,
        {
          id: `user-${Date.now()}`,
          type: "user",
          content: text,
          ts: now,
        },
      ]);

      if (send) {
        send({
          type: "task_followup",
          task_id: taskId,
          text,
        });
      }
    },
    [send]
  );

  // Accept feed events from WS
  const handleFeedEvent = useCallback(
    (event: FeedEvent) => {
      addEvent(event);
    },
    [addEvent]
  );

  // Handle task-specific WS messages
  const handleTaskWsMessage = useCallback(
    (msg: WsMessage & Record<string, unknown>) => {
      if (!selectedTask) return;

      const taskId = msg.task_id as string;
      if (taskId && taskId !== selectedTask.id) return;

      const now = new Date().toISOString();

      switch (msg.type) {
        case "task_text":
          setLiveConversation((prev) => [
            ...prev,
            {
              id: `assistant-${msg.message_id || Date.now()}`,
              type: "assistant",
              content: (msg.content as string) || "",
              ts: now,
              agent: msg.agent as string,
              streaming: msg.streaming as boolean,
            },
          ]);
          break;

        case "task_edit":
          setLiveConversation((prev) =>
            prev.map((e) =>
              e.id === `assistant-${msg.message_id}`
                ? { ...e, content: (msg.content as string) || "", streaming: msg.streaming as boolean }
                : e
            )
          );
          break;

        case "task_tool_use":
          setLiveConversation((prev) => [
            ...prev,
            {
              id: `tool-${Date.now()}`,
              type: "tool_use",
              content: (msg.content as string) || "",
              ts: now,
              tool: msg.tool as string,
              file_path: msg.file_path as string,
              streaming: msg.streaming as boolean,
            },
          ]);
          break;

        case "task_tool_result":
          setLiveConversation((prev) => [
            ...prev,
            {
              id: `result-${Date.now()}`,
              type: "tool_result",
              content: (msg.content as string) || "",
              ts: now,
            },
          ]);
          break;

        case "task_error":
          setLiveConversation((prev) => [
            ...prev,
            {
              id: `error-${Date.now()}`,
              type: "error",
              content: (msg.content as string) || "",
              ts: now,
            },
          ]);
          break;

        case "task_process":
          setLiveProcesses((prev) => {
            const existing = prev.find((p) => p.id === (msg.process_id as string));
            if (existing) {
              return prev.map((p) =>
                p.id === (msg.process_id as string)
                  ? {
                      ...p,
                      status: (msg.status as ProcessEntry["status"]) || p.status,
                      output: (msg.output as string) || p.output,
                      completed_at: (msg.completed_at as string) || p.completed_at,
                      duration_ms: (msg.duration_ms as number) || p.duration_ms,
                      exit_code: (msg.exit_code as number) ?? p.exit_code,
                    }
                  : p
              );
            }
            return [
              ...prev,
              {
                id: (msg.process_id as string) || `proc-${Date.now()}`,
                name: (msg.name as string) || "Process",
                status: (msg.status as ProcessEntry["status"]) || "running",
                started_at: now,
                agent: msg.agent as string,
                output: msg.output as string,
              },
            ];
          });
          break;

        case "task_file_change":
          setLiveFiles((prev) => [
            ...prev,
            {
              path: (msg.path as string) || "",
              action: (msg.action as FileChange["action"]) || "modified",
              additions: msg.additions as number,
              deletions: msg.deletions as number,
              diff: msg.diff as string,
              ts: now,
            },
          ]);
          break;

        case "task_complete":
          setIsExecuting(false);
          setLiveConversation((prev) => [
            ...prev,
            {
              id: `system-done-${Date.now()}`,
              type: "system",
              content: "Task execution completed",
              ts: now,
            },
          ]);
          update(selectedTask.id, { status: "review" });
          break;
      }
    },
    [selectedTask, update]
  );

  // Export handlers for parent to wire up
  (Dashboard as unknown as Record<string, unknown>)._onFeedEvent = handleFeedEvent;
  (Dashboard as unknown as Record<string, unknown>)._onTaskWsMessage = handleTaskWsMessage;

  // If a task is selected, show the detail view
  if (selectedTask) {
    // Get latest task data from tasks array
    const latestTask = tasks.find((t) => t.id === selectedTask.id) || selectedTask;

    return (
      <TaskDetailView
        task={latestTask}
        agents={agents}
        onBack={handleBack}
        onUpdateTask={handleUpdateTask}
        onDeleteTask={handleDeleteTask}
        onExecuteTask={handleExecuteTask}
        onFollowUp={handleFollowUp}
        liveConversation={liveConversation}
        liveProcesses={liveProcesses}
        liveFiles={liveFiles}
        isExecuting={isExecuting}
      />
    );
  }

  return (
    <>
      <div className="flex h-full overflow-hidden">
        {/* Left: Agent Roster */}
        <div className="w-[220px] border-r border-border flex-shrink-0 bg-card/30">
          <AgentRoster agents={agents} activeCount={activeCount} />
        </div>

        {/* Center: Kanban Board */}
        <div className="flex-1 min-w-0">
          <KanbanBoard
            tasks={tasks}
            onMoveTask={handleMoveTask}
            onClickTask={handleSelectTask}
            onNewTask={() => setNewTaskOpen(true)}
          />
        </div>

        {/* Right: Live Feed */}
        <div className="w-[280px] border-l border-border flex-shrink-0 bg-card/30">
          <LiveFeed events={feed} />
        </div>
      </div>

      <NewTaskDialog
        open={newTaskOpen}
        onClose={() => setNewTaskOpen(false)}
        onCreate={handleCreateTask}
        agents={agents}
      />
    </>
  );
}
