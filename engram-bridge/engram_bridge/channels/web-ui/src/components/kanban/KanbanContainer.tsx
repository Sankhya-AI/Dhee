import { useState, useMemo, useCallback } from "react";
import { useNavigate } from "react-router-dom";
import { useProjectContext } from "@/contexts/ProjectContext";
import { useUiPreferencesStore } from "@/stores/useUiPreferencesStore";
import { KanbanBoard } from "./KanbanBoard";
import { KanbanFilterBar } from "./KanbanFilterBar";
import { IssuePanel } from "@/components/issue/IssuePanel";
import { ResizablePanel } from "@/components/layout/ResizablePanel";
import { Button } from "@/components/ui/button";
import { MessageSquare } from "lucide-react";
import type { Issue, Priority } from "@/types";

const PRIORITY_ORDER: Record<string, number> = { urgent: 0, high: 1, medium: 2, normal: 2, low: 3 };

export function KanbanContainer() {
  const navigate = useNavigate();
  const { issues, statuses, loading } = useProjectContext();
  const { filters, sort } = useUiPreferencesStore();
  const [selectedIssue, setSelectedIssue] = useState<Issue | null>(null);
  const [createInStatusId, setCreateInStatusId] = useState<string | null>(null);
  const [showCreate, setShowCreate] = useState(false);

  // Filter issues
  const filteredIssues = useMemo(() => {
    let result = [...issues];

    // Search
    if (filters.search) {
      const q = filters.search.toLowerCase();
      result = result.filter(i =>
        i.title.toLowerCase().includes(q) ||
        i.description.toLowerCase().includes(q) ||
        String(i.issue_number).includes(q)
      );
    }

    // Priority filter
    if (filters.priorities.length > 0) {
      result = result.filter(i => filters.priorities.includes(i.priority as Priority));
    }

    // Assignee filter
    if (filters.assignees.length > 0) {
      result = result.filter(i =>
        i.assignee_ids.some(a => filters.assignees.includes(a))
      );
    }

    // Tag filter
    if (filters.tagIds.length > 0) {
      result = result.filter(i =>
        i.tag_ids.some(t => filters.tagIds.includes(t))
      );
    }

    // Sort (within columns, the board handles column assignment)
    if (sort.field !== "sort_order") {
      result.sort((a, b) => {
        let cmp = 0;
        switch (sort.field) {
          case "priority":
            cmp = (PRIORITY_ORDER[a.priority] ?? 2) - (PRIORITY_ORDER[b.priority] ?? 2);
            break;
          case "created_at":
            cmp = a.created_at.localeCompare(b.created_at);
            break;
          case "updated_at":
            cmp = a.updated_at.localeCompare(b.updated_at);
            break;
          case "title":
            cmp = a.title.localeCompare(b.title);
            break;
        }
        return sort.direction === "desc" ? -cmp : cmp;
      });
    }

    return result;
  }, [issues, filters, sort]);

  const handleCardClick = useCallback((issue: Issue) => {
    setSelectedIssue(issue);
  }, []);

  const handleCardDoubleClick = useCallback((issue: Issue) => {
    navigate(`/task/${issue.id}`);
  }, [navigate]);

  const handleCreateInColumn = useCallback((statusId: string) => {
    setCreateInStatusId(statusId);
    setShowCreate(true);
  }, []);

  const handleCreateClick = useCallback(() => {
    setCreateInStatusId(statuses[0]?.id || null);
    setShowCreate(true);
  }, [statuses]);

  if (loading) {
    return (
      <div className="flex items-center justify-center h-full">
        <div className="text-muted-foreground text-sm">Loading project...</div>
      </div>
    );
  }

  if (statuses.length === 0) {
    return (
      <div className="flex items-center justify-center h-full">
        <div className="text-muted-foreground text-sm">No statuses configured. Create a project first.</div>
      </div>
    );
  }

  const showPanel = selectedIssue || showCreate;

  return (
    <div className="flex h-full">
      {/* Board area */}
      <div className="flex-1 flex flex-col overflow-hidden">
        <KanbanFilterBar
          onCreateClick={handleCreateClick}
          totalCount={filteredIssues.length}
        />
        <div className="flex-1 overflow-hidden">
          <KanbanBoard
            issues={filteredIssues}
            onCardClick={handleCardClick}
            onCardDoubleClick={handleCardDoubleClick}
            onCreateInColumn={handleCreateInColumn}
          />
        </div>
      </div>

      {/* Issue detail panel (resizable right split) */}
      {selectedIssue && !showCreate && (
        <ResizablePanel defaultWidth={480} minWidth={360} maxWidth={640}>
          <div className="h-full border-l border-border overflow-y-auto bg-background">
            <div className="px-4 pt-3 pb-1">
              <Button
                variant="outline"
                size="sm"
                className="w-full gap-2 text-xs"
                onClick={() => navigate(`/task/${selectedIssue.id}`)}
              >
                <MessageSquare className="h-3.5 w-3.5" />
                Open Chat
              </Button>
            </div>
            <IssuePanel
              issue={selectedIssue}
              onClose={() => setSelectedIssue(null)}
              onIssueChange={(updated) => setSelectedIssue(updated)}
            />
          </div>
        </ResizablePanel>
      )}

      {/* Create issue panel */}
      {showCreate && (
        <ResizablePanel defaultWidth={480} minWidth={360} maxWidth={640}>
          <div className="h-full border-l border-border overflow-y-auto bg-background">
            <IssuePanel
              createMode
              defaultStatusId={createInStatusId}
              onClose={() => { setShowCreate(false); setCreateInStatusId(null); }}
            />
          </div>
        </ResizablePanel>
      )}
    </div>
  );
}
