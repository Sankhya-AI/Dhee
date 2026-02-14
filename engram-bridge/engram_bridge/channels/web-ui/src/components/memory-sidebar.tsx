import {
  Sheet,
  SheetContent,
  SheetHeader,
  SheetTitle,
  SheetTrigger,
} from "@/components/ui/sheet";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Separator } from "@/components/ui/separator";
import {
  Brain,
  Layers,
  Activity,
  Database,
  Zap,
  Archive,
  BarChart3,
  Sparkles,
} from "lucide-react";

export interface MemoryStats {
  total: number;
  sml_count: number;
  lml_count: number;
  avg_strength: number;
  echo_stats: {
    shallow: number;
    medium: number;
    deep: number;
    none: number;
  };
  echo_enabled: boolean;
}

export interface SessionInfo {
  agent: string;
  repo: string;
  running: boolean;
  sessionId: string | null;
}

interface MemorySidebarProps {
  stats: MemoryStats | null;
  session: SessionInfo | null;
  onRefresh: () => void;
}

function StatCard({
  icon: Icon,
  label,
  value,
  color,
}: {
  icon: React.ElementType;
  label: string;
  value: string | number;
  color?: string;
}) {
  return (
    <div className="flex items-center justify-between py-2">
      <div className="flex items-center gap-2 text-sm text-muted-foreground">
        <Icon className={`h-3.5 w-3.5 ${color || ""}`} />
        <span>{label}</span>
      </div>
      <span className="text-sm font-medium tabular-nums">{value}</span>
    </div>
  );
}

function StrengthBar({ value, max = 1 }: { value: number; max?: number }) {
  const pct = Math.min((value / max) * 100, 100);
  return (
    <div className="w-full h-1.5 bg-muted rounded-full overflow-hidden">
      <div
        className="h-full rounded-full transition-all duration-500"
        style={{
          width: `${pct}%`,
          background: `linear-gradient(90deg, oklch(0.58 0.22 280), oklch(0.72 0.15 195))`,
        }}
      />
    </div>
  );
}

