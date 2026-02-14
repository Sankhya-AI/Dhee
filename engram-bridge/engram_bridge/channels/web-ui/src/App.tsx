import { BrowserRouter, Routes, Route } from "react-router-dom";
import { useHotkeys } from "react-hotkeys-hook";
import { useState } from "react";
import { TooltipProvider } from "@/components/ui/tooltip";
import { ProjectProvider } from "@/contexts/ProjectContext";
import { WebSocketProvider } from "@/contexts/WebSocketContext";
import { AppBar } from "@/components/layout/AppBar";
import { ChatView } from "@/views/ChatView";
import { BoardView } from "@/views/BoardView";
import { TaskChatView } from "@/views/TaskChatView";
import { MemoryView } from "@/views/MemoryView";
import { TodoView } from "@/views/TodoView";
import { CoordinationView } from "@/views/CoordinationView";
import { CommandBar } from "@/components/dialogs/CommandBar";
import { SettingsDialog } from "@/components/dialogs/SettingsDialog";
import { IssuePanel } from "@/components/issue/IssuePanel";
import type { Issue } from "@/types";

function AppInner() {
  const [commandBarOpen, setCommandBarOpen] = useState(false);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [commandIssue, setCommandIssue] = useState<Issue | null>(null);

  // Keyboard shortcuts
  useHotkeys("mod+k", (e) => { e.preventDefault(); setCommandBarOpen(true); }, { enableOnFormTags: true });

  return (
    <TooltipProvider>
      <div className="flex flex-col h-screen bg-background">
        <AppBar onOpenSettings={() => setSettingsOpen(true)} />

        <Routes>
          <Route path="/" element={<ChatView />} />
          <Route path="/todos" element={<TodoView />} />
          <Route path="/board" element={<BoardView />} />
          <Route path="/task/:taskId" element={<TaskChatView />} />
          <Route path="/memory" element={<MemoryView />} />
          <Route path="/coordination" element={<CoordinationView />} />
        </Routes>

        {/* Command palette */}
        <CommandBar
          open={commandBarOpen}
          onClose={() => setCommandBarOpen(false)}
          onSelectIssue={(issue) => setCommandIssue(issue)}
          onCreateIssue={() => setCommandBarOpen(false)}
        />

        {/* Settings dialog */}
        <SettingsDialog
          open={settingsOpen}
          onClose={() => setSettingsOpen(false)}
        />

        {/* Issue from command bar */}
        {commandIssue && (
          <IssuePanel
            issue={commandIssue}
            onClose={() => setCommandIssue(null)}
            onIssueChange={updated => setCommandIssue(updated)}
          />
        )}
      </div>
    </TooltipProvider>
  );
}

export default function App() {
  return (
    <BrowserRouter>
      <ProjectProvider>
        <WebSocketProvider>
          <AppInner />
        </WebSocketProvider>
      </ProjectProvider>
    </BrowserRouter>
  );
}
