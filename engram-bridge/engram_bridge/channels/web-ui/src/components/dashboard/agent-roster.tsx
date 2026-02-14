import { Badge } from "@/components/ui/badge";
import { Bot, Cpu, Terminal } from "lucide-react";
import type { AgentInfo } from "@/types/dashboard";
import { STATUS_COLORS } from "@/types/dashboard";

interface AgentRosterProps {
  agents: AgentInfo[];
  activeCount: number;
}

const typeIcons: Record<string, React.ElementType> = {
  claude: Bot,
  codex: Cpu,
  custom: Terminal,
};

const typeLabels: Record<string, string> = {
  claude: "Claude Code",
  codex: "Codex",
  custom: "Custom CLI",
};

export function AgentRoster({ agents, activeCount }: AgentRosterProps) {
  return (
    <div className="h-full flex flex-col">
      {/* Header */}
      <div className="px-4 py-3 border-b border-border">
        <div className="flex items-center justify-between">
          <h2 className="text-sm font-semibold uppercase tracking-wider text-muted-foreground">
            Agents
          </h2>
          <Badge variant="outline" className="text-[10px]">
            {agents.length}
          </Badge>
        </div>
        {activeCount > 0 && (
          <Badge className="mt-1.5 text-[10px] bg-emerald-500/20 text-emerald-400 border-emerald-500/30" variant="outline">
            {activeCount} active
          </Badge>
        )}
      </div>

      {/* Agent list */}
      <div className="flex-1 overflow-y-auto py-2">
        {agents.map((agent) => {
          const Icon = typeIcons[agent.type] || Terminal;
          const dotColor = STATUS_COLORS[agent.status] || STATUS_COLORS.offline;

          return (
            <div
              key={agent.name}
              className="flex items-center gap-3 px-4 py-2.5 hover:bg-secondary/50 cursor-pointer transition-colors"
            >
              {/* Avatar */}
              <div className="relative flex-shrink-0">
                <div className="w-9 h-9 rounded-full bg-card border border-border flex items-center justify-center">
                  <Icon className="h-4 w-4 text-muted-foreground" />
                </div>
                {/* Status dot */}
                <div
                  className={`absolute -bottom-0.5 -right-0.5 w-3 h-3 rounded-full border-2 border-background ${dotColor}`}
                />
              </div>

              {/* Info */}
              <div className="min-w-0 flex-1">
                <div className="text-sm font-medium truncate">{agent.name}</div>
                <div className="text-[11px] text-muted-foreground truncate">
                  {typeLabels[agent.type] || agent.type}
                </div>
              </div>
            </div>
          );
        })}

        {agents.length === 0 && (
          <div className="px-4 py-8 text-center text-sm text-muted-foreground">
            No agents configured
          </div>
        )}
      </div>
    </div>
  );
}
