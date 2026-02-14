import { useCallback, useState } from "react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Plus } from "lucide-react";
import { TaskCard } from "@/components/dashboard/task-card";
import type { Task, TaskStatus } from "@/types/dashboard";
import { STATUS_COLUMNS } from "@/types/dashboard";

interface KanbanBoardProps {
  tasks: Task[];
  onMoveTask: (taskId: string, newStatus: TaskStatus) => void;
  onClickTask: (task: Task) => void;
  onNewTask: () => void;
}

export function KanbanBoard({ tasks, onMoveTask, onClickTask, onNewTask }: KanbanBoardProps) {
  const [dragOverColumn, setDragOverColumn] = useState<TaskStatus | null>(null);

  const handleDragStart = useCallback((e: React.DragEvent, taskId: string) => {
    e.dataTransfer.setData("text/plain", taskId);
    e.dataTransfer.effectAllowed = "move";
  }, []);

  const handleDragOver = useCallback((e: React.DragEvent, status: TaskStatus) => {
    e.preventDefault();
    e.dataTransfer.dropEffect = "move";
    setDragOverColumn(status);
  }, []);

  const handleDragLeave = useCallback(() => {
    setDragOverColumn(null);
  }, []);

  const handleDrop = useCallback(
    (e: React.DragEvent, status: TaskStatus) => {
      e.preventDefault();
      setDragOverColumn(null);
      const taskId = e.dataTransfer.getData("text/plain");
      if (taskId) {
        onMoveTask(taskId, status);
      }
    },
    [onMoveTask]
  );

  return (
    <div className="h-full flex flex-col">
      {/* Board header */}
      <div className="flex items-center justify-between px-4 py-3 border-b border-border">
        <div className="flex items-center gap-3">
          <h2 className="text-sm font-semibold uppercase tracking-wider text-muted-foreground">
            Mission Queue
          </h2>
          <Badge variant="outline" className="text-[10px]">
            {tasks.filter((t) => t.status !== "done").length} active
          </Badge>
        </div>
        <Button size="sm" className="h-7 text-xs gap-1" onClick={onNewTask}>
          <Plus className="h-3.5 w-3.5" />
          New
        </Button>
      </div>

      {/* Status filter pills */}
      <div className="flex items-center gap-1.5 px-4 py-2 border-b border-border overflow-x-auto">
        {STATUS_COLUMNS.map((col) => {
          const count = tasks.filter((t) => t.status === col.key).length;
          return (
            <Badge
              key={col.key}
              variant="outline"
              className="text-[11px] px-2 py-0.5 shrink-0 cursor-default"
            >
              {col.label} {count}
            </Badge>
          );
        })}
      </div>

      {/* Columns */}
      <div className="flex-1 overflow-x-auto">
        <div className="flex h-full min-w-max">
          {STATUS_COLUMNS.map((col) => {
            const columnTasks = tasks.filter((t) => t.status === col.key);
            const isDragOver = dragOverColumn === col.key;

            return (
              <div
                key={col.key}
                className={`flex-1 min-w-[260px] max-w-[320px] border-r border-border last:border-r-0 flex flex-col transition-colors ${
                  isDragOver ? "bg-primary/5" : ""
                }`}
                onDragOver={(e) => handleDragOver(e, col.key)}
                onDragLeave={handleDragLeave}
                onDrop={(e) => handleDrop(e, col.key)}
              >
                {/* Column header */}
                <div className="flex items-center justify-between px-3 py-2 border-b border-border/50">
                  <div className="flex items-center gap-2">
                    <div
                      className={`w-2 h-2 rounded-full ${
                        col.key === "done"
                          ? "bg-emerald-500"
                          : col.key === "active"
                            ? "bg-blue-500"
                            : col.key === "blocked"
                              ? "bg-red-500"
                              : "bg-muted-foreground/40"
                      }`}
                    />
                    <span className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">
                      {col.label}
                    </span>
                  </div>
                  <span className="text-xs text-muted-foreground tabular-nums">
                    {columnTasks.length}
                  </span>
                </div>

                {/* Task cards */}
                <div className="flex-1 overflow-y-auto p-2 space-y-2">
                  {columnTasks.map((task) => (
                    <TaskCard
                      key={task.id}
                      task={task}
                      onDragStart={handleDragStart}
                      onClick={onClickTask}
                    />
                  ))}
                  {columnTasks.length === 0 && (
                    <div className="text-center text-xs text-muted-foreground/40 py-8">
                      Drop tasks here
                    </div>
                  )}
                </div>
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
}
