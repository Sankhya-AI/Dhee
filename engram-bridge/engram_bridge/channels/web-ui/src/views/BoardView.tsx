import { useState } from "react";
import { KanbanContainer } from "@/components/kanban/KanbanContainer";
import { CreateProjectDialog } from "@/components/dialogs/CreateProjectDialog";
import { Sidebar } from "@/components/layout/Sidebar";
import { useUiPreferencesStore } from "@/stores/useUiPreferencesStore";

export function BoardView() {
  const [createProjectOpen, setCreateProjectOpen] = useState(false);
  const { sidebarOpen } = useUiPreferencesStore();

  return (
    <div className="flex flex-1 overflow-hidden">
      <Sidebar
        open={sidebarOpen}
        onCreateProject={() => setCreateProjectOpen(true)}
      />
      <main className="flex-1 overflow-hidden">
        <KanbanContainer />
      </main>
      <CreateProjectDialog
        open={createProjectOpen}
        onClose={() => setCreateProjectOpen(false)}
      />
    </div>
  );
}
