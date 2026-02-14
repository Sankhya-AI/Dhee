import { useState, useRef, useCallback } from "react";
import {
  DragDropContext,
  Droppable,
  Draggable,
  type DropResult,
} from "@hello-pangea/dnd";
import {
  GripVertical,
  X,
  ArrowRightToLine,
  Trash2,
  ListChecks,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Checkbox } from "@/components/ui/checkbox";
import { PriorityIcon } from "@/components/primitives/PriorityIcon";
import { useTodoStore, type TodoDraft } from "@/stores/useTodoStore";
import { useProjectContext } from "@/contexts/ProjectContext";
import type { Priority } from "@/types";

const PRIORITY_CYCLE: Priority[] = ["medium", "high", "urgent", "low"];

function nextPriority(current: Priority): Priority {
  const idx = PRIORITY_CYCLE.indexOf(current);
  return PRIORITY_CYCLE[(idx + 1) % PRIORITY_CYCLE.length];
}

function DraftRow({ draft, index }: { draft: TodoDraft; index: number }) {
  const { updateDraftTitle, updateDraftPriority, removeDraft, toggleSelect } =
    useTodoStore();
  const { statuses, createIssue } = useProjectContext();
  const [editing, setEditing] = useState(false);
  const [editValue, setEditValue] = useState(draft.title);
  const [pushing, setPushing] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);

  const commitEdit = () => {
    const trimmed = editValue.trim();
    if (trimmed && trimmed !== draft.title) {
      updateDraftTitle(draft.id, trimmed);
    } else {
      setEditValue(draft.title);
    }
    setEditing(false);
  };

  const pushSingle = async () => {
    const inbox = [...statuses].sort((a, b) => a.sort_order - b.sort_order)[0];
    if (!inbox) return;
    setPushing(true);
    try {
      await createIssue({
        title: draft.title,
        priority: draft.priority,
        status_id: inbox.id,
      });
      removeDraft(draft.id);
    } catch {
      // keep draft on failure
    } finally {
      setPushing(false);
    }
  };

  return (
    <Draggable draggableId={draft.id} index={index}>
      {(provided, snapshot) => (
        <div
          ref={provided.innerRef}
          {...provided.draggableProps}
          className={`group flex items-center gap-2 px-3 py-2 rounded-lg border transition-colors ${
            snapshot.isDragging
              ? "bg-muted border-border shadow-md"
              : "bg-background border-transparent hover:border-border"
          }`}
        >
          {/* Drag handle */}
          <div
            {...provided.dragHandleProps}
            className="cursor-grab text-muted-foreground/40 hover:text-muted-foreground"
          >
            <GripVertical className="h-4 w-4" />
          </div>

          {/* Checkbox */}
          <Checkbox
            checked={draft.selected}
            onCheckedChange={() => toggleSelect(draft.id)}
          />

          {/* Title */}
          {editing ? (
            <Input
              ref={inputRef}
              value={editValue}
              onChange={(e) => setEditValue(e.target.value)}
              onBlur={commitEdit}
              onKeyDown={(e) => {
                if (e.key === "Enter") commitEdit();
                if (e.key === "Escape") {
                  setEditValue(draft.title);
                  setEditing(false);
                }
              }}
              className="h-7 flex-1 text-sm border-none shadow-none focus-visible:ring-1 px-1"
              autoFocus
            />
          ) : (
            <span
              className="flex-1 text-sm cursor-text truncate"
              onClick={() => {
                setEditing(true);
                setEditValue(draft.title);
              }}
            >
              {draft.title}
            </span>
          )}

          {/* Priority badge */}
          <button
            onClick={() => updateDraftPriority(draft.id, nextPriority(draft.priority))}
            className="flex items-center gap-1 px-1.5 py-0.5 rounded text-xs text-muted-foreground hover:bg-muted transition-colors"
            title={`Priority: ${draft.priority} (click to cycle)`}
          >
            <PriorityIcon priority={draft.priority} className="h-3 w-3" />
            <span className="hidden sm:inline">{draft.priority}</span>
          </button>

          {/* Push single to board */}
          <Button
            variant="ghost"
            size="icon"
            className="h-6 w-6 opacity-0 group-hover:opacity-100 transition-opacity text-muted-foreground hover:text-foreground"
            onClick={pushSingle}
            disabled={pushing}
            title="Push to Board"
          >
            <ArrowRightToLine className="h-3.5 w-3.5" />
          </Button>

          {/* Delete */}
          <Button
            variant="ghost"
            size="icon"
            className="h-6 w-6 opacity-0 group-hover:opacity-100 transition-opacity text-muted-foreground hover:text-destructive"
            onClick={() => removeDraft(draft.id)}
            title="Delete"
          >
            <X className="h-3.5 w-3.5" />
          </Button>
        </div>
      )}
    </Draggable>
  );
}

