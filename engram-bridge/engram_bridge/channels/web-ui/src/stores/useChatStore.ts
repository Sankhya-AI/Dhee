import { create } from "zustand";
import { persist } from "zustand/middleware";

export interface ChatMessage {
  id: string;
  role: "user" | "agent" | "system";
  content: string;
  messageId?: number; // server-assigned, for streaming edits
  timestamp: string;
  streaming?: boolean;
}

interface ChatState {
  messages: ChatMessage[];
  addMessage: (msg: ChatMessage) => void;
  editMessage: (messageId: number, content: string, streaming?: boolean) => void;
  clearMessages: () => void;
}

export const useChatStore = create<ChatState>()(
  persist(
    (set) => ({
      messages: [],

      addMessage: (msg) =>
        set((s) => ({ messages: [...s.messages, msg] })),

      editMessage: (messageId, content, streaming) =>
        set((s) => ({
          messages: s.messages.map((m) =>
            m.messageId === messageId
              ? { ...m, content, streaming: streaming ?? m.streaming }
              : m,
          ),
        })),

      clearMessages: () => set({ messages: [] }),
    }),
    {
      name: "engram-chat",
    },
  ),
);
