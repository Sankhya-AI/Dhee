import { Badge } from "@/components/ui/badge";
import {
  FileCode,
  FilePlus,
  FileX,
  FileEdit,
  ChevronDown,
  ChevronRight,
  Plus,
  Minus,
} from "lucide-react";
import { useState } from "react";
import type { FileChange } from "@/types/dashboard";

interface FilesPanelProps {
  files: FileChange[];
}

function FileRow({ file }: { file: FileChange }) {
  const [expanded, setExpanded] = useState(false);
  const hasDiff = file.diff && file.diff.length > 0;

  const actionIcon =
    file.action === "created" ? (
      <FilePlus className="h-3.5 w-3.5 text-emerald-400" />
    ) : file.action === "deleted" ? (
      <FileX className="h-3.5 w-3.5 text-red-400" />
    ) : (
      <FileEdit className="h-3.5 w-3.5 text-blue-400" />
    );

  const actionColor =
    file.action === "created"
      ? "bg-emerald-500/15 text-emerald-400 border-emerald-500/30"
      : file.action === "deleted"
        ? "bg-red-500/15 text-red-400 border-red-500/30"
        : "bg-blue-500/15 text-blue-400 border-blue-500/30";

  const fileName = file.path.split("/").pop() || file.path;
  const dirPath = file.path.split("/").slice(0, -1).join("/");

  return (
    <div className="border-b border-border/30 last:border-b-0">
      <button
        className="flex items-center gap-3 w-full px-4 py-2.5 hover:bg-secondary/20 transition-colors text-left"
        onClick={() => hasDiff && setExpanded(!expanded)}
      >
        {actionIcon}
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2">
            <span className="text-sm font-mono font-medium text-foreground">{fileName}</span>
            <Badge variant="outline" className={`text-[10px] px-1.5 py-0 capitalize ${actionColor}`}>
              {file.action}
            </Badge>
          </div>
          {dirPath && (
            <span className="text-[11px] text-muted-foreground/50 font-mono">{dirPath}/</span>
          )}
        </div>
        <div className="flex items-center gap-2 text-[11px]">
          {file.additions !== undefined && file.additions > 0 && (
            <span className="flex items-center gap-0.5 text-emerald-400">
              <Plus className="h-3 w-3" />
              {file.additions}
            </span>
          )}
          {file.deletions !== undefined && file.deletions > 0 && (
            <span className="flex items-center gap-0.5 text-red-400">
              <Minus className="h-3 w-3" />
              {file.deletions}
            </span>
          )}
          {hasDiff && (
            <span className="text-muted-foreground/40">
              {expanded ? <ChevronDown className="h-4 w-4" /> : <ChevronRight className="h-4 w-4" />}
            </span>
          )}
        </div>
      </button>
      {expanded && file.diff && (
        <div className="mx-4 mb-3 rounded-md border border-border/30 overflow-x-auto max-h-[400px] overflow-y-auto">
          <pre className="text-[11px] font-mono leading-relaxed">
            {file.diff.split("\n").map((line, i) => {
              let lineClass = "px-3 py-0 text-muted-foreground/70";
              if (line.startsWith("+") && !line.startsWith("+++")) {
                lineClass = "px-3 py-0 bg-emerald-500/10 text-emerald-400/90";
              } else if (line.startsWith("-") && !line.startsWith("---")) {
                lineClass = "px-3 py-0 bg-red-500/10 text-red-400/90";
              } else if (line.startsWith("@@")) {
                lineClass = "px-3 py-0 bg-blue-500/10 text-blue-400/70";
              }
              return (
                <div key={i} className={lineClass}>
                  {line}
                </div>
              );
            })}
          </pre>
        </div>
      )}
    </div>
  );
}

export function FilesPanel({ files }: FilesPanelProps) {
  const created = files.filter((f) => f.action === "created").length;
  const modified = files.filter((f) => f.action === "modified").length;
  const deleted = files.filter((f) => f.action === "deleted").length;
  const totalAdds = files.reduce((s, f) => s + (f.additions || 0), 0);
  const totalDels = files.reduce((s, f) => s + (f.deletions || 0), 0);

  return (
    <div className="h-full flex flex-col">
      {/* Header */}
      <div className="flex items-center justify-between px-4 py-3 border-b border-border">
        <div className="flex items-center gap-2">
          <FileCode className="h-4 w-4 text-muted-foreground" />
          <h3 className="text-sm font-semibold text-muted-foreground">Files Changed</h3>
        </div>
        <div className="flex items-center gap-2 text-[11px]">
          {created > 0 && <span className="text-emerald-400">{created} new</span>}
          {modified > 0 && <span className="text-blue-400">{modified} mod</span>}
          {deleted > 0 && <span className="text-red-400">{deleted} del</span>}
          {(totalAdds > 0 || totalDels > 0) && (
            <Badge variant="outline" className="text-[10px]">
              <Plus className="h-2.5 w-2.5 text-emerald-400 mr-0.5" />
              {totalAdds}
              <Minus className="h-2.5 w-2.5 text-red-400 ml-1 mr-0.5" />
              {totalDels}
            </Badge>
          )}
        </div>
      </div>

      {/* File list */}
      <div className="flex-1 overflow-y-auto">
        {files.length === 0 ? (
          <div className="flex flex-col items-center justify-center h-full text-muted-foreground/40 py-12">
            <FileCode className="h-8 w-8 mb-2" />
            <p className="text-sm">No file changes yet</p>
            <p className="text-xs">File diffs will appear as the agent makes changes</p>
          </div>
        ) : (
          files.map((f, i) => <FileRow key={`${f.path}-${i}`} file={f} />)
        )}
      </div>
    </div>
  );
}
