import { useState, useEffect } from "react";
import { X, Settings, Users, Brain, FolderKanban } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import { StatusDot } from "@/components/primitives/StatusDot";
import { useProjectContext } from "@/contexts/ProjectContext";
import { api } from "@/hooks/use-api";
import type { AgentInfo } from "@/types/dashboard";
import type { MemoryStats, SystemInfo } from "@/types";

interface Props {
  open: boolean;
  onClose: () => void;
}

type Tab = "general" | "agents" | "memory" | "project";

function GeneralTab() {
  return (
    <div className="space-y-4">
      <h3 className="text-sm font-semibold">General Settings</h3>
      <div className="text-sm text-muted-foreground">
        <p>Display preferences are saved automatically in your browser.</p>
        <p className="mt-2">
          Use <kbd className="px-1.5 py-0.5 rounded bg-muted text-xs font-mono">Cmd+K</kbd> to open the command bar.
        </p>
      </div>
    </div>
  );
}

function AgentsTab() {
  const [agents, setAgents] = useState<AgentInfo[]>([]);

  useEffect(() => {
    api.agents().then((data) => setAgents(data as AgentInfo[])).catch(() => {});
  }, []);

  return (
    <div className="space-y-4">
      <h3 className="text-sm font-semibold">Agents</h3>
      {agents.length === 0 ? (
        <p className="text-sm text-muted-foreground">No agents connected.</p>
      ) : (
        <div className="space-y-2">
          {agents.map((a, i) => (
            <div key={i} className="flex items-center gap-3 p-3 rounded-lg border border-border">
              <div className="w-8 h-8 rounded-full bg-muted flex items-center justify-center text-xs font-bold">
                {a.name?.[0]?.toUpperCase() || "?"}
              </div>
              <div className="flex-1 min-w-0">
                <div className="text-sm font-medium">{a.name}</div>
                <div className="text-xs text-muted-foreground">{a.model}</div>
              </div>
              <Badge
                variant="outline"
                className={`text-[10px] ${
                  a.status === "active"
                    ? "bg-emerald-50 text-emerald-700 border-emerald-200"
                    : a.status === "idle"
                      ? "bg-amber-50 text-amber-700 border-amber-200"
                      : "text-muted-foreground"
                }`}
              >
                {a.status}
              </Badge>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function MemoryTab() {
  const [stats, setStats] = useState<MemoryStats | null>(null);
  const [info, setInfo] = useState<SystemInfo | null>(null);

  useEffect(() => {
    api.memoryStats().then(setStats).catch(() => {});
    api.info().then(setInfo).catch(() => {});
  }, []);

  return (
    <div className="space-y-4">
      <h3 className="text-sm font-semibold">Memory</h3>
      <div className="p-3 rounded-lg border border-border space-y-2 text-sm">
        <div className="flex justify-between">
          <span className="text-muted-foreground">Memory available</span>
          <span>{info?.has_memory ? "Yes" : "No"}</span>
        </div>
        {stats && (
          <>
            <div className="flex justify-between">
              <span className="text-muted-foreground">Total memories</span>
              <span className="font-medium tabular-nums">{stats.total}</span>
            </div>
            <div className="flex justify-between">
              <span className="text-muted-foreground">SML / LML</span>
              <span className="tabular-nums">{stats.sml_count} / {stats.lml_count}</span>
            </div>
            <div className="flex justify-between">
              <span className="text-muted-foreground">Avg strength</span>
              <span className="tabular-nums">{stats.avg_strength.toFixed(3)}</span>
            </div>
            <div className="flex justify-between">
              <span className="text-muted-foreground">Echo enabled</span>
              <span>{stats.echo_enabled ? "Yes" : "No"}</span>
            </div>
          </>
        )}
      </div>
    </div>
  );
}

function ProjectTab() {
  const { currentProject, statuses, tags, createStatus, createTag } = useProjectContext();
  const [newStatusName, setNewStatusName] = useState("");
  const [newTagName, setNewTagName] = useState("");

  if (!currentProject) {
    return <p className="text-sm text-muted-foreground">No project selected.</p>;
  }

  return (
    <div className="space-y-4">
      <h3 className="text-sm font-semibold">Project: {currentProject.name}</h3>

      {/* Statuses */}
      <div>
        <h4 className="text-xs font-medium text-muted-foreground uppercase tracking-wider mb-2">
          Statuses
        </h4>
        <div className="space-y-1 mb-2">
          {statuses.map((s) => (
            <div key={s.id} className="flex items-center gap-2 px-2 py-1.5 rounded-md text-sm">
              <StatusDot color={s.color} size={8} />
              {s.name}
            </div>
          ))}
        </div>
        <div className="flex gap-2">
          <Input
            placeholder="New status name"
            value={newStatusName}
            onChange={(e) => setNewStatusName(e.target.value)}
            className="h-8 text-sm"
            onKeyDown={(e) => {
              if (e.key === "Enter" && newStatusName.trim()) {
                createStatus({ name: newStatusName.trim() });
                setNewStatusName("");
              }
            }}
          />
          <Button
            size="sm"
            className="h-8"
            disabled={!newStatusName.trim()}
            onClick={() => {
              createStatus({ name: newStatusName.trim() });
              setNewStatusName("");
            }}
          >
            Add
          </Button>
        </div>
      </div>

      {/* Tags */}
      <div>
        <h4 className="text-xs font-medium text-muted-foreground uppercase tracking-wider mb-2">
          Tags
        </h4>
        <div className="flex flex-wrap gap-1 mb-2">
          {tags.map((t) => (
            <span
              key={t.id}
              className="text-[10px] px-1.5 py-0.5 rounded-full"
              style={{ backgroundColor: `${t.color}20`, color: t.color }}
            >
              {t.name}
            </span>
          ))}
        </div>
        <div className="flex gap-2">
          <Input
            placeholder="New tag name"
            value={newTagName}
            onChange={(e) => setNewTagName(e.target.value)}
            className="h-8 text-sm"
            onKeyDown={(e) => {
              if (e.key === "Enter" && newTagName.trim()) {
                createTag({ name: newTagName.trim() });
                setNewTagName("");
              }
            }}
          />
          <Button
            size="sm"
            className="h-8"
            disabled={!newTagName.trim()}
            onClick={() => {
              createTag({ name: newTagName.trim() });
              setNewTagName("");
            }}
          >
            Add
          </Button>
        </div>
      </div>
    </div>
  );
}

const TABS: { key: Tab; label: string; icon: React.ElementType }[] = [
  { key: "general", label: "General", icon: Settings },
  { key: "agents", label: "Agents", icon: Users },
  { key: "memory", label: "Memory", icon: Brain },
  { key: "project", label: "Project", icon: FolderKanban },
];

export function SettingsDialog({ open, onClose }: Props) {
  const [activeTab, setActiveTab] = useState<Tab>("general");

  if (!open) return null;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center">
      <div className="absolute inset-0 bg-black/50" onClick={onClose} />
      <div className="relative bg-background border border-border rounded-xl shadow-2xl w-full max-w-2xl max-h-[80vh] flex overflow-hidden">
        {/* Sidebar */}
        <div className="w-48 border-r border-border bg-muted/30 p-3 space-y-0.5">
          <div className="flex items-center justify-between mb-4">
            <h2 className="text-sm font-semibold">Settings</h2>
            <Button variant="ghost" size="sm" onClick={onClose} className="h-7 w-7 p-0">
              <X className="h-4 w-4" />
            </Button>
          </div>
          {TABS.map((tab) => {
            const Icon = tab.icon;
            return (
              <button
                key={tab.key}
                onClick={() => setActiveTab(tab.key)}
                className={`flex items-center gap-2 w-full px-2.5 py-2 rounded-md text-sm transition-colors ${
                  activeTab === tab.key
                    ? "bg-background text-foreground shadow-sm"
                    : "text-muted-foreground hover:text-foreground hover:bg-muted/50"
                }`}
              >
                <Icon className="h-4 w-4" />
                {tab.label}
              </button>
            );
          })}
        </div>

        {/* Content */}
        <div className="flex-1 p-6 overflow-y-auto">
          {activeTab === "general" && <GeneralTab />}
          {activeTab === "agents" && <AgentsTab />}
          {activeTab === "memory" && <MemoryTab />}
          {activeTab === "project" && <ProjectTab />}
        </div>
      </div>
    </div>
  );
}