export function TodoView() {
  const { drafts, addDraft, reorder, selectAll, deselectAll, removeDrafts } =
    useTodoStore();
  const { statuses, createIssue } = useProjectContext();
  const [inputValue, setInputValue] = useState("");
  const [pushing, setPushing] = useState(false);

  const selected = drafts.filter((d) => d.selected);
  const allSelected = drafts.length > 0 && selected.length === drafts.length;

  const handleAdd = () => {
    const trimmed = inputValue.trim();
    if (!trimmed) return;
    addDraft(trimmed);
    setInputValue("");
  };

  const handleDragEnd = useCallback(
    (result: DropResult) => {
      if (!result.destination) return;
      reorder(result.source.index, result.destination.index);
    },
    [reorder],
  );

  const pushSelected = async () => {
    if (selected.length === 0) return;
    const inbox = [...statuses].sort((a, b) => a.sort_order - b.sort_order)[0];
    if (!inbox) return;

    setPushing(true);
    const results = await Promise.allSettled(
      selected.map((d) =>
        createIssue({
          title: d.title,
          priority: d.priority,
          status_id: inbox.id,
        }),
      ),
    );

    const pushedIds = selected
      .filter((_, i) => results[i].status === "fulfilled")
      .map((d) => d.id);

    if (pushedIds.length > 0) {
      removeDrafts(pushedIds);
    }
    setPushing(false);
  };

  const deleteSelected = () => {
    if (selected.length === 0) return;
    removeDrafts(selected.map((d) => d.id));
  };

  return (
    <div className="flex-1 overflow-y-auto">
      <div className="max-w-2xl mx-auto px-4 py-8">
        {/* Header */}
        <h2 className="text-xl font-semibold mb-6">Todos</h2>

        {/* Input */}
        <Input
          value={inputValue}
          onChange={(e) => setInputValue(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") handleAdd();
          }}
          placeholder="Type a todo, press Enter..."
          className="mb-4"
        />

        {/* Toolbar */}
        {drafts.length > 0 && (
          <div className="flex items-center gap-3 mb-3 text-sm">
            <label className="flex items-center gap-2 cursor-pointer text-muted-foreground hover:text-foreground">
              <Checkbox
                checked={allSelected}
                onCheckedChange={() =>
                  allSelected ? deselectAll() : selectAll()
                }
              />
              <span>Select all</span>
            </label>

            {selected.length > 0 && (
              <>
                <span className="text-muted-foreground">
                  {selected.length} selected
                </span>
                <Button
                  size="sm"
                  className="h-7 text-xs gap-1.5 engram-gradient-bg text-white border-none hover:opacity-90"
                  onClick={pushSelected}
                  disabled={pushing}
                >
                  <ArrowRightToLine className="h-3 w-3" />
                  Push to Board
                </Button>
                <Button
                  size="sm"
                  variant="ghost"
                  className="h-7 text-xs gap-1.5 text-muted-foreground hover:text-destructive"
                  onClick={deleteSelected}
                >
                  <Trash2 className="h-3 w-3" />
                </Button>
              </>
            )}
          </div>
        )}

        {/* Draft list */}
        {drafts.length > 0 ? (
          <DragDropContext onDragEnd={handleDragEnd}>
            <Droppable droppableId="todo-drafts">
              {(provided) => (
                <div
                  ref={provided.innerRef}
                  {...provided.droppableProps}
                  className="space-y-1"
                >
                  {drafts.map((draft, index) => (
                    <DraftRow key={draft.id} draft={draft} index={index} />
                  ))}
                  {provided.placeholder}
                </div>
              )}
            </Droppable>
          </DragDropContext>
        ) : (
          /* Empty state */
          <div className="flex flex-col items-center justify-center py-20 text-muted-foreground">
            <div className="engram-gradient-bg rounded-2xl p-3 mb-4 opacity-80">
              <ListChecks className="h-8 w-8 text-white" />
            </div>
            <p className="text-sm font-medium">What needs to be done?</p>
            <p className="text-xs mt-1">
              Type a todo above and press Enter to add it
            </p>
          </div>
        )}
      </div>
    </div>
  );
}
