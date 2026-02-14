import { Button } from "@/components/ui/button";
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import {
  Play,
  ArrowRightLeft,
  Activity,
  Bot,
  Square,
  History,
  Brain,
} from "lucide-react";

interface CommandBarProps {
  onCommand: (command: string) => void;
}

const commands = [
  { cmd: "start", icon: Play, label: "Start", needsArgs: true, hint: "agent repo" },
  { cmd: "switch", icon: ArrowRightLeft, label: "Switch", needsArgs: true, hint: "agent" },
  { cmd: "status", icon: Activity, label: "Status", needsArgs: false },
  { cmd: "agents", icon: Bot, label: "Agents", needsArgs: false },
  { cmd: "stop", icon: Square, label: "Stop", needsArgs: false },
  { cmd: "sessions", icon: History, label: "Sessions", needsArgs: false },
  { cmd: "memory", icon: Brain, label: "Memory", needsArgs: true, hint: "query" },
] as const;

export function CommandBar({ onCommand }: CommandBarProps) {
  const handleClick = (cmd: typeof commands[number]) => {
    if (cmd.needsArgs) {
      const args = prompt(`/${cmd.cmd} — enter arguments (${cmd.hint}):`);
      if (args !== null) {
        onCommand(`/${cmd.cmd} ${args}`.trim());
      }
    } else {
      onCommand(`/${cmd.cmd}`);
    }
  };

  return (
    <div className="flex items-center gap-1.5 px-4 py-2 border-b border-border overflow-x-auto">
      {commands.map((cmd) => (
        <Tooltip key={cmd.cmd}>
          <TooltipTrigger asChild>
            <Button
              variant="ghost"
              size="sm"
              className="h-7 px-2 text-xs text-muted-foreground hover:text-foreground hover:bg-secondary gap-1.5 shrink-0"
              onClick={() => handleClick(cmd)}
            >
              <cmd.icon className="h-3.5 w-3.5" />
              {cmd.label}
            </Button>
          </TooltipTrigger>
          <TooltipContent>
            <p>/{cmd.cmd}{cmd.needsArgs ? ` <${cmd.hint}>` : ""}</p>
          </TooltipContent>
        </Tooltip>
      ))}
    </div>
  );
}
