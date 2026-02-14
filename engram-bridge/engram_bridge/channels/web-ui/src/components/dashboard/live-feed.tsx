import { Badge } from "@/components/ui/badge";
import {
  ArrowRight,
  MessageSquare,
  Plus,
  UserPlus,
  Activity,
} from "lucide-react";
import type { FeedEvent } from "@/types/dashboard";

interface LiveFeedProps {
  events: FeedEvent[];
}

function timeAgo(iso: string): string {
  const diff = Date.now() - new Date(iso).getTime();
  const mins = Math.floor(diff / 60000);
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins}m ago`;
  const hrs = Math.floor(mins / 60);
  if (hrs < 24) return `${hrs}h ago`;
  return `${Math.floor(hrs / 24)}d ago`;
}

const eventConfig: Record<string, { icon: React.ElementType; color: string }> = {
  task_created: { icon: Plus, color: "text-emerald-400" },
  task_moved: { icon: ArrowRight, color: "text-blue-400" },
  task_assigned: { icon: UserPlus, color: "text-purple-400" },
  comment: { icon: MessageSquare, color: "text-orange-400" },
};

function FeedEntry({ event }: { event: FeedEvent }) {
  const config = eventConfig[event.event] || { icon: Activity, color: "text-muted-foreground" };
  const Icon = config.icon;

  let description = "";
  switch (event.event) {
    case "task_created":
      description = `New task: ${event.title}`;
      break;
    case "task_moved":
      description = `Moved to ${event.to}`;
      break;
    case "task_assigned":
      description = `Assigned to ${event.agent}`;
      break;
    case "comment":
      description = event.text || "commented";
      break;
    default:
      description = event.event;
  }

  return (
    <div className="flex gap-3 px-4 py-2.5 hover:bg-secondary/30 transition-colors">
      <div className={`mt-0.5 ${config.color}`}>
        <Icon className="h-3.5 w-3.5" />
      </div>
      <div className="min-w-0 flex-1">
        <p className="text-sm leading-snug">
          {event.agent && (
            <span className="font-medium text-foreground">{event.agent}</span>
          )}{" "}
          <span className="text-muted-foreground">{description}</span>
        </p>
        {event.title && event.event !== "task_created" && (
          <div className="mt-0.5">
            <Badge variant="outline" className="text-[10px] px-1.5 py-0 text-muted-foreground">
              {event.title}
            </Badge>
          </div>
        )}
        <span className="text-[10px] text-muted-foreground/60">{timeAgo(event.ts)}</span>
      </div>
    </div>
  );
}

export function LiveFeed({ events }: LiveFeedProps) {
  const reversed = [...events].reverse();

  return (
    <div className="h-full flex flex-col">
      {/* Header */}
      <div className="px-4 py-3 border-b border-border">
        <div className="flex items-center gap-2">
          <div className="w-2 h-2 rounded-full bg-red-500 animate-pulse" />
          <h2 className="text-sm font-semibold uppercase tracking-wider text-muted-foreground">
            Live Feed
          </h2>
        </div>
      </div>

      {/* Feed */}
      <div className="flex-1 overflow-y-auto">
        {reversed.length === 0 && (
          <div className="px-4 py-8 text-center text-sm text-muted-foreground">
            No activity yet
          </div>
        )}
        {reversed.map((event) => (
          <FeedEntry key={event.id} event={event} />
        ))}
      </div>
    </div>
  );
}
