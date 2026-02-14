import { renderMarkdown } from "@/lib/render-markdown";
import { Bot, User } from "lucide-react";

export interface ChatMessageData {
  id: string;
  role: "user" | "agent" | "system";
  content: string;
  messageId?: number; // server-assigned message_id for editable messages
  timestamp: string;
  streaming?: boolean;
}

interface ChatMessageProps {
  message: ChatMessageData;
}

function ThinkingDots() {
  return (
    <span className="inline-flex items-center gap-1.5 text-muted-foreground py-1">
      <span className="thinking-dot inline-block w-1.5 h-1.5 rounded-full bg-accent" />
      <span className="thinking-dot inline-block w-1.5 h-1.5 rounded-full bg-accent" />
      <span className="thinking-dot inline-block w-1.5 h-1.5 rounded-full bg-accent" />
      <span className="ml-1 text-sm italic">Thinking</span>
    </span>
  );
}

/** Detect tool-use status lines like [Read: file.py] */
function isToolStatus(content: string): boolean {
  return /^\[[A-Z][a-zA-Z]+:\s*.+\]$/.test(content.trim());
}

export function ChatMessage({ message }: ChatMessageProps) {
  const isUser = message.role === "user";
  const isSystem = message.role === "system";
  const isThinking = message.content === "...";
  const isTool = !isUser && !isSystem && isToolStatus(message.content);

  if (isSystem) {
    return (
      <div className="flex justify-center my-3">
        <div className="text-xs text-muted-foreground/80 bg-muted/30 backdrop-blur-sm px-4 py-1.5 rounded-full border border-border/50">
          {message.content}
        </div>
      </div>
    );
  }

  return (
    <div
      className={`flex mb-3 gap-2.5 ${isUser ? "justify-end" : "justify-start"}`}
      data-message-id={message.messageId}
    >
      {/* Agent avatar */}
      {!isUser && (
        <div className="flex-shrink-0 w-7 h-7 rounded-full bg-primary/15 flex items-center justify-center mt-0.5">
          <Bot className="h-3.5 w-3.5 text-primary" />
        </div>
      )}

      <div
        className={`max-w-[75%] text-sm leading-relaxed ${
          isUser
            ? "rounded-2xl rounded-br-md px-4 py-2.5 bg-primary text-primary-foreground"
            : isTool
              ? "rounded-xl px-3 py-1.5 bg-muted/50 border border-border/50"
              : "rounded-2xl rounded-bl-md px-4 py-2.5 bg-muted border border-border/30"
        }`}
      >
        {isThinking ? (
          <ThinkingDots />
        ) : isTool ? (
          <span className="text-accent italic text-xs font-mono">{message.content}</span>
        ) : (
          <div
            className="message-content [&_pre]:my-2 [&_code]:break-all"
            dangerouslySetInnerHTML={{ __html: renderMarkdown(message.content) }}
          />
        )}
        {!isTool && (
          <div
            className={`text-[10px] mt-1.5 ${
              isUser ? "text-primary-foreground/50 text-right" : "text-muted-foreground/50"
            }`}
          >
            {new Date(message.timestamp).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}
          </div>
        )}
      </div>

      {/* User avatar */}
      {isUser && (
        <div className="flex-shrink-0 w-7 h-7 rounded-full bg-primary/30 flex items-center justify-center mt-0.5">
          <User className="h-3.5 w-3.5 text-primary-foreground" />
        </div>
      )}
    </div>
  );
}
