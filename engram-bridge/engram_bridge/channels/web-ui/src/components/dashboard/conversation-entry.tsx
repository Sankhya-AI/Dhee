import { renderMarkdown } from "@/lib/render-markdown";
import {
  Bot,
  User,
  FileCode,
  Terminal,
  AlertCircle,
  Info,
  CheckCircle2,
  Loader2,
  ChevronDown,
  ChevronRight,
} from "lucide-react";
import { useState } from "react";
import type { ConversationEntry as ConversationEntryType } from "@/types/dashboard";

interface ConversationEntryProps {
  entry: ConversationEntryType;
}

function timeStr(iso: string): string {
  return new Date(iso).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

/** Streaming dots animation */
function StreamingDots() {
  return (
    <span className="inline-flex items-center gap-1 ml-1">
      <span className="thinking-dot inline-block w-1 h-1 rounded-full bg-accent" />
      <span className="thinking-dot inline-block w-1 h-1 rounded-full bg-accent" />
      <span className="thinking-dot inline-block w-1 h-1 rounded-full bg-accent" />
    </span>
  );
}

export function ConversationEntry({ entry }: ConversationEntryProps) {
  const [expanded, setExpanded] = useState(false);

  // ── User message ──
  if (entry.type === "user") {
    return (
      <div className="flex gap-2.5 py-2 px-3">
        <div className="flex-shrink-0 w-6 h-6 rounded-full bg-primary/30 flex items-center justify-center mt-0.5">
          <User className="h-3 w-3 text-primary-foreground" />
        </div>
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2 mb-0.5">
            <span className="text-xs font-semibold text-foreground">You</span>
            <span className="text-[10px] text-muted-foreground/60">{timeStr(entry.ts)}</span>
          </div>
          <div
            className="text-sm leading-relaxed text-foreground message-content"
            dangerouslySetInnerHTML={{ __html: renderMarkdown(entry.content) }}
          />
        </div>
      </div>
    );
  }

  // ── Assistant message ──
  if (entry.type === "assistant") {
    return (
      <div className="flex gap-2.5 py-2 px-3">
        <div className="flex-shrink-0 w-6 h-6 rounded-full bg-primary/15 flex items-center justify-center mt-0.5">
          <Bot className="h-3 w-3 text-primary" />
        </div>
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2 mb-0.5">
            <span className="text-xs font-semibold text-primary">{entry.agent || "Agent"}</span>
            <span className="text-[10px] text-muted-foreground/60">{timeStr(entry.ts)}</span>
            {entry.streaming && <StreamingDots />}
          </div>
          <div
            className="text-sm leading-relaxed text-foreground/90 message-content [&_pre]:my-2 [&_code]:break-all"
            dangerouslySetInnerHTML={{ __html: renderMarkdown(entry.content) }}
          />
        </div>
      </div>
    );
  }

  // ── Tool use ──
  if (entry.type === "tool_use") {
    const isFileOp = entry.tool && ["Read", "Edit", "Write", "Glob", "Grep"].includes(entry.tool);
    const hasOutput = entry.content && entry.content.length > 80;

    return (
      <div className="py-0.5 px-3 ml-8">
        <button
          className="flex items-center gap-1.5 text-xs group w-full text-left"
          onClick={() => hasOutput && setExpanded(!expanded)}
        >
          <span className="text-accent/60">
            {isFileOp ? (
              <FileCode className="h-3 w-3 inline" />
            ) : (
              <Terminal className="h-3 w-3 inline" />
            )}
          </span>
          <span className="font-mono text-accent/80">
            {entry.tool}
          </span>
          {entry.file_path && (
            <span className="text-muted-foreground/60 font-mono truncate">
              {entry.file_path}
            </span>
          )}
          {entry.streaming && (
            <Loader2 className="h-3 w-3 text-accent animate-spin" />
          )}
          {!entry.streaming && !hasOutput && (
            <CheckCircle2 className="h-3 w-3 text-emerald-500/60" />
          )}
          {hasOutput && (
            <span className="text-muted-foreground/40">
              {expanded ? <ChevronDown className="h-3 w-3" /> : <ChevronRight className="h-3 w-3" />}
            </span>
          )}
        </button>
        {expanded && entry.content && (
          <div className="mt-1 ml-4 rounded-md bg-muted border border-border/30 p-2 overflow-x-auto max-h-[300px] overflow-y-auto">
            <pre className="text-[11px] font-mono text-muted-foreground/80 whitespace-pre-wrap break-all">
              {entry.content}
            </pre>
          </div>
        )}
      </div>
    );
  }

  // ── Tool result ──
  if (entry.type === "tool_result") {
    return (
      <div className="py-0.5 px-3 ml-8">
        <button
          className="flex items-center gap-1.5 text-xs group w-full text-left"
          onClick={() => setExpanded(!expanded)}
        >
          <CheckCircle2 className="h-3 w-3 text-emerald-500/60" />
          <span className="font-mono text-muted-foreground/60">
            Result
          </span>
          {entry.content && (
            <span className="text-muted-foreground/40 truncate max-w-[300px]">
              {entry.content.slice(0, 60)}...
            </span>
          )}
          <span className="text-muted-foreground/40">
            {expanded ? <ChevronDown className="h-3 w-3" /> : <ChevronRight className="h-3 w-3" />}
          </span>
        </button>
        {expanded && entry.content && (
          <div className="mt-1 ml-4 rounded-md bg-muted border border-border/30 p-2 overflow-x-auto max-h-[300px] overflow-y-auto">
            <pre className="text-[11px] font-mono text-muted-foreground/80 whitespace-pre-wrap break-all">
              {entry.content}
            </pre>
          </div>
        )}
      </div>
    );
  }

  // ── Error ──
  if (entry.type === "error") {
    return (
      <div className="flex gap-2 py-1.5 px-3 ml-8">
        <AlertCircle className="h-3.5 w-3.5 text-red-400 mt-0.5 flex-shrink-0" />
        <div className="text-xs text-red-400/90 font-mono">{entry.content}</div>
      </div>
    );
  }

  // ── System ──
  return (
    <div className="flex justify-center py-2 px-3">
      <div className="flex items-center gap-1.5 text-[11px] text-muted-foreground/60 bg-muted/20 px-3 py-1 rounded-full">
        <Info className="h-3 w-3" />
        {entry.content}
      </div>
    </div>
  );
}
