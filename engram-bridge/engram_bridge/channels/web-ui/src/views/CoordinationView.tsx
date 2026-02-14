import { useState, useEffect, useCallback } from "react";
import {
  Search,
  Bot,
  Cpu,
  Activity,
  ArrowRightLeft,
  Play,
  RefreshCw,
  Clock,
  CheckCircle2,
  XCircle,
  Zap,
  CircleDot,
  Network,
} from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { api } from "@/hooks/use-api";
import { useProjectContext } from "@/contexts/ProjectContext";
import type { CoordinationAgent, CoordinationEvent, Issue } from "@/types";

// ── Status colors ──

const STATUS_STYLE: Record<string, string> = {
  available: "bg-emerald-50 text-emerald-700 border-emerald-200",
  busy: "bg-amber-50 text-amber-700 border-amber-200",
  offline: "bg-zinc-100 text-zinc-500 border-zinc-200",
};

const STATUS_DOT: Record<string, string> = {
  available: "bg-emerald-500",
  busy: "bg-amber-500",
  offline: "bg-zinc-400",
};

// ── Agent Card ──

function AgentCard({
  agent,
  selected,
  onClick,
}: {
  agent: CoordinationAgent;
  selected: boolean;
  onClick: () => void;
}) {
  return (
    <button
      onClick={onClick}
      className={`w-full text-left p-3 border-b border-border/40 transition-colors ${
        selected ? "bg-accent" : "hover:bg-muted/50"
      }`}
    >
      <div className="flex items-center gap-2 mb-1.5">
        <div className="relative">
          <div className="h-7 w-7 rounded-md bg-muted flex items-center justify-center">
            <Bot className="h-3.5 w-3.5 text-muted-foreground" />
          </div>
          <span
            className={`absolute -bottom-0.5 -right-0.5 h-2.5 w-2.5 rounded-full border-2 border-background ${
              STATUS_DOT[agent.status] || STATUS_DOT.offline
            }`}
          />
        </div>
        <div className="flex-1 min-w-0">
          <div className="text-sm font-medium truncate">{agent.name}</div>
          <div className="text-[10px] text-muted-foreground">
            {agent.type} {agent.model ? `/ ${agent.model}` : ""}
          </div>
        </div>
        <Badge
          variant="outline"
          className={`text-[10px] px-1.5 py-0 ${STATUS_STYLE[agent.status] || STATUS_STYLE.offline}`}
        >
          {agent.status}
        </Badge>
      </div>

      {/* Capabilities */}
      <div className="flex flex-wrap gap-1 mt-1.5">
        {agent.capabilities.slice(0, 5).map((cap) => (
          <span
            key={cap}
            className="text-[9px] px-1.5 py-0.5 rounded-full bg-muted text-muted-foreground"
          >
            {cap}
          </span>
        ))}
        {agent.capabilities.length > 5 && (
          <span className="text-[9px] px-1.5 py-0.5 rounded-full bg-muted text-muted-foreground">
            +{agent.capabilities.length - 5}
          </span>
        )}
      </div>

      {/* Load indicator */}
      <div className="flex items-center gap-2 mt-2">
        <div className="flex-1 h-1.5 bg-muted rounded-full overflow-hidden">
          <div
            className={`h-full rounded-full transition-all duration-500 ${
              agent.active_tasks.length >= agent.max_concurrent
                ? "bg-amber-400"
                : "bg-emerald-400"
            }`}
            style={{
              width: `${Math.min((agent.active_tasks.length / Math.max(agent.max_concurrent, 1)) * 100, 100)}%`,
            }}
          />
        </div>
        <span className="text-[10px] text-muted-foreground tabular-nums">
          {agent.active_tasks.length}/{agent.max_concurrent}
        </span>
      </div>
    </button>
  );
}

// ── Event Row ──

const EVENT_ICONS: Record<string, typeof ArrowRightLeft> = {
  task_routed: ArrowRightLeft,
  task_claimed: CheckCircle2,
  task_released: XCircle,
};

const EVENT_COLORS: Record<string, string> = {
  task_routed: "text-blue-500",
  task_claimed: "text-emerald-500",
  task_released: "text-amber-500",
};

