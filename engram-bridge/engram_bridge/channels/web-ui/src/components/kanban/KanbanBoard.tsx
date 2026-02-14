import { useCallback } from "react";
import {
  DragDropContext,
  Droppable,
  Draggable,
  type DropResult,
} from "@hello-pangea/dnd";
import { Plus } from "lucide-react";
import { useProjectContext } from "@/contexts/ProjectContext";
import { KanbanCardContent } from "./KanbanCardContent";
import { StatusDot } from "@/components/primitives/StatusDot";
import type { Issue, ProjectStatus } from "@/types";

interface Props {
  issues: Issue[];
  onCardClick: (issue: Issue) => void;
  onCardDoubleClick?: (issue: Issue) => void;
  onCreateInColumn: (statusId: string) => void;
}

export function KanbanBoard({ issues, onCardClick, onCardDoubleClick, onCreateInColumn }: Props) {
  const { statuses, updateIssue, bulkUpdateIssues } = useProjectContext();

  const visibleStatuses = statuses.filter(s => !s.hidden);

  const issuesByStatus = new Map<string, Issue[]>();
  for (const s of visibleStatuses) {
    issuesByStatus.set(s.id, []);
  }
  for (const issue of issues) {
    const col = issuesByStatus.get(issue.status_id || "");
    if (col) {
      col.push(issue);
    } else {
      // Unmatched: put in first column
      const first = visibleStatuses[0];
      if (first) issuesByStatus.get(first.id)?.push(issue);
    }
  }

  // Sort each column by sort_order
  for (const arr of issuesByStatus.values()) {
    arr.sort((a, b) => a.sort_order - b.sort_order);
  }

  const handleDragEnd = useCallback(async (result: DropResult) => {
    const { source, destination, draggableId } = result;
    if (!destination) return;
    if (source.droppableId === destination.droppableId && source.index === destination.index) return;

    const sourceCol = issuesByStatus.get(source.droppableId) || [];
    const destCol = source.droppableId === destination.droppableId
      ? sourceCol
      : (issuesByStatus.get(destination.droppableId) || []);

    // Remove from source
    const [moved] = sourceCol.splice(source.index, 1);
    if (!moved) return;

    // Insert at destination
    destCol.splice(destination.index, 0, moved);

    // Build updates
    const updates: Partial<Issue>[] = [];

    // If column changed, update status_id
    if (source.droppableId !== destination.droppableId) {
      updates.push({
        id: moved.id,
        status_id: destination.droppableId,
        sort_order: destination.index,
      });
    }

    // Update sort_order for all items in destination column
    destCol.forEach((issue, idx) => {
      if (issue.sort_order !== idx) {
        const existing = updates.find(u => u.id === issue.id);
        if (existing) {
          existing.sort_order = idx;
        } else {
          updates.push({ id: issue.id, sort_order: idx });
        }
      }
    });

    // If moved from different column, also reindex source
    if (source.droppableId !== destination.droppableId) {
      sourceCol.forEach((issue, idx) => {
        if (issue.sort_order !== idx) {
          updates.push({ id: issue.id, sort_order: idx });
        }
      });
    }

    if (updates.length > 0) {
      // Optimistic: update first item immediately for responsiveness
      const first = updates[0];
      if (first.id && source.droppableId !== destination.droppableId) {
        await updateIssue(first.id, {
          status_id: destination.droppableId,
          sort_order: destination.index,
        });
      }
      // Bulk the rest
      if (updates.length > 1) {
        await bulkUpdateIssues(updates.slice(1));
      }
    }
  }, [issuesByStatus, updateIssue, bulkUpdateIssues]);

  return (
    <DragDropContext onDragEnd={handleDragEnd}>
      <div className="flex gap-4 h-full overflow-x-auto p-4">
        {visibleStatuses.map(status => (
          <Column
            key={status.id}
            status={status}
            issues={issuesByStatus.get(status.id) || []}
            onCardClick={onCardClick}
            onCardDoubleClick={onCardDoubleClick}
            onCreateClick={() => onCreateInColumn(status.id)}
          />
        ))}
      </div>
    </DragDropContext>
  );
}

function Column({
  status,
  issues,
  onCardClick,
  onCardDoubleClick,
  onCreateClick,
}: {
  status: ProjectStatus;
  issues: Issue[];
  onCardClick: (issue: Issue) => void;
  onCardDoubleClick?: (issue: Issue) => void;
  onCreateClick: () => void;
}) {
  return (
    <div className="flex flex-col w-72 min-w-[288px] flex-shrink-0">
      {/* Column header */}
      <div className="flex items-center justify-between mb-3 px-1">
        <div className="flex items-center gap-2">
          <StatusDot color={status.color} />
          <span className="text-sm font-medium">{status.name}</span>
          <span className="text-xs text-muted-foreground bg-muted/50 px-1.5 py-0.5 rounded-full">
            {issues.length}
          </span>
        </div>
        <button
          onClick={onCreateClick}
          className="p-1 rounded hover:bg-muted/50 text-muted-foreground hover:text-foreground transition-colors"
        >
          <Plus className="h-4 w-4" />
        </button>
      </div>

      {/* Droppable area */}
      <Droppable droppableId={status.id}>
        {(provided, snapshot) => (
          <div
            ref={provided.innerRef}
            {...provided.droppableProps}
            className={`flex-1 space-y-2 p-1 rounded-lg min-h-[100px] transition-colors ${
              snapshot.isDraggingOver ? "bg-primary/5" : ""
            }`}
          >
            {issues.map((issue, index) => (
              <Draggable key={issue.id} draggableId={issue.id} index={index}>
                {(provided, snapshot) => (
                  <div
                    ref={provided.innerRef}
                    {...provided.draggableProps}
                    {...provided.dragHandleProps}
                    className={snapshot.isDragging ? "opacity-90 rotate-1" : ""}
                  >
                    <KanbanCardContent
                      issue={issue}
                      onClick={() => onCardClick(issue)}
                      onDoubleClick={() => onCardDoubleClick?.(issue)}
                    />
                  </div>
                )}
              </Draggable>
            ))}
            {provided.placeholder}
          </div>
        )}
      </Droppable>
    </div>
  );
}
