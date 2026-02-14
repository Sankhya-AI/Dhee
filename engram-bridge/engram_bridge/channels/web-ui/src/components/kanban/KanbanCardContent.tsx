import { MessageSquare, Link2, Loader2, GitBranch } from "lucide-react";
import { PriorityIcon } from "@/components/primitives/PriorityIcon";
import { UserAvatar } from "@/components/primitives/UserAvatar";
import { useProjectContext } from "@/contexts/ProjectContext";
import type { Issue, Priority } from "@/types";

interface Props {
  issue: Issue;
  onClick: () => void;
  onDoubleClick?: () => void;
}

export function KanbanCardContent({ issue, onClick, onDoubleClick }: Props) {
  const { tags } = useProjectContext();
  const issueTags = tags.filter(t => issue.tag_ids.includes(t.id));

  const isExecuting = issue.conversation?.some(
    (e) => e.type === "assistant" && e.streaming,
  );
  const hasExecution = (issue.conversation?.length ?? 0) > 0;

  return (
    <div
      onClick={onClick}
      onDoubleClick={onDoubleClick}
      className="group bg-card border border-border/60 rounded-lg p-3 cursor-pointer hover:border-primary/40 hover:shadow-md transition-all"
    >
      {/* Tags row */}
      {issueTags.length > 0 && (
        <div className="flex flex-wrap gap-1 mb-2">
          {issueTags.map(tag => (
            <span
              key={tag.id}
              className="text-[10px] px-1.5 py-0.5 rounded-full font-medium"
              style={{
                backgroundColor: `${tag.color}20`,
                color: tag.color,
                border: `1px solid ${tag.color}30`,
              }}
            >
              {tag.name}
            </span>
          ))}
        </div>
      )}

      {/* Title */}
      <div className="flex items-start gap-2">
        <PriorityIcon priority={(issue.priority || "medium") as Priority} className="h-3.5 w-3.5 mt-0.5 flex-shrink-0" />
        <p className="text-sm font-medium leading-snug line-clamp-2 flex-1">
          {issue.title}
        </p>
        {isExecuting && (
          <Loader2 className="h-3.5 w-3.5 animate-spin text-blue-500 flex-shrink-0 mt-0.5" />
        )}
      </div>

      {/* Footer */}
      <div className="flex items-center justify-between mt-2.5 pt-2 border-t border-border/30">
        <div className="flex items-center gap-2 text-muted-foreground">
          {issue.issue_number > 0 && (
            <span className="text-[10px] font-mono opacity-60">#{issue.issue_number}</span>
          )}
          {issue.comments.length > 0 && (
            <span className="flex items-center gap-0.5 text-[10px]">
              <MessageSquare className="h-3 w-3" />
              {issue.comments.length}
            </span>
          )}
          {issue.relationships.length > 0 && (
            <span className="flex items-center gap-0.5 text-[10px]">
              <Link2 className="h-3 w-3" />
              {issue.relationships.length}
            </span>
          )}
          {issue.parent_task_id && (
            <span className="flex items-center gap-0.5 text-[10px]">
              <GitBranch className="h-3 w-3" />
            </span>
          )}
        </div>

        {/* Assignee avatars */}
        {issue.assignee_ids.length > 0 && (
          <div className="flex -space-x-1">
            {issue.assignee_ids.slice(0, 3).map(id => (
              <UserAvatar key={id} name={id} size="xs" />
            ))}
            {issue.assignee_ids.length > 3 && (
              <span className="text-[9px] text-muted-foreground ml-1">+{issue.assignee_ids.length - 3}</span>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
