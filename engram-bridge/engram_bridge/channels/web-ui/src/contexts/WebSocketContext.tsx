import { createContext, useContext, useCallback, type ReactNode } from "react";
import { useWebSocket, type ConnectionStatus, type WsMessage } from "@/hooks/use-websocket";
import { useProjectContext } from "@/contexts/ProjectContext";
import { useChatStore } from "@/stores/useChatStore";
import { useTaskConversationStore } from "@/stores/useTaskConversationStore";
import type { ProcessEntry, FileChange } from "@/types";

// Extended WS message with dynamic fields from server
type Msg = WsMessage & Record<string, unknown>;

interface WebSocketContextValue {
  status: ConnectionStatus;
  send: (data: Record<string, unknown>) => void;
}

const WebSocketContext = createContext<WebSocketContextValue | null>(null);

export function useWsContext() {
  const ctx = useContext(WebSocketContext);
  if (!ctx) throw new Error("useWsContext must be used within WebSocketProvider");
  return ctx;
}

interface Props {
  children: ReactNode;
}

export function WebSocketProvider({ children }: Props) {
  const { refreshIssues } = useProjectContext();

  const handleMessage = useCallback(
    (raw: WsMessage) => {
      const msg = raw as Msg;

      // Real-time issue updates
      if (
        msg.type === "issue_created" ||
        msg.type === "issue_updated" ||
        msg.type === "issue_deleted" ||
        msg.type === "status_changed" ||
        msg.type === "issues_bulk_updated"
      ) {
        refreshIssues();
        return;
      }

      const taskId = msg.task_id as string | undefined;

      // ── Task-scoped messages → useTaskConversationStore ──
      if (taskId) {
        const store = useTaskConversationStore.getState();
        const now = new Date().toISOString();

        switch (msg.type) {
          case "task_text":
            store.addConversationEntry(taskId, {
              id: `assistant-${msg.message_id || Date.now()}`,
              type: "assistant",
              content: (msg.content as string) || "",
              ts: now,
              agent: msg.agent as string,
              streaming: msg.streaming as boolean,
            });
            break;

          case "task_edit":
            store.editConversationEntry(
              taskId,
              `assistant-${msg.message_id}`,
              (msg.content as string) || "",
              msg.streaming as boolean,
            );
            break;

          case "task_tool_use":
            store.addConversationEntry(taskId, {
              id: `tool-${Date.now()}`,
              type: "tool_use",
              content: (msg.content as string) || "",
              ts: now,
              tool: msg.tool as string,
              file_path: msg.file_path as string,
              streaming: msg.streaming as boolean,
            });
            break;

          case "task_tool_result":
            store.addConversationEntry(taskId, {
              id: `result-${Date.now()}`,
              type: "tool_result",
              content: (msg.content as string) || "",
              ts: now,
            });
            break;

          case "task_error":
            store.addConversationEntry(taskId, {
              id: `error-${Date.now()}`,
              type: "error",
              content: (msg.content as string) || "",
              ts: now,
            });
            break;

          case "task_process": {
            const processId = (msg.process_id as string) || `proc-${Date.now()}`;
            const taskData = store.tasks[taskId];
            const existing = taskData?.liveProcesses.find((p) => p.id === processId);

            if (existing) {
              store.updateProcess(taskId, processId, {
                status: (msg.status as ProcessEntry["status"]) || existing.status,
                output: (msg.output as string) || existing.output,
                completed_at: (msg.completed_at as string) || existing.completed_at,
                duration_ms: (msg.duration_ms as number) || existing.duration_ms,
                exit_code: (msg.exit_code as number) ?? existing.exit_code,
              });
            } else {
              store.addProcess(taskId, {
                id: processId,
                name: (msg.name as string) || "Process",
                status: (msg.status as ProcessEntry["status"]) || "running",
                started_at: now,
                agent: msg.agent as string,
                output: msg.output as string,
              });
            }
            break;
          }

          case "task_file_change":
            store.addFileChange(taskId, {
              path: (msg.path as string) || "",
              action: (msg.action as FileChange["action"]) || "modified",
              additions: msg.additions as number,
              deletions: msg.deletions as number,
              diff: msg.diff as string,
              ts: now,
            });
            break;

          case "task_complete":
            store.markComplete(taskId);
            break;
        }
        return;
      }

      // ── Global chat messages (no task_id) → useChatStore ──
      const chatStore = useChatStore.getState();

      if (msg.type === "text") {
        chatStore.addMessage({
          id: `agent-${msg.message_id || Date.now()}`,
          role: "agent",
          content: (msg.content as string) || "",
          messageId: msg.message_id as number,
          timestamp: new Date().toISOString(),
          streaming: msg.streaming as boolean,
        });
      } else if (msg.type === "edit") {
        if (msg.message_id) {
          chatStore.editMessage(
            msg.message_id as number,
            (msg.content as string) || "",
            msg.streaming as boolean,
          );
        }
      }
    },
    [refreshIssues],
  );

  const { status, send } = useWebSocket({ onMessage: handleMessage });

  return (
    <WebSocketContext.Provider value={{ status, send }}>
      {children}
    </WebSocketContext.Provider>
  );
}
