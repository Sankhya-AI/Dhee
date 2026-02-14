import { useState } from "react";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogFooter,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import type { AgentInfo, Task } from "@/types/dashboard";

interface NewTaskDialogProps {
  open: boolean;
  onClose: () => void;
  onCreate: (data: Partial<Task>) => void;
  agents: AgentInfo[];
}

const priorities = ["low", "normal", "high", "urgent"] as const;

export function NewTaskDialog({ open, onClose, onCreate, agents }: NewTaskDialogProps) {
  const [title, setTitle] = useState("");
  const [description, setDescription] = useState("");
  const [priority, setPriority] = useState<string>("normal");
  const [assignedAgent, setAssignedAgent] = useState<string>("");
  const [tagInput, setTagInput] = useState("");

  const handleSubmit = () => {
    if (!title.trim()) return;
    const tags = tagInput
      .split(",")
      .map((t) => t.trim())
      .filter(Boolean);
    onCreate({
      title: title.trim(),
      description: description.trim(),
      priority: priority as Task["priority"],
      assigned_agent: assignedAgent || null,
      tags,
      status: assignedAgent ? "assigned" : "inbox",
    });
    setTitle("");
    setDescription("");
    setPriority("normal");
    setAssignedAgent("");
    setTagInput("");
    onClose();
  };

  return (
    <Dialog open={open} onOpenChange={onClose}>
      <DialogContent className="bg-card border-border max-w-md">
        <DialogHeader>
          <DialogTitle>New Task</DialogTitle>
        </DialogHeader>

        <div className="space-y-4 py-2">
          {/* Title */}
          <div>
            <label className="text-xs font-medium text-muted-foreground mb-1 block">Title</label>
            <Input
              value={title}
              onChange={(e) => setTitle(e.target.value)}
              placeholder="Fix robots.txt redirect..."
              autoFocus
            />
          </div>

          {/* Description */}
          <div>
            <label className="text-xs font-medium text-muted-foreground mb-1 block">Description</label>
            <textarea
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              placeholder="Details about the task..."
              rows={3}
              className="w-full resize-none bg-input rounded-md px-3 py-2 text-sm outline-none placeholder:text-muted-foreground focus:ring-1 focus:ring-ring"
            />
          </div>

          {/* Priority */}
          <div>
            <label className="text-xs font-medium text-muted-foreground mb-1.5 block">Priority</label>
            <div className="flex gap-1.5">
              {priorities.map((p) => (
                <Badge
                  key={p}
                  variant="outline"
                  className={`cursor-pointer text-xs capitalize ${
                    priority === p ? "bg-primary/20 text-primary border-primary/40" : ""
                  }`}
                  onClick={() => setPriority(p)}
                >
                  {p}
                </Badge>
              ))}
            </div>
          </div>

          {/* Assign agent */}
          <div>
            <label className="text-xs font-medium text-muted-foreground mb-1 block">Assign to Agent</label>
            <select
              value={assignedAgent}
              onChange={(e) => setAssignedAgent(e.target.value)}
              className="w-full bg-input rounded-md px-3 py-2 text-sm outline-none focus:ring-1 focus:ring-ring"
            >
              <option value="">Unassigned (Inbox)</option>
              {agents.map((a) => (
                <option key={a.name} value={a.name}>
                  {a.name} ({a.type})
                </option>
              ))}
            </select>
          </div>

          {/* Tags */}
          <div>
            <label className="text-xs font-medium text-muted-foreground mb-1 block">Tags (comma-separated)</label>
            <Input
              value={tagInput}
              onChange={(e) => setTagInput(e.target.value)}
              placeholder="dev, seo, phase-1"
            />
          </div>
        </div>

        <DialogFooter>
          <Button variant="ghost" onClick={onClose}>
            Cancel
          </Button>
          <Button onClick={handleSubmit} disabled={!title.trim()}>
            Create Task
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