function EventRow({ event }: { event: CoordinationEvent }) {
  const Icon = EVENT_ICONS[event.type] || Activity;
  const color = EVENT_COLORS[event.type] || "text-muted-foreground";
  const ts = event.timestamp
    ? new Date(event.timestamp).toLocaleTimeString([], {
        hour: "2-digit",
        minute: "2-digit",
        second: "2-digit",
      })
    : "";

  return (
    <div className="flex items-start gap-2.5 px-4 py-2.5 border-b border-border/30 hover:bg-muted/30 transition-colors">
      <div className={`mt-0.5 ${color}`}>
        <Icon className="h-3.5 w-3.5" />
      </div>
      <div className="flex-1 min-w-0">
        <p className="text-sm leading-snug">{event.content}</p>
        <span className="text-[10px] text-muted-foreground">{ts}</span>
      </div>
    </div>
  );
}

// ── Match Result Card ──

function MatchCard({
  agent,
  onRoute,
}: {
  agent: CoordinationAgent;
  onRoute?: () => void;
}) {
  return (
    <div className="flex items-center gap-3 px-4 py-3 border-b border-border/30">
      <div className="relative">
        <div className="h-8 w-8 rounded-md bg-muted flex items-center justify-center">
          <Cpu className="h-4 w-4 text-muted-foreground" />
        </div>
        <span
          className={`absolute -bottom-0.5 -right-0.5 h-2.5 w-2.5 rounded-full border-2 border-background ${
            STATUS_DOT[agent.status] || STATUS_DOT.offline
          }`}
        />
      </div>
      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-2">
          <span className="text-sm font-medium">{agent.name}</span>
          <Badge
            variant="outline"
            className={`text-[10px] px-1.5 py-0 ${STATUS_STYLE[agent.status] || ""}`}
          >
            {agent.status}
          </Badge>
        </div>
        <div className="flex items-center gap-2 mt-0.5">
          {agent.similarity !== undefined && (
            <span className="text-[10px] text-muted-foreground tabular-nums">
              score: {agent.similarity.toFixed(3)}
            </span>
          )}
          <span className="text-[10px] text-muted-foreground">
            {agent.active_tasks.length}/{agent.max_concurrent} tasks
          </span>
        </div>
        <div className="flex flex-wrap gap-1 mt-1">
          {agent.capabilities.slice(0, 4).map((cap) => (
            <span
              key={cap}
              className="text-[9px] px-1.5 py-0.5 rounded-full bg-muted text-muted-foreground"
            >
              {cap}
            </span>
          ))}
        </div>
      </div>
      {onRoute && agent.status === "available" && (
        <Button variant="ghost" size="icon" className="h-7 w-7" onClick={onRoute}>
          <Play className="h-3.5 w-3.5" />
        </Button>
      )}
    </div>
  );
}

// ── Agent Detail Panel ──