export function MemorySidebar({ stats, session, onRefresh }: MemorySidebarProps) {
  return (
    <Sheet>
      <SheetTrigger asChild>
        <Button
          variant="ghost"
          size="icon"
          className="h-8 w-8 text-muted-foreground hover:text-foreground"
        >
          <BarChart3 className="h-4 w-4" />
        </Button>
      </SheetTrigger>
      <SheetContent className="w-[320px] bg-background border-border">
        <SheetHeader>
          <SheetTitle className="flex items-center gap-2 text-base">
            <Brain className="h-4 w-4 text-primary" />
            Engram Memory
          </SheetTitle>
        </SheetHeader>

        <div className="mt-6 space-y-6">
          {/* Active Session */}
          {session && (
            <div>
              <h3 className="text-xs font-semibold uppercase tracking-wider text-muted-foreground mb-3">
                Active Session
              </h3>
              <div className="rounded-lg border border-border bg-card p-3 space-y-2">
                <div className="flex items-center justify-between">
                  <span className="text-sm text-muted-foreground">Agent</span>
                  <Badge variant="outline" className="text-xs">
                    {session.agent}
                  </Badge>
                </div>
                <div className="flex items-center justify-between">
                  <span className="text-sm text-muted-foreground">Status</span>
                  <Badge
                    variant="outline"
                    className={`text-xs ${
                      session.running
                        ? "text-emerald-400 border-emerald-500/30"
                        : "text-muted-foreground"
                    }`}
                  >
                    {session.running ? "Running" : "Idle"}
                  </Badge>
                </div>
                <div className="text-xs text-muted-foreground truncate" title={session.repo}>
                  {session.repo}
                </div>
              </div>
            </div>
          )}

          <Separator />

          {/* Memory Stats */}
          {stats ? (
            <div>
              <div className="flex items-center justify-between mb-3">
                <h3 className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">
                  Memory Stats
                </h3>
                <Button
                  variant="ghost"
                  size="sm"
                  className="h-6 text-xs text-muted-foreground hover:text-foreground"
                  onClick={onRefresh}
                >
                  Refresh
                </Button>
              </div>

              <div className="rounded-lg border border-border bg-card p-3 space-y-1">
                <StatCard icon={Database} label="Total Memories" value={stats.total} />
                <StatCard
                  icon={Zap}
                  label="Short-term (SML)"
                  value={stats.sml_count}
                  color="text-yellow-400"
                />
                <StatCard
                  icon={Archive}
                  label="Long-term (LML)"
                  value={stats.lml_count}
                  color="text-emerald-400"
                />
                <Separator className="my-2" />
                <div className="space-y-1.5">
                  <div className="flex items-center justify-between text-sm">
                    <span className="text-muted-foreground flex items-center gap-2">
                      <Activity className="h-3.5 w-3.5 text-accent" />
                      Avg Strength
                    </span>
                    <span className="font-medium tabular-nums">
                      {stats.avg_strength.toFixed(3)}
                    </span>
                  </div>
                  <StrengthBar value={stats.avg_strength} />
                </div>
              </div>

              {/* Echo Encoding Stats */}
              {stats.echo_enabled && (
                <div className="mt-4">
                  <h4 className="text-xs font-semibold uppercase tracking-wider text-muted-foreground mb-2 flex items-center gap-1.5">
                    <Sparkles className="h-3 w-3" />
                    Echo Encoding
                  </h4>
                  <div className="rounded-lg border border-border bg-card p-3">
                    <div className="grid grid-cols-2 gap-2">
                      {(["deep", "medium", "shallow", "none"] as const).map((depth) => (
                        <div key={depth} className="text-center p-2 rounded bg-muted/50">
                          <div className="text-lg font-semibold tabular-nums">
                            {stats.echo_stats[depth]}
                          </div>
                          <div className="text-[10px] uppercase tracking-wider text-muted-foreground">
                            {depth}
                          </div>
                        </div>
                      ))}
                    </div>
                  </div>
                </div>
              )}

              {/* Memory Layer Visualization */}
              {stats.total > 0 && (
                <div className="mt-4">
                  <h4 className="text-xs font-semibold uppercase tracking-wider text-muted-foreground mb-2 flex items-center gap-1.5">
                    <Layers className="h-3 w-3" />
                    Layer Distribution
                  </h4>
                  <div className="flex h-3 rounded-full overflow-hidden bg-muted">
                    {stats.sml_count > 0 && (
                      <div
                        className="h-full transition-all duration-500"
                        style={{
                          width: `${(stats.sml_count / stats.total) * 100}%`,
                          background: "oklch(0.7 0.18 60)",
                        }}
                        title={`SML: ${stats.sml_count}`}
                      />
                    )}
                    {stats.lml_count > 0 && (
                      <div
                        className="h-full transition-all duration-500"
                        style={{
                          width: `${(stats.lml_count / stats.total) * 100}%`,
                          background: "oklch(0.72 0.15 195)",
                        }}
                        title={`LML: ${stats.lml_count}`}
                      />
                    )}
                  </div>
                  <div className="flex justify-between text-[10px] text-muted-foreground mt-1">
                    <span className="flex items-center gap-1">
                      <span className="w-2 h-2 rounded-full" style={{ background: "oklch(0.7 0.18 60)" }} />
                      SML {Math.round((stats.sml_count / stats.total) * 100)}%
                    </span>
                    <span className="flex items-center gap-1">
                      <span className="w-2 h-2 rounded-full" style={{ background: "oklch(0.72 0.15 195)" }} />
                      LML {Math.round((stats.lml_count / stats.total) * 100)}%
                    </span>
                  </div>
                </div>
              )}
            </div>
          ) : (
            <div className="text-center py-8 text-sm text-muted-foreground">
              <Brain className="h-8 w-8 mx-auto mb-2 opacity-30" />
              <p>No memory data available</p>
              <p className="text-xs mt-1">Start a session to see stats</p>
            </div>
          )}
        </div>
      </SheetContent>
    </Sheet>
  );
}
