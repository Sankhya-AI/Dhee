import { useLocation, useNavigate } from "react-router-dom";
import {
  Wifi,
  WifiOff,
  Loader2,
  MessageSquare,
  ListTodo,
  LayoutDashboard,
  Database,
  Network,
  ChevronDown,
  Settings,
} from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { useProjectContext } from "@/contexts/ProjectContext";
import { useWsContext } from "@/contexts/WebSocketContext";
import { StatusDot } from "@/components/primitives/StatusDot";

interface Props {
  onOpenSettings: () => void;
}

const NAV_TABS = [
  { path: "/", label: "Chat", icon: MessageSquare },
  { path: "/todos", label: "Todos", icon: ListTodo },
  { path: "/board", label: "Board", icon: LayoutDashboard },
  { path: "/memory", label: "Memory", icon: Database },
  { path: "/coordination", label: "Agents", icon: Network },
] as const;

export function AppBar({ onOpenSettings }: Props) {
  const { projects, currentProject, selectProject } = useProjectContext();
  const { status: connectionStatus } = useWsContext();
  const location = useLocation();
  const navigate = useNavigate();

  const activeTab = NAV_TABS.find(
    (t) =>
      t.path === "/"
        ? location.pathname === "/"
        : location.pathname === t.path || location.pathname.startsWith(t.path + "/"),
  ) || NAV_TABS[0];

  const statusIcon =
    connectionStatus === "connected" ? <Wifi className="h-3 w-3" /> :
    connectionStatus === "connecting" ? <Loader2 className="h-3 w-3 animate-spin" /> :
    <WifiOff className="h-3 w-3" />;

  const statusColor =
    connectionStatus === "connected" ? "bg-emerald-50 text-emerald-700 border-emerald-200" :
    connectionStatus === "connecting" ? "bg-amber-50 text-amber-700 border-amber-200" :
    "bg-red-50 text-red-700 border-red-200";

  return (
    <header className="flex items-center justify-between px-4 py-2.5 border-b border-border bg-background">
      <div className="flex items-center gap-4">
        {/* Logo */}
        <a
          href="/"
          onClick={(e) => { e.preventDefault(); navigate("/"); }}
          className="flex items-center hover:opacity-80 transition-opacity"
        >
          <span
            className="engram-gradient-text font-bold tracking-[-0.025em]"
            style={{ fontSize: "1.35rem", lineHeight: "1.5rem", fontFamily: "'Space Grotesk', sans-serif" }}
          >
            engram
          </span>
        </a>

        {/* Project selector */}
        {currentProject && (
          <div className="relative group">
            <button className="flex items-center gap-2 px-2.5 py-1.5 rounded-md hover:bg-muted transition-colors">
              <StatusDot color={currentProject.color} size={8} />
              <span className="text-sm font-medium">{currentProject.name}</span>
              <ChevronDown className="h-3 w-3 text-muted-foreground" />
            </button>
            <div className="absolute top-full left-0 mt-1 w-48 bg-popover border border-border rounded-lg shadow-lg opacity-0 invisible group-hover:opacity-100 group-hover:visible transition-all z-50">
              <div className="py-1">
                {projects.map(p => (
                  <button
                    key={p.id}
                    onClick={() => selectProject(p.id)}
                    className={`flex items-center gap-2 w-full px-3 py-2 text-sm hover:bg-muted transition-colors ${
                      p.id === currentProject.id ? "text-primary font-medium" : ""
                    }`}
                  >
                    <StatusDot color={p.color} size={6} />
                    {p.name}
                  </button>
                ))}
              </div>
            </div>
          </div>
        )}

        {/* View tabs */}
        <div className="flex items-center bg-muted rounded-lg p-0.5">
          {NAV_TABS.map((tab) => {
            const Icon = tab.icon;
            const isActive = activeTab.path === tab.path;
            return (
              <button
                key={tab.path}
                onClick={() => navigate(tab.path)}
                className={`flex items-center gap-1.5 px-3 py-1.5 rounded-md text-xs font-medium transition-colors ${
                  isActive
                    ? "bg-background text-foreground shadow-sm"
                    : "text-muted-foreground hover:text-foreground"
                }`}
              >
                <Icon className="h-3.5 w-3.5" />
                {tab.label}
              </button>
            );
          })}
        </div>
      </div>

      {/* Right side */}
      <div className="flex items-center gap-2">
        <Badge variant="outline" className={`text-[11px] gap-1.5 ${statusColor}`}>
          {statusIcon}
          {connectionStatus}
        </Badge>
        <Button
          variant="ghost"
          size="icon"
          className="h-8 w-8 text-muted-foreground hover:text-foreground"
          onClick={onOpenSettings}
        >
          <Settings className="h-4 w-4" />
        </Button>
      </div>
    </header>
  );
}