function AgentDetailPanel({ agent }: { agent: CoordinationAgent | null }) {
  if (!agent) {
    return (
      <div className="flex flex-col items-center justify-center h-full text-muted-foreground py-12">
        <Bot className="h-8 w-8 mb-2 opacity-30" />
        <p className="text-sm">Select an agent</p>
        <p className="text-xs mt-1">Click an agent to view details</p>
      </div>
    );
  }

  return (
    <div className="p-4 overflow-y-auto h-full space-y-4">
      {/* Header */}
      <div className="flex items-center gap-3">
        <div className="relative">
          <div className="h-10 w-10 rounded-lg bg-muted flex items-center justify-center">
            <Bot className="h-5 w-5 text-muted-foreground" />
          </div>
          <span
            className={`absolute -bottom-0.5 -right-0.5 h-3 w-3 rounded-full border-2 border-background ${
              STATUS_DOT[agent.status] || STATUS_DOT.offline
            }`}
          />
        </div>
        <div>
          <h3 className="text-sm font-semibold">{agent.name}</h3>
          <p className="text-xs text-muted-foreground">
            {agent.type} {agent.model ? `/ ${agent.model}` : ""}
          </p>
        </div>
      </div>

      {/* Properties */}
      <div className="border-t border-border pt-3">
        <h4 className="text-xs font-semibold uppercase tracking-wider text-muted-foreground mb-2">
          Properties
        </h4>
        <div className="space-y-1.5 text-sm">
          <div className="flex justify-between">
            <span className="text-muted-foreground">Status</span>
            <Badge
              variant="outline"
              className={`text-[10px] ${STATUS_STYLE[agent.status] || ""}`}
            >
              {agent.status}
            </Badge>
          </div>
          <div className="flex justify-between">
            <span className="text-muted-foreground">Max Concurrent</span>
            <span className="tabular-nums">{agent.max_concurrent}</span>
          </div>
          <div className="flex justify-between">
            <span className="text-muted-foreground">Active Tasks</span>
            <span className="tabular-nums">{agent.active_tasks.length}</span>
          </div>
          {agent.registered_at && (
            <div className="flex justify-between">
              <span className="text-muted-foreground">Registered</span>
              <span className="text-xs">
                {new Date(agent.registered_at).toLocaleDateString()}
              </span>
            </div>
          )}
          {agent.last_seen && (
            <div className="flex justify-between">
              <span className="text-muted-foreground">Last Seen</span>
              <span className="text-xs">
                {new Date(agent.last_seen).toLocaleTimeString()}
              </span>
            </div>
          )}
        </div>
      </div>

      {/* Capabilities */}
      <div className="border-t border-border pt-3">
        <h4 className="text-xs font-semibold uppercase tracking-wider text-muted-foreground mb-2">
          Capabilities
        </h4>
        <div className="flex flex-wrap gap-1.5">
          {agent.capabilities.map((cap) => (
            <span
              key={cap}
              className="text-[10px] px-2 py-0.5 rounded-full bg-muted text-muted-foreground"
            >
              {cap}
            </span>
          ))}
        </div>
      </div>

      {/* Active Tasks */}
      {agent.active_tasks.length > 0 && (
        <div className="border-t border-border pt-3">
          <h4 className="text-xs font-semibold uppercase tracking-wider text-muted-foreground mb-2">
            Active Tasks
          </h4>
          <div className="space-y-1">
            {agent.active_tasks.map((tid) => (
              <div
                key={tid}
                className="flex items-center gap-2 px-2 py-1.5 rounded-md bg-muted/50 text-xs"
              >
                <CircleDot className="h-3 w-3 text-emerald-500" />
                <span className="font-mono text-muted-foreground">{tid}</span>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Description */}
      {agent.description && (
        <div className="border-t border-border pt-3">
          <h4 className="text-xs font-semibold uppercase tracking-wider text-muted-foreground mb-2">
            Description
          </h4>
          <p className="text-sm leading-relaxed text-muted-foreground">
            {agent.description}
          </p>
        </div>
      )}
    </div>
  );
}

// ── Main View ──

type CenterTab = "match" | "events";

export function CoordinationView() {
  const { issues } = useProjectContext();

  // State
  const [agents, setAgents] = useState<CoordinationAgent[]>([]);
  const [events, setEvents] = useState<CoordinationEvent[]>([]);
  const [matchResults, setMatchResults] = useState<CoordinationAgent[]>([]);
  const [selectedAgent, setSelectedAgent] = useState<CoordinationAgent | null>(null);
  const [searchQuery, setSearchQuery] = useState("");
  const [centerTab, setCenterTab] = useState<CenterTab>("match");
  const [loading, setLoading] = useState(false);
  const [matchLoading, setMatchLoading] = useState(false);
  const [routingId, setRoutingId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  // Load agents + events
  const refresh = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [a, e] = await Promise.all([
        api.coordinationAgents(),
        api.coordinationEvents(50),
      ]);
      setAgents(a);
      setEvents(e);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load coordination data");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    refresh();
  }, [refresh]);

  // Semantic search
  useEffect(() => {
    if (!searchQuery.trim()) {
      setMatchResults([]);
      return;
    }
    const timer = setTimeout(async () => {
      setMatchLoading(true);
      try {
        const results = await api.coordinationMatch(searchQuery);
        setMatchResults(results);
      } catch {
        setMatchResults([]);
      } finally {
        setMatchLoading(false);
      }
    }, 300);
    return () => clearTimeout(timer);
  }, [searchQuery]);

  // Route a single task
  const handleRoute = useCallback(
    async (taskId: string) => {
      setRoutingId(taskId);
      try {
        await api.coordinationRoute(taskId, true);
        await refresh();
      } catch {
        // ignore
      } finally {
        setRoutingId(null);
      }
    },
    [refresh],
  );

  // Batch route all pending
  const handleRoutePending = useCallback(async () => {
    setRoutingId("batch");
    try {
      const result = await api.coordinationRoutePending();
      if (result.routed > 0) await refresh();
    } catch {
      // ignore
    } finally {
      setRoutingId(null);
    }
  }, [refresh]);

  // Unassigned tasks for routing
  const unassignedTasks = issues.filter(
    (t) => !t.assigned_agent && (t.status === "inbox" || !t.status),
  );

  // 503 = coordination not enabled
  const notEnabled = error?.includes("503");

  if (notEnabled) {
    return (
      <div className="flex flex-1 items-center justify-center">
        <div className="text-center space-y-3 max-w-sm">
          <Network className="h-12 w-12 mx-auto text-muted-foreground/30" />
          <h2 className="text-lg font-semibold">Coordination Not Enabled</h2>
          <p className="text-sm text-muted-foreground leading-relaxed">
            Add <code className="text-xs bg-muted px-1.5 py-0.5 rounded">{`"coordination": {"enabled": true}`}</code> to
            your <code className="text-xs bg-muted px-1.5 py-0.5 rounded">~/.engram/bridge.json</code> and
            restart the bridge.
          </p>
        </div>
      </div>
    );
  }

  return (
    <div className="flex flex-1 overflow-hidden">
      {/* ── Left: Agent Registry ── */}
      <div className="w-64 border-r border-border flex flex-col bg-sidebar overflow-hidden">
        <div className="flex items-center justify-between px-3 py-2.5 border-b border-border">
          <h3 className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">
            Agents
          </h3>
          <div className="flex items-center gap-1">
            <Badge variant="outline" className="text-[10px] px-1.5 py-0 tabular-nums">
              {agents.length}
            </Badge>
            <Button
              variant="ghost"
              size="icon"
              className="h-6 w-6"
              onClick={refresh}
              disabled={loading}
            >
              <RefreshCw className={`h-3 w-3 ${loading ? "animate-spin" : ""}`} />
            </Button>
          </div>
        </div>

        <div className="flex-1 overflow-y-auto">
          {agents.length === 0 && !loading ? (
            <div className="flex flex-col items-center justify-center h-full text-muted-foreground py-12">
              <Bot className="h-8 w-8 mb-2 opacity-30" />
              <p className="text-sm">No agents registered</p>
              <p className="text-xs mt-1">Agents appear when the bridge starts</p>
            </div>
          ) : (
            agents.map((agent) => (
              <AgentCard
                key={agent.id || agent.name}
                agent={agent}
                selected={selectedAgent?.name === agent.name}
                onClick={() => setSelectedAgent(agent)}
              />
            ))
          )}
        </div>

        {/* Summary footer */}
        {agents.length > 0 && (
          <div className="px-3 py-2 border-t border-border text-[10px] text-muted-foreground flex gap-3">
            <span className="flex items-center gap-1">
              <span className="w-1.5 h-1.5 rounded-full bg-emerald-500" />
              {agents.filter((a) => a.status === "available").length} available
            </span>
            <span className="flex items-center gap-1">
              <span className="w-1.5 h-1.5 rounded-full bg-amber-500" />
              {agents.filter((a) => a.status === "busy").length} busy
            </span>
            <span className="flex items-center gap-1">
              <span className="w-1.5 h-1.5 rounded-full bg-zinc-400" />
              {agents.filter((a) => a.status === "offline").length} offline
            </span>
          </div>
        )}
      </div>

      {/* ── Center: Match / Events ── */}
      <div className="flex-1 flex flex-col overflow-hidden">
        {/* Tab bar */}
        <div className="flex items-center border-b border-border">
          <button
            onClick={() => setCenterTab("match")}
            className={`flex items-center gap-1.5 px-4 py-2.5 text-xs font-medium border-b-2 transition-colors ${
              centerTab === "match"
                ? "border-primary text-foreground"
                : "border-transparent text-muted-foreground hover:text-foreground"
            }`}
          >
            <Search className="h-3 w-3" />
            Routing
          </button>
          <button
            onClick={() => setCenterTab("events")}
            className={`flex items-center gap-1.5 px-4 py-2.5 text-xs font-medium border-b-2 transition-colors ${
              centerTab === "events"
                ? "border-primary text-foreground"
                : "border-transparent text-muted-foreground hover:text-foreground"
            }`}
          >
            <Activity className="h-3 w-3" />
            Events
            {events.length > 0 && (
              <span className="text-[10px] text-muted-foreground tabular-nums">
                ({events.length})
              </span>
            )}
          </button>
        </div>

        {centerTab === "match" ? (
          <div className="flex-1 flex flex-col overflow-hidden">
            {/* Search bar */}
            <div className="p-3 border-b border-border space-y-2">
              <div className="relative">
                <Search className="absolute left-2.5 top-1/2 -translate-y-1/2 h-3.5 w-3.5 text-muted-foreground" />
                <Input
                  placeholder="Search capabilities... (e.g. python debugging)"
                  value={searchQuery}
                  onChange={(e) => setSearchQuery(e.target.value)}
                  className="pl-8 h-8 text-sm"
                />
              </div>
              {/* Batch route button */}
              {unassignedTasks.length > 0 && (
                <div className="flex items-center justify-between">
                  <span className="text-xs text-muted-foreground">
                    {unassignedTasks.length} unassigned task{unassignedTasks.length !== 1 ? "s" : ""}
                  </span>
                  <Button
                    variant="outline"
                    size="sm"
                    className="h-7 text-xs gap-1.5"
                    onClick={handleRoutePending}
                    disabled={routingId === "batch"}
                  >
                    {routingId === "batch" ? (
                      <RefreshCw className="h-3 w-3 animate-spin" />
                    ) : (
                      <Zap className="h-3 w-3" />
                    )}
                    Route All Pending
                  </Button>
                </div>
              )}
            </div>

            {/* Results / task list */}
            <div className="flex-1 overflow-y-auto">
              {searchQuery.trim() ? (
                // Show semantic match results
                matchLoading ? (
                  <div className="flex items-center justify-center h-32">
                    <div className="text-sm text-muted-foreground">Searching...</div>
                  </div>
                ) : matchResults.length === 0 ? (
                  <div className="flex flex-col items-center justify-center h-32 text-muted-foreground">
                    <p className="text-sm">No matching agents</p>
                    <p className="text-xs mt-1">Try different keywords</p>
                  </div>
                ) : (
                  <>
                    <div className="px-4 py-2 border-b border-border">
                      <span className="text-[11px] font-medium text-muted-foreground uppercase tracking-wider">
                        Best Matches
                      </span>
                    </div>
                    {matchResults.map((agent) => (
                      <MatchCard key={agent.id || agent.name} agent={agent} />
                    ))}
                  </>
                )
              ) : (
                // Show unassigned tasks ready for routing
                <>
                  <div className="px-4 py-2 border-b border-border">
                    <span className="text-[11px] font-medium text-muted-foreground uppercase tracking-wider">
                      Unassigned Tasks
                    </span>
                  </div>
                  {unassignedTasks.length === 0 ? (
                    <div className="flex flex-col items-center justify-center h-32 text-muted-foreground">
                      <CheckCircle2 className="h-6 w-6 mb-2 opacity-30" />
                      <p className="text-sm">All tasks assigned</p>
                    </div>
                  ) : (
                    unassignedTasks.map((task) => (
                      <UnassignedTaskRow
                        key={task.id}
                        task={task}
                        routing={routingId === task.id}
                        onRoute={() => handleRoute(task.id)}
                      />
                    ))
                  )}
                </>
              )}
            </div>
          </div>
        ) : (
          // Events timeline
          <div className="flex-1 overflow-y-auto">
            {events.length === 0 ? (
              <div className="flex flex-col items-center justify-center h-full text-muted-foreground py-12">
                <Activity className="h-8 w-8 mb-2 opacity-30" />
                <p className="text-sm">No coordination events yet</p>
                <p className="text-xs mt-1">Events appear as tasks get routed</p>
              </div>
            ) : (
              events.map((event) => (
                <EventRow key={event.id} event={event} />
              ))
            )}
          </div>
        )}
      </div>

      {/* ── Right: Agent Detail ── */}
      <div className="w-72 border-l border-border flex flex-col overflow-hidden">
        <div className="px-3 py-2.5 border-b border-border">
          <h3 className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">
            Agent Detail
          </h3>
        </div>
        <div className="flex-1 overflow-hidden">
          <AgentDetailPanel agent={selectedAgent} />
        </div>
      </div>
    </div>
  );
}

// ── Unassigned Task Row ──

function UnassignedTaskRow({
  task,
  routing,
  onRoute,
}: {
  task: Issue;
  routing: boolean;
  onRoute: () => void;
}) {
  return (
    <div className="flex items-center gap-3 px-4 py-3 border-b border-border/30 hover:bg-muted/30 transition-colors">
      <div className="flex-1 min-w-0">
        <p className="text-sm font-medium truncate">{task.title}</p>
        {task.description && (
          <p className="text-xs text-muted-foreground truncate mt-0.5">
            {task.description}
          </p>
        )}
        {task.tags && task.tags.length > 0 && (
          <div className="flex flex-wrap gap-1 mt-1">
            {task.tags.slice(0, 3).map((tag) => (
              <span
                key={tag}
                className="text-[9px] px-1.5 py-0.5 rounded-full bg-muted text-muted-foreground"
              >
                {tag}
              </span>
            ))}
          </div>
        )}
      </div>
      <Button
        variant="outline"
        size="sm"
        className="h-7 text-xs gap-1 shrink-0"
        onClick={onRoute}
        disabled={routing}
      >
        {routing ? (
          <RefreshCw className="h-3 w-3 animate-spin" />
        ) : (
          <ArrowRightLeft className="h-3 w-3" />
        )}
        Route
      </Button>
    </div>
  );
}
