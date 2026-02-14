import { Badge } from "@/components/ui/badge";
import { MessageSquare, GripVertical } from "lucide-react";
import type { Task } from "@/types/dashboard";
import { PRIORITY_COLORS } from "@/types/dashboard";

interface TaskCardProps {
  task: Task;
  onDragStart: (e: React.DragEvent, taskId: string) => void;
  onClick: (task: Task) => void;
}

function timeAgo(iso: string): string {
  const diff = Date.now() - new Date(iso).getTime();
  const mins = Math.floor(diff / 60000);
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins}m ago`;
  const hrs = Math.floor(mins / 60);
  if (hrs < 24) return `${hrs}h ago`;
  return `${Math.floor(hrs / 24)}d ago`;
}

export function TaskCard({ task, onDragStart, onClick }: TaskCardProps) {
  const priorityClass = PRIORITY_COLORS[task.priority] || PRIORITY_COLORS.normal;

  return (
    <div
      draggable
      onDragStart={(e) => onDragStart(e, task.id)}
      onClick={() => onClick(task)}
      className="group bg-card border border-border rounded-lg p-3 cursor-grab active:cursor-grabbing hover:border-primary/30 transition-colors"
    >
      {/* Priority + tags row */}
      <div className="flex items-center gap-1.5 mb-2 flex-wrap">
        <Badge variant="outline" className={`text-[10px] px-1.5 py-0 uppercase ${priorityClass}`}>
          {task.priority}
        </Badge>
        {task.tags.slice(0, 3).map((tag) => (
          <Badge key={tag} variant="outline" className="text-[10px] px-1.5 py-0 text-muted-foreground">
            {tag}
          </Badge>
        ))}
      </div>

      {/* Title */}
      <h4 className="text-sm font-medium leading-snug mb-1.5 line-clamp-2">
        {task.title}
      </h4>

      {/* Description preview */}
      {task.description && (
        <p className="text-xs text-muted-foreground line-clamp-2 mb-2">
          {task.description}
        </p>
      )}

      {/* Footer: assigned agent, comments, time */}
      <div className="flex items-center justify-between text-[11px] text-muted-foreground">
        <div className="flex items-center gap-2">
          {task.assigned_agent && (
            <span className="flex items-center gap-1">
              <span className="w-4 h-4 rounded-full bg-primary/20 flex items-center justify-center text-[9px] font-bold text-primary">
                {task.assigned_agent[0].toUpperCase()}
              </span>
              {task.assigned_agent}
            </span>
          )}
          {task.comments.length > 0 && (
            <span className="flex items-center gap-0.5">
              <MessageSquare className="h-3 w-3" />
              {task.comments.length}
            </span>
          )}
        </div>
        <span>{timeAgo(task.created_at)}</span>
      </div>

      {/* Drag handle (visible on hover) */}
      <div className="absolute top-2 right-2 opacity-0 group-hover:opacity-40 transition-opacity">
        <GripVertical className="h-4 w-4" />
      </div>
    </div>
  );
}
