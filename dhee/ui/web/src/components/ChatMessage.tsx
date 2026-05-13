import type { SankhyaTask, TaskMessage } from "../types";
import {
  BrowserCard,
  CodeCard,
  DocumentCard,
  GrepCard,
  LinkCard,
} from "./cards/Cards";

export function ChatMessage({
  msg,
  tasks,
  onSelectTask,
}: {
  msg: TaskMessage;
  tasks: SankhyaTask[];
  onSelectTask: (id: string) => void;
}) {
  if (msg.role === "component") {
    const anyMsg = msg as any;
    return (
      <div style={{ marginBottom: 14, paddingLeft: 14 }}>
        {anyMsg.type === "browser" && (
          <BrowserCard url={anyMsg.url} title={anyMsg.title} lines={anyMsg.lines} />
        )}
        {anyMsg.type === "grep" && (
          <GrepCard query={anyMsg.query} files={anyMsg.files} />
        )}
        {anyMsg.type === "code" && (
          <CodeCard lang={anyMsg.lang} lines={anyMsg.lines} />
        )}
        {anyMsg.type === "document" && (
          <DocumentCard title={anyMsg.title} lines={anyMsg.lines} />
        )}
        {anyMsg.type === "link" && (
          <LinkCard
            linkedTask={anyMsg.linkedTask}
            preview={anyMsg.preview}
            tasks={tasks}
            onSelectTask={onSelectTask}
          />
        )}
      </div>
    );
  }
  const isUser = msg.role === "user";
  return (
    <div
      style={{
        marginBottom: 13,
        display: "flex",
        flexDirection: "column",
        alignItems: isUser ? "flex-end" : "flex-start",
      }}
    >
      {!isUser && (
        <div
          style={{
            display: "flex",
            alignItems: "center",
            gap: 5,
            marginBottom: 4,
          }}
        >
          <div
            style={{
              width: 5,
              height: 5,
              background: "var(--green)",
              borderRadius: "50%",
            }}
          />
          <span
            style={{
              fontFamily: "var(--mono)",
              fontSize: 9,
              color: "var(--ink3)",
              letterSpacing: 1,
            }}
          >
            AGENT
          </span>
        </div>
      )}
      <div
        style={{
          maxWidth: "88%",
          padding: "9px 13px",
          background: isUser ? "var(--ink)" : "white",
          color: isUser ? "var(--bg)" : "var(--ink)",
          border: isUser ? "none" : "1px solid var(--border)",
          fontSize: 13.5,
          lineHeight: 1.6,
          whiteSpace: "pre-wrap",
        }}
      >
        {msg.content}
      </div>
    </div>
  );
}
