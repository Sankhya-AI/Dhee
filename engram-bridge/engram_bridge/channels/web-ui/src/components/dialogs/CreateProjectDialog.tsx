import { useState, useCallback } from "react";
import { X } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { useProjectContext } from "@/contexts/ProjectContext";

const COLORS = [
  "#6366f1", "#8b5cf6", "#ec4899", "#ef4444", "#f59e0b",
  "#22c55e", "#06b6d4", "#3b82f6", "#64748b", "#f97316",
];

interface Props {
  open: boolean;
  onClose: () => void;
}

export function CreateProjectDialog({ open, onClose }: Props) {
  const { createProject } = useProjectContext();
  const [name, setName] = useState("");
  const [color, setColor] = useState("#6366f1");
  const [description, setDescription] = useState("");
  const [saving, setSaving] = useState(false);

  const handleCreate = useCallback(async () => {
    if (!name.trim()) return;
    setSaving(true);
    try {
      await createProject({ name: name.trim(), color, description });
      setName("");
      setColor("#6366f1");
      setDescription("");
      onClose();
    } finally {
      setSaving(false);
    }
  }, [name, color, description, createProject, onClose]);

  if (!open) return null;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center">
      <div className="absolute inset-0 bg-black/50" onClick={onClose} />
      <div className="relative bg-background border border-border rounded-xl shadow-2xl w-full max-w-md p-6">
        <div className="flex items-center justify-between mb-4">
          <h2 className="text-lg font-semibold">Create Project</h2>
          <Button variant="ghost" size="sm" onClick={onClose} className="h-7 w-7 p-0">
            <X className="h-4 w-4" />
          </Button>
        </div>

        <div className="space-y-4">
          <div>
            <label className="text-xs text-muted-foreground mb-1 block">Name</label>
            <Input
              value={name}
              onChange={e => setName(e.target.value)}
              placeholder="Project name"
              autoFocus
              onKeyDown={e => { if (e.key === "Enter") handleCreate(); }}
            />
          </div>

          <div>
            <label className="text-xs text-muted-foreground mb-1 block">Color</label>
            <div className="flex gap-2">
              {COLORS.map(c => (
                <button
                  key={c}
                  onClick={() => setColor(c)}
                  className={`w-7 h-7 rounded-full transition-transform ${
                    color === c ? "ring-2 ring-primary ring-offset-2 ring-offset-background scale-110" : "hover:scale-105"
                  }`}
                  style={{ backgroundColor: c }}
                />
              ))}
            </div>
          </div>

          <div>
            <label className="text-xs text-muted-foreground mb-1 block">Description (optional)</label>
            <Textarea
              value={description}
              onChange={e => setDescription(e.target.value)}
              placeholder="What is this project about?"
              className="min-h-[60px]"
            />
          </div>

          <Button onClick={handleCreate} disabled={!name.trim() || saving} className="w-full">
            {saving ? "Creating..." : "Create Project"}
          </Button>
        </div>
      </div>
    </div>
  );
}
