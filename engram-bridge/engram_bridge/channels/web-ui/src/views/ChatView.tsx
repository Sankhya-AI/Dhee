import { useEffect, useRef, useCallback } from "react";
import { useChatStore, type ChatMessage } from "@/stores/useChatStore";
import { useWsContext } from "@/contexts/WebSocketContext";
import { ChatMessage as ChatMessageComponent } from "@/components/chat-message";
import type { ChatMessageData } from "@/components/chat-message";
import { ChatInput } from "@/components/chat-input";
import { Brain } from "lucide-react";

const WELCOME_MESSAGE: ChatMessage = {
  id: "system-welcome",
  role: "system",
  content: "Welcome to Engram. Describe a task or just chat.",
  timestamp: new Date().toISOString(),
};

function toDisplayMessage(msg: ChatMessage): ChatMessageData {
  return {
    id: msg.id,
    role: msg.role,
    content: msg.content,
    messageId: msg.messageId,
    timestamp: msg.timestamp,
    streaming: msg.streaming,
  };
}

export function ChatView() {
  const { messages, addMessage } = useChatStore();
  const { send, status } = useWsContext();
  const scrollRef = useRef<HTMLDivElement>(null);

  // Auto-scroll to bottom on new messages
  useEffect(() => {
    const el = scrollRef.current;
    if (el) {
      el.scrollTop = el.scrollHeight;
    }
  }, [messages]);

  const handleSend = useCallback(
    (text: string) => {
      // Add user message to store
      addMessage({
        id: `user-${Date.now()}`,
        role: "user",
        content: text,
        timestamp: new Date().toISOString(),
      });

      // Send via WebSocket
      send({ type: "message", text });
    },
    [addMessage, send],
  );

  const allMessages = messages.length > 0 ? messages : [WELCOME_MESSAGE];

  return (
    <div className="flex-1 flex flex-col overflow-hidden">
      {/* Scrollable message area */}
      <div ref={scrollRef} className="flex-1 overflow-y-auto">
        <div className="max-w-3xl mx-auto px-4 py-6">
          {/* Empty state with branding */}
          {messages.length === 0 && (
            <div className="flex flex-col items-center justify-center pt-16 pb-8">
              <div className="w-12 h-12 rounded-2xl flex items-center justify-center bg-primary/10 mb-4">
                <Brain className="h-6 w-6 text-primary" />
              </div>
              <h2 className="text-lg font-semibold mb-1">Engram</h2>
              <p className="text-sm text-muted-foreground text-center max-w-md">
                Describe a task and I'll create it on the board, or just chat.
              </p>
            </div>
          )}

          {/* Messages */}
          {allMessages.map((msg) => (
            <ChatMessageComponent
              key={msg.id}
              message={toDisplayMessage(msg)}
            />
          ))}
        </div>
      </div>

      {/* Input area */}
      <div className="max-w-3xl mx-auto w-full">
        <ChatInput
          onSend={handleSend}
          disabled={status !== "connected"}
        />
      </div>
    </div>
  );
}
