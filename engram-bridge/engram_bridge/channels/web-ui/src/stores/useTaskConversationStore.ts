import { create } from "zustand";
import type { ConversationEntry, ProcessEntry, FileChange } from "@/types";

interface TaskConversationData {
  liveConversation: ConversationEntry[];
  liveProcesses: ProcessEntry[];
  liveFiles: FileChange[];
  isExecuting: boolean;
}

interface TaskConversationState {
  tasks: Record<string, TaskConversationData>;

  // Conversation
  addConversationEntry: (taskId: string, entry: ConversationEntry) => void;
  editConversationEntry: (
    taskId: string,
    entryId: string,
    content: string,
    streaming?: boolean,
  ) => void;

  // Processes
  addProcess: (taskId: string, process: ProcessEntry) => void;
  updateProcess: (taskId: string, processId: string, data: Partial<ProcessEntry>) => void;

  // Files
  addFileChange: (taskId: string, file: FileChange) => void;

  // State
  setExecuting: (taskId: string, executing: boolean) => void;
  markComplete: (taskId: string) => void;
  resetTask: (taskId: string) => void;
}

function getOrCreate(
  tasks: Record<string, TaskConversationData>,
  taskId: string,
): TaskConversationData {
  return tasks[taskId] || {
    liveConversation: [],
    liveProcesses: [],
    liveFiles: [],
    isExecuting: false,
  };
}

export const useTaskConversationStore = create<TaskConversationState>((set) => ({
  tasks: {},

  addConversationEntry: (taskId, entry) =>
    set((s) => {
      const data = getOrCreate(s.tasks, taskId);
      return {
        tasks: {
          ...s.tasks,
          [taskId]: {
            ...data,
            liveConversation: [...data.liveConversation, entry],
          },
        },
      };
    }),

  editConversationEntry: (taskId, entryId, content, streaming) =>
    set((s) => {
      const data = getOrCreate(s.tasks, taskId);
      return {
        tasks: {
          ...s.tasks,
          [taskId]: {
            ...data,
            liveConversation: data.liveConversation.map((e) =>
              e.id === entryId
                ? { ...e, content, streaming: streaming ?? e.streaming }
                : e,
            ),
          },
        },
      };
    }),

  addProcess: (taskId, process) =>
    set((s) => {
      const data = getOrCreate(s.tasks, taskId);
      return {
        tasks: {
          ...s.tasks,
          [taskId]: {
            ...data,
            liveProcesses: [...data.liveProcesses, process],
          },
        },
      };
    }),

  updateProcess: (taskId, processId, updates) =>
    set((s) => {
      const data = getOrCreate(s.tasks, taskId);
      return {
        tasks: {
          ...s.tasks,
          [taskId]: {
            ...data,
            liveProcesses: data.liveProcesses.map((p) =>
              p.id === processId ? { ...p, ...updates } : p,
            ),
          },
        },
      };
    }),

  addFileChange: (taskId, file) =>
    set((s) => {
      const data = getOrCreate(s.tasks, taskId);
      return {
        tasks: {
          ...s.tasks,
          [taskId]: {
            ...data,
            liveFiles: [...data.liveFiles, file],
          },
        },
      };
    }),

  setExecuting: (taskId, executing) =>
    set((s) => {
      const data = getOrCreate(s.tasks, taskId);
      return {
        tasks: {
          ...s.tasks,
          [taskId]: { ...data, isExecuting: executing },
        },
      };
    }),

  markComplete: (taskId) =>
    set((s) => {
      const data = getOrCreate(s.tasks, taskId);
      return {
        tasks: {
          ...s.tasks,
          [taskId]: {
            ...data,
            isExecuting: false,
            liveConversation: [
              ...data.liveConversation,
              {
                id: `system-done-${Date.now()}`,
                type: "system" as const,
                content: "Task execution completed",
                ts: new Date().toISOString(),
              },
            ],
          },
        },
      };
    }),

  resetTask: (taskId) =>
    set((s) => {
      const { [taskId]: _, ...rest } = s.tasks;
      return { tasks: rest };
    }),
}));
