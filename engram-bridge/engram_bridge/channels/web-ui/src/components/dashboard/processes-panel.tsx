import { Badge } from "@/components/ui/badge";
import {
  CheckCircle2,
  XCircle,
  Loader2,
  Clock,
  ChevronDown,
  ChevronRight,
  Terminal,
} from "lucide-react";
import { useState } from "react";
import type { ProcessEntry } from "@/types/dashboard";

interface ProcessesPanelProps {
  processes: ProcessEntry[];
}

function formatDuration(ms: number): string {
  if (ms < 1000) return `${ms}ms`;
  if (ms < 60000) return `${(ms / 1000).toFixed(1)}s`;
  return `${(ms / 60000).toFixed(1)}m`;
}

function timeStr(iso: string): string {
  return new Date(iso).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

function ProcessRow({ process }: { process: ProcessEntry }) {
  const [expanded, setExpanded] = useState(false);
  const hasOutput = process.output && process.output.length > 0;

  const statusIcon =
    process.status === "running" ? (
      <Loader2 className="h-3.5 w-3.5 text-blue-400 animate-spin" />
    ) : process.status === "completed" ? (
      <CheckCircle2 className="h-3.5 w-3.5 text-emerald-400" />
    ) : (
      <XCircle className="h-3.5 w-3.5 text-red-400" />
    );

  const statusColor =
    process.status === "running"
      ? "bg-blue-500/15 text-blue-400 border-blue-500/30"
      : process.status === "completed"
        ? "bg-emerald-500/15 text-emerald-400 border-emerald-500/30"
        : "bg-red-500/15 text-red-400 border-red-500/30";

  return (
    <div className="border-b border-border/30 last:border-b-0">
      <button
        className="flex items-center gap-3 w-full px-4 py-3 hover:bg-secondary/20 transition-colors text-left"
        onClick={() => hasOutput && setExpanded(!expanded)}
      >
        {statusIcon}
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2">
            <span className="text-sm font-medium text-foreground truncate">{process.name}</span>
            <Badge variant="outline" className={`text-[10px] px-1.5 py-0 ${statusColor}`}>
              {process.status}
            </Badge>
          </div>
          <div className="flex items-center gap-3 mt-0.5 text-[11px] text-muted-foreground/60">
            <span className="flex items-center gap-1">
              <Clock className="h-3 w-3" />
              {timeStr(process.started_at)}
            </span>
            {process.duration_ms !== undefined && (
              <span>{formatDuration(process.duration_ms)}</span>
            )}
            {process.agent && <span>{process.agent}</span>}
            {process.exit_code !== undefined && process.exit_code !== 0 && (
              <span className="text-red-400">exit {process.exit_code}</span>
            )}
          </div>
        </div>
        {hasOutput && (
          <span className="text-muted-foreground/40">
            {expanded ? <ChevronDown className="h-4 w-4" /> : <ChevronRight className="h-4 w-4" />}
          </span>
        )}
      </button>
      {expanded && process.output && (
        <div className="mx-4 mb-3 rounded-md bg-muted border border-border/30 p-3 overflow-x-auto max-h-[400px] overflow-y-auto">
          <pre className="text-[11px] font-mono text-muted-foreground/80 whitespace-pre-wrap break-all leading-relaxed">
            {process.output}
          </pre>
        </div>
      )}
    </div>
  );
}

export function ProcessesPanel({ processes }: ProcessesPanelProps) {
  const running = processes.filter((p) => p.status === "running");
  const completed = processes.filter((p) => p.status !== "running");

  return (
    <div className="h-full flex flex-col">
      {/* Header */}
      <div className="flex items-center justify-between px-4 py-3 border-b border-border">
        <div className="flex items-center gap-2">
          <Terminal className="h-4 w-4 text-muted-foreground" />
          <h3 className="text-sm font-semibold text-muted-foreground">Processes</h3>
        </div>
        <div className="flex items-center gap-2">
          {running.length > 0 && (
            <Badge variant="outline" className="text-[10px] bg-blue-500/15 text-blue-400 border-blue-500/30">
              {running.length} running
            </Badge>
          )}
          <Badge variant="outline" className="text-[10px]">
            {processes.length} total
          </Badge>
        </div>
      </div>

      {/* Process list */}
      <div className="flex-1 overflow-y-auto">
        {processes.length === 0 ? (
          <div className="flex flex-col items-center justify-center h-full text-muted-foreground/40 py-12">
            <Terminal className="h-8 w-8 mb-2" />
            <p className="text-sm">No processes yet</p>
            <p className="text-xs">Processes will appear when the agent starts working</p>
          </div>
        ) : (
          <>
            {running.map((p) => (
              <ProcessRow key={p.id} process={p} />
            ))}
            {completed.map((p) => (
              <ProcessRow key={p.id} process={p} />
            ))}
          </>
        )}
      </div>
    </div>
  );
}
