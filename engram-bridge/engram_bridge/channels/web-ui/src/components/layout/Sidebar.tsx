import { Plus, FolderKanban } from "lucide-react";
import { useProjectContext } from "@/contexts/ProjectContext";
import { StatusDot } from "@/components/primitives/StatusDot";

interface Props {
  open: boolean;
  onCreateProject: () => void;
}

export function Sidebar({ open, onCreateProject }: Props) {
  const { projects, currentProject, selectProject, statuses, tags } = useProjectContext();

  if (!open) return null;

  return (
    <aside className="w-56 border-r border-border/40 flex flex-col bg-sidebar overflow-y-auto">
      {/* Projects */}
      <div className="p-3">
        <div className="flex items-center justify-between mb-2">
          <span className="text-[11px] font-medium text-muted-foreground uppercase tracking-wider">Projects</span>
          <button onClick={onCreateProject} className="p-0.5 rounded hover:bg-muted/50 text-muted-foreground">
            <Plus className="h-3.5 w-3.5" />
          </button>
        </div>
        <div className="space-y-0.5">
          {projects.map(p => (
            <button
              key={p.id}
              onClick={() => selectProject(p.id)}
              className={`flex items-center gap-2 w-full px-2 py-1.5 rounded-md text-sm transition-colors ${
                currentProject?.id === p.id
                  ? "bg-sidebar-accent text-sidebar-accent-foreground"
                  : "text-sidebar-foreground/70 hover:bg-sidebar-accent/50"
              }`}
            >
              <FolderKanban className="h-3.5 w-3.5 flex-shrink-0" style={{ color: p.color }} />
              <span className="truncate">{p.name}</span>
            </button>
          ))}
        </div>
      </div>

      {/* Current project statuses */}
      {currentProject && statuses.length > 0 && (
        <div className="px-3 py-2 border-t border-border/30">
          <span className="text-[11px] font-medium text-muted-foreground uppercase tracking-wider">Statuses</span>
          <div className="mt-1.5 space-y-0.5">
            {statuses.map(s => (
              <div key={s.id} className="flex items-center gap-2 px-2 py-1 text-xs text-muted-foreground">
                <StatusDot color={s.color} size={6} />
                {s.name}
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Tags */}
      {currentProject && tags.length > 0 && (
        <div className="px-3 py-2 border-t border-border/30">
          <span className="text-[11px] font-medium text-muted-foreground uppercase tracking-wider">Tags</span>
          <div className="mt-1.5 flex flex-wrap gap-1 px-2">
            {tags.map(t => (
              <span
                key={t.id}
                className="text-[10px] px-1.5 py-0.5 rounded-full"
                style={{ backgroundColor: `${t.color}20`, color: t.color }}
              >
                {t.name}
              </span>
            ))}
          </div>
        </div>
      )}
    </aside>
  );
}
