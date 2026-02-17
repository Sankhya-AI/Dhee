import { useState, useEffect, useCallback, useRef, useMemo } from "react";
import {
  Swords,
  Bot,
  User,
  Info,
  Send,
  Plus,
  RefreshCw,
  Gavel,
  Lightbulb,
  MessageSquare,
  CheckCircle2,
  X,
  ChevronRight,
  Shield,
  Crown,
  ArrowRight,
  HelpCircle,
  Eye,
  Loader2,
} from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { api } from "@/hooks/use-api";
import { useWarRoomStore } from "@/stores/useWarRoomStore";
import { renderMarkdown } from "@/lib/render-markdown";
import type { WarRoom, WarRoomState, WarRoomMessage } from "@/types";

// ── State badge colors ──

const STATE_STYLE: Record<WarRoomState, string> = {
  open: "bg-zinc-100 text-zinc-600 border-zinc-200",
  discussing: "bg-blue-50 text-blue-700 border-blue-200",
  deciding: "bg-amber-50 text-amber-700 border-amber-200",
  decided: "bg-emerald-50 text-emerald-700 border-emerald-200",
  delivering: "bg-purple-50 text-purple-700 border-purple-200",
  closed: "bg-zinc-50 text-zinc-400 border-zinc-200",
};

// ── Directive parser ──
// Monitor messages can contain @delegate(agent, instruction), @ask(agent, question), @decide(text)

interface Directive {
  type: "delegate" | "ask" | "decide";
  agent?: string;
  text: string;
}

function parseDirectives(content: string): { plain: string; directives: Directive[] } {
  const directives: Directive[] = [];
  let plain = content;

  // @delegate(agent_name, instruction)
  plain = plain.replace(/@delegate\(\s*([\w-]+)\s*,\s*([\s\S]+?)\s*\)/g, (_, agent, text) => {
    directives.push({ type: "delegate", agent, text: text.trim() });
    return "";
  });

  // @ask(agent_name, question)
  plain = plain.replace(/@ask\(\s*([\w-]+)\s*,\s*([\s\S]+?)\s*\)/g, (_, agent, text) => {
    directives.push({ type: "ask", agent, text: text.trim() });
    return "";
  });

  // @decide(decision text)
  plain = plain.replace(/@decide\(\s*([\s\S]+?)\s*\)/g, (_, text) => {
    directives.push({ type: "decide", text: text.trim() });
    return "";
  });

  return { plain: plain.trim(), directives };
}

// ── Directive badge component ──

function DirectiveBadge({ directive }: { directive: Directive }) {
  if (directive.type === "delegate") {
    return (
      <div className="flex items-start gap-2 my-1.5 p-2 rounded-md bg-blue-50 border border-blue-200">
        <ArrowRight className="h-3.5 w-3.5 text-blue-600 mt-0.5 shrink-0" />
        <div className="min-w-0">
          <div className="flex items-center gap-1.5 mb-0.5">
            <span className="text-[10px] font-semibold uppercase tracking-wider text-blue-600">Delegate</span>
            <Badge variant="outline" className="text-[9px] px-1 py-0 bg-blue-100 text-blue-700 border-blue-300">
              {directive.agent}
            </Badge>
          </div>
          <p className="text-xs text-blue-800 leading-relaxed">{directive.text}</p>
        </div>
      </div>
    );
  }

  if (directive.type === "ask") {
    return (
      <div className="flex items-start gap-2 my-1.5 p-2 rounded-md bg-violet-50 border border-violet-200">
        <HelpCircle className="h-3.5 w-3.5 text-violet-600 mt-0.5 shrink-0" />
        <div className="min-w-0">
          <div className="flex items-center gap-1.5 mb-0.5">
            <span className="text-[10px] font-semibold uppercase tracking-wider text-violet-600">Ask</span>
            <Badge variant="outline" className="text-[9px] px-1 py-0 bg-violet-100 text-violet-700 border-violet-300">
              {directive.agent}
            </Badge>
          </div>
          <p className="text-xs text-violet-800 leading-relaxed">{directive.text}</p>
        </div>
      </div>
    );
  }

  // decide
  return (
    <div className="flex items-start gap-2 my-1.5 p-2 rounded-md bg-emerald-50 border border-emerald-200">
      <Gavel className="h-3.5 w-3.5 text-emerald-600 mt-0.5 shrink-0" />
      <div className="min-w-0">
        <span className="text-[10px] font-semibold uppercase tracking-wider text-emerald-600">Decision</span>
        <p className="text-xs text-emerald-800 leading-relaxed mt-0.5">{directive.text}</p>
      </div>
    </div>
  );
}

// ── Room Card (left panel) ──

function RoomCard({
  room,
  selected,
  onClick,
}: {
  room: WarRoom;
  selected: boolean;
  onClick: () => void;
}) {
  const isDeciding = room.wr_state === "deciding";

  return (
    <button
      onClick={onClick}
      className={`w-full text-left p-3 border-b border-border/40 transition-colors ${
        selected ? "bg-accent" : "hover:bg-muted/50"
      }`}
    >
      <div className="flex items-center gap-2 mb-1">
        <div className="h-7 w-7 rounded-md bg-muted flex items-center justify-center shrink-0">
          <Swords className="h-3.5 w-3.5 text-muted-foreground" />
        </div>
        <div className="flex-1 min-w-0">
          <div className="text-sm font-medium truncate">{room.wr_topic}</div>
        </div>
        {isDeciding && (
          <span className="relative flex h-2 w-2 shrink-0">
            <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-amber-400 opacity-75" />
            <span className="relative inline-flex rounded-full h-2 w-2 bg-amber-500" />
          </span>
        )}
      </div>
      <div className="flex items-center gap-2 mt-1.5">
        <Badge
          variant="outline"
          className={`text-[10px] px-1.5 py-0 ${STATE_STYLE[room.wr_state] || STATE_STYLE.open}`}
        >
          {room.wr_state}
        </Badge>
        {room.wr_monitor_agent && (
          <span className="text-[10px] text-muted-foreground truncate flex items-center gap-0.5">
            <Crown className="h-2.5 w-2.5 text-amber-500" />
            {room.wr_monitor_agent}
          </span>
        )}
        <span className="text-[10px] text-muted-foreground ml-auto tabular-nums">
          <MessageSquare className="h-2.5 w-2.5 inline mr-0.5" />
          {room.wr_message_count || 0}
        </span>
      </div>
    </button>
  );
}

// ── Message Entry (center panel) ──

const MSG_TYPE_ICON: Record<string, typeof Bot> = {
  decision: Gavel,
  proposal: Lightbulb,
  system: Info,
  vote: CheckCircle2,
  action_item: ArrowRight,
};

function MessageEntry({ msg, monitorAgent }: { msg: WarRoomMessage; monitorAgent: string }) {
  const isSystem = msg.wrmsg_message_type === "system";
  const isDecision = msg.wrmsg_message_type === "decision";
  const isMonitor = msg.wrmsg_sender === monitorAgent && monitorAgent !== "";
  const isUser = msg.wrmsg_sender === "user";
  const Icon = MSG_TYPE_ICON[msg.wrmsg_message_type];

  const ts = msg.wrmsg_timestamp
    ? new Date(msg.wrmsg_timestamp).toLocaleTimeString([], {
        hour: "2-digit",
        minute: "2-digit",
      })
    : "";

  // Parse directives from monitor messages
  const { plain, directives } = useMemo(
    () => (isMonitor ? parseDirectives(msg.content) : { plain: msg.content, directives: [] }),
    [msg.content, isMonitor],
  );

  if (isSystem) {
    return (
      <div className="flex items-center justify-center gap-2 py-2 px-4">
        <div className="h-px flex-1 bg-border" />
        <span className="text-[11px] text-muted-foreground flex items-center gap-1">
          <Info className="h-3 w-3" />
          {msg.content}
        </span>
        <div className="h-px flex-1 bg-border" />
      </div>
    );
  }

  // Avatar ring color: amber for monitor, blue for user, default for agents
  const avatarRing = isMonitor
    ? "ring-2 ring-amber-400 ring-offset-1"
    : isUser
      ? "ring-2 ring-blue-400 ring-offset-1"
      : "";

  return (
    <div
      className={`flex items-start gap-2.5 px-4 py-2.5 hover:bg-muted/30 transition-colors ${
        isDecision ? "bg-emerald-50/50" : isMonitor ? "bg-amber-50/20" : ""
      }`}
    >
      <div className="mt-0.5 shrink-0">
        <div className={`h-6 w-6 rounded-full flex items-center justify-center relative ${avatarRing} ${
          isMonitor ? "bg-amber-100" : isUser ? "bg-blue-100" : "bg-muted"
        }`}>
          {Icon ? (
            <Icon className={`h-3 w-3 ${isDecision ? "text-emerald-600" : "text-amber-600"}`} />
          ) : isMonitor ? (
            <Crown className="h-3 w-3 text-amber-600" />
          ) : isUser ? (
            <User className="h-3 w-3 text-blue-600" />
          ) : (
            <Bot className="h-3 w-3 text-muted-foreground" />
          )}
        </div>
      </div>
      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-1.5 mb-0.5">
          <span className={`text-xs font-medium ${isMonitor ? "text-amber-700" : ""}`}>
            {msg.wrmsg_sender}
          </span>
          {isMonitor && (
            <Badge variant="outline" className="text-[9px] px-1 py-0 bg-amber-50 text-amber-700 border-amber-300">
              monitor
            </Badge>
          )}
          {msg.wrmsg_message_type !== "message" && (
            <Badge variant="outline" className="text-[9px] px-1 py-0">
              {msg.wrmsg_message_type}
            </Badge>
          )}
          <span className="text-[10px] text-muted-foreground">{ts}</span>
        </div>

        {/* Main content */}
        {plain && (
          <div
            className="text-sm leading-relaxed message-content [&_pre]:my-2 [&_code]:break-all"
            dangerouslySetInnerHTML={{ __html: renderMarkdown(plain) }}
          />
        )}

        {/* Parsed directives */}
        {directives.length > 0 && (
          <div className="mt-1">
            {directives.map((d, i) => (
              <DirectiveBadge key={i} directive={d} />
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

// ── Room Detail Panel (right panel) ──

function RoomDetailPanel({
  room,
  onTransition,
  onMonitorChange,
}: {
  room: WarRoom | null;
  onTransition: (state: string) => void;
  onMonitorChange: (agent: string) => void;
}) {
  const [monitorInput, setMonitorInput] = useState("");

  if (!room) {
    return (
      <div className="flex flex-col items-center justify-center h-full text-muted-foreground py-12">
        <Swords className="h-8 w-8 mb-2 opacity-30" />
        <p className="text-sm">Select a war room</p>
        <p className="text-xs mt-1">Click a room to view details</p>
      </div>
    );
  }

  const nextStates: Record<string, { label: string; target: string }[]> = {
    open: [{ label: "Start Discussion", target: "discussing" }],
    discussing: [{ label: "Call for Decision", target: "deciding" }],
    deciding: [{ label: "Back to Discussion", target: "discussing" }],
    decided: [{ label: "Begin Delivery", target: "delivering" }],
    delivering: [{ label: "Close Room", target: "closed" }],
    closed: [],
  };

  const transitions = nextStates[room.wr_state] || [];
  const isDeciding = room.wr_state === "deciding";

  return (
    <div className="p-4 overflow-y-auto h-full space-y-4">
      {/* Topic */}
      <div>
        <h3 className="text-sm font-semibold">{room.wr_topic}</h3>
        {room.wr_agenda && (
          <p className="text-xs text-muted-foreground mt-1 leading-relaxed">
            {room.wr_agenda}
          </p>
        )}
      </div>

      {/* Monitor — hub display */}
      <div className="border-t border-border pt-3">
        <h4 className="text-xs font-semibold uppercase tracking-wider text-muted-foreground mb-2">
          Monitor (Hub)
        </h4>
        {room.wr_monitor_agent ? (
          <div className="p-2.5 rounded-lg bg-amber-50 border border-amber-200 mb-2">
            <div className="flex items-center gap-2">
              <div className="h-8 w-8 rounded-full bg-amber-100 ring-2 ring-amber-400 flex items-center justify-center">
                <Crown className="h-4 w-4 text-amber-600" />
              </div>
              <div>
                <span className="text-sm font-semibold text-amber-800">{room.wr_monitor_agent}</span>
                <p className="text-[10px] text-amber-600">
                  {isDeciding ? "Synthesizing decision..." : "Coordinating agents"}
                </p>
              </div>
              {isDeciding && (
                <Loader2 className="h-3.5 w-3.5 text-amber-500 animate-spin ml-auto" />
              )}
            </div>

            {/* Hub-spoke topology */}
            {room.wr_participants && room.wr_participants.length > 0 && (
              <div className="mt-2 pt-2 border-t border-amber-200/60">
                <div className="flex flex-wrap gap-1">
                  {room.wr_participants
                    .filter((p) => p !== room.wr_monitor_agent)
                    .map((p) => (
                      <div
                        key={p}
                        className="flex items-center gap-1 px-1.5 py-0.5 rounded-full bg-white/80 border border-amber-200/60 text-[10px]"
                      >
                        <Bot className="h-2.5 w-2.5 text-muted-foreground" />
                        {p}
                      </div>
                    ))}
                </div>
                <p className="text-[9px] text-amber-500 mt-1.5 flex items-center gap-1">
                  <Eye className="h-2.5 w-2.5" />
                  Only monitor & user see all agents
                </p>
              </div>
            )}
          </div>
        ) : (
          <p className="text-xs text-muted-foreground mb-2">No monitor assigned</p>
        )}
        <div className="flex gap-1.5">
          <Input
            placeholder="Agent name..."
            value={monitorInput}
            onChange={(e) => setMonitorInput(e.target.value)}
            className="h-7 text-xs flex-1"
          />
          <Button
            variant="outline"
            size="sm"
            className="h-7 text-xs px-2"
            disabled={!monitorInput.trim()}
            onClick={() => {
              onMonitorChange(monitorInput.trim());
              setMonitorInput("");
            }}
          >
            Set
          </Button>
        </div>
      </div>

      {/* State */}
      <div className="border-t border-border pt-3">
        <h4 className="text-xs font-semibold uppercase tracking-wider text-muted-foreground mb-2">
          State
        </h4>
        <div className="flex items-center gap-2 mb-2">
          <Badge
            variant="outline"
            className={`text-[10px] ${STATE_STYLE[room.wr_state] || ""}`}
          >
            {room.wr_state}
          </Badge>
          {isDeciding && (
            <span className="relative flex h-2 w-2">
              <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-amber-400 opacity-75" />
              <span className="relative inline-flex rounded-full h-2 w-2 bg-amber-500" />
            </span>
          )}
        </div>
        <div className="space-y-1.5">
          {transitions.map((t) => (
            <Button
              key={t.target}
              variant="outline"
              size="sm"
              className="w-full h-7 text-xs gap-1.5 justify-start"
              onClick={() => onTransition(t.target)}
            >
              <ChevronRight className="h-3 w-3" />
              {t.label}
            </Button>
          ))}
        </div>
      </div>

      {/* Decision */}
      {room.wr_decision_text && (
        <div className="border-t border-border pt-3">
          <h4 className="text-xs font-semibold uppercase tracking-wider text-muted-foreground mb-2">
            Decision
          </h4>
          <div className="p-2.5 rounded-md bg-emerald-50 border border-emerald-200 text-sm leading-relaxed">
            <Gavel className="h-3.5 w-3.5 inline text-emerald-600 mr-1" />
            {room.wr_decision_text}
          </div>
          {room.wr_action_items && room.wr_action_items.length > 0 && (
            <div className="mt-2 space-y-1">
              <span className="text-[10px] font-medium text-muted-foreground uppercase tracking-wider">
                Action Items
              </span>
              {room.wr_action_items.map((item, i) => (
                <div
                  key={i}
                  className="flex items-center gap-2 px-2 py-1 rounded-md bg-muted/50 text-xs"
                >
                  <CheckCircle2 className="h-3 w-3 text-emerald-500 shrink-0" />
                  <span>{item}</span>
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      {/* Meta */}
      <div className="border-t border-border pt-3">
        <h4 className="text-xs font-semibold uppercase tracking-wider text-muted-foreground mb-2">
          Info
        </h4>
        <div className="space-y-1.5 text-sm">
          {room.wr_created_by && (
            <div className="flex justify-between">
              <span className="text-muted-foreground">Created by</span>
              <span className="text-xs">{room.wr_created_by}</span>
            </div>
          )}
          {room.wr_created_at && (
            <div className="flex justify-between">
              <span className="text-muted-foreground">Created</span>
              <span className="text-xs">
                {new Date(room.wr_created_at).toLocaleString()}
              </span>
            </div>
          )}
          {room.wr_task_id && (
            <div className="flex justify-between">
              <span className="text-muted-foreground">Task</span>
              <span className="text-xs font-mono">{room.wr_task_id.slice(0, 8)}</span>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

// ── Create Room Dialog (inline modal) ──

function CreateRoomDialog({
  open,
  onClose,
  onCreate,
}: {
  open: boolean;
  onClose: () => void;
  onCreate: (data: { topic: string; agenda?: string }) => void;
}) {
  const [topic, setTopic] = useState("");
  const [agenda, setAgenda] = useState("");

  if (!open) return null;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50">
      <div className="bg-background rounded-lg border border-border shadow-xl w-[420px] max-w-[90vw]">
        <div className="flex items-center justify-between px-4 py-3 border-b border-border">
          <h3 className="text-sm font-semibold">Create War Room</h3>
          <Button variant="ghost" size="icon" className="h-6 w-6" onClick={onClose}>
            <X className="h-3.5 w-3.5" />
          </Button>
        </div>
        <div className="p-4 space-y-3">
          <div>
            <label className="text-xs font-medium text-muted-foreground mb-1 block">
              Topic
            </label>
            <Input
              placeholder="What's this war room about?"
              value={topic}
              onChange={(e) => setTopic(e.target.value)}
              className="h-8 text-sm"
              autoFocus
            />
          </div>
          <div>
            <label className="text-xs font-medium text-muted-foreground mb-1 block">
              Agenda (optional)
            </label>
            <textarea
              placeholder="Describe the goals and scope..."
              value={agenda}
              onChange={(e) => setAgenda(e.target.value)}
              className="flex w-full rounded-md border border-input bg-background px-3 py-2 text-sm ring-offset-background placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 min-h-[80px] resize-none"
            />
          </div>
        </div>
        <div className="flex justify-end gap-2 px-4 py-3 border-t border-border">
          <Button variant="outline" size="sm" className="h-7 text-xs" onClick={onClose}>
            Cancel
          </Button>
          <Button
            size="sm"
            className="h-7 text-xs"
            disabled={!topic.trim()}
            onClick={() => {
              onCreate({ topic: topic.trim(), agenda: agenda.trim() || undefined });
              setTopic("");
              setAgenda("");
              onClose();
            }}
          >
            Create
          </Button>
        </div>
      </div>
    </div>
  );
}

// ── Main View ──

export function WarRoomView() {
  const { rooms, messages, selectedRoomId, setRooms, selectRoom, setMessages } =
    useWarRoomStore();

  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [createOpen, setCreateOpen] = useState(false);
  const [msgInput, setMsgInput] = useState("");
  const [sending, setSending] = useState(false);
  const scrollRef = useRef<HTMLDivElement>(null);

  const selectedRoom = rooms.find((r) => r.id === selectedRoomId) || null;
  const roomMessages = selectedRoomId ? messages[selectedRoomId] || [] : [];
  const monitorAgent = selectedRoom?.wr_monitor_agent || "";

  // Load rooms
  const refresh = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await api.warrooms();
      setRooms(data);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load war rooms");
    } finally {
      setLoading(false);
    }
  }, [setRooms]);

  useEffect(() => {
    refresh();
  }, [refresh]);

  // Load messages when room selected
  useEffect(() => {
    if (!selectedRoomId) return;
    api.warroomMessages(selectedRoomId).then((msgs) => {
      setMessages(selectedRoomId, msgs);
    }).catch(() => {});
  }, [selectedRoomId, setMessages]);

  // Auto-scroll on new messages
  useEffect(() => {
    if (scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
    }
  }, [roomMessages.length]);

  // Send message
  const handleSend = useCallback(async () => {
    if (!msgInput.trim() || !selectedRoomId) return;
    setSending(true);
    try {
      await api.postWarroomMessage(selectedRoomId, "user", msgInput.trim());
      setMsgInput("");
    } catch {
      // message may still arrive via WS
    } finally {
      setSending(false);
    }
  }, [msgInput, selectedRoomId]);

  // Create room
  const handleCreate = useCallback(
    async (data: { topic: string; agenda?: string }) => {
      try {
        const room = await api.createWarroom(data);
        useWarRoomStore.getState().addRoom(room);
        selectRoom(room.id);
      } catch {
        // ignore
      }
    },
    [selectRoom],
  );

  // Transition state
  const handleTransition = useCallback(
    async (newState: string) => {
      if (!selectedRoomId) return;
      try {
        await api.transitionWarroom(selectedRoomId, newState);
      } catch {
        // ignore
      }
    },
    [selectedRoomId],
  );

  // Change monitor
  const handleMonitorChange = useCallback(
    async (agentName: string) => {
      if (!selectedRoomId) return;
      try {
        await api.setWarroomMonitor(selectedRoomId, agentName);
      } catch {
        // ignore
      }
    },
    [selectedRoomId],
  );

  // 503 = war room not enabled
  const notEnabled = error?.includes("503");

  if (notEnabled) {
    return (
      <div className="flex flex-1 items-center justify-center">
        <div className="text-center space-y-3 max-w-sm">
          <Swords className="h-12 w-12 mx-auto text-muted-foreground/30" />
          <h2 className="text-lg font-semibold">War Room Not Enabled</h2>
          <p className="text-sm text-muted-foreground leading-relaxed">
            Add <code className="text-xs bg-muted px-1.5 py-0.5 rounded">{`"warroom": {"enabled": true}`}</code> to
            your <code className="text-xs bg-muted px-1.5 py-0.5 rounded">~/.engram/bridge.json</code> and
            restart the bridge.
          </p>
        </div>
      </div>
    );
  }

  // ── Empty state: full-page welcome ──
  if (rooms.length === 0 && !loading) {
    return (
      <div className="flex flex-1 items-center justify-center">
        <div className="max-w-lg w-full px-6 space-y-6">
          {/* Hero */}
          <div className="text-center space-y-3">
            <div className="mx-auto h-16 w-16 rounded-2xl bg-gradient-to-br from-amber-100 to-orange-100 border border-amber-200 flex items-center justify-center">
              <Swords className="h-8 w-8 text-amber-600" />
            </div>
            <h2 className="text-xl font-bold">War Room</h2>
            <p className="text-sm text-muted-foreground leading-relaxed max-w-sm mx-auto">
              Coordinate multiple AI agents on complex problems.
              A monitor agent leads the discussion, delegates to specialists,
              and drives toward a decision — while you observe and guide.
            </p>
          </div>

          {/* How it works */}
          <div className="grid grid-cols-3 gap-3">
            <div className="p-3 rounded-lg bg-muted/50 border border-border text-center">
              <div className="h-8 w-8 rounded-full bg-amber-100 flex items-center justify-center mx-auto mb-2">
                <Crown className="h-4 w-4 text-amber-600" />
              </div>
              <p className="text-xs font-medium">Monitor leads</p>
              <p className="text-[10px] text-muted-foreground mt-0.5">
                One agent coordinates all others
              </p>
            </div>
            <div className="p-3 rounded-lg bg-muted/50 border border-border text-center">
              <div className="h-8 w-8 rounded-full bg-blue-100 flex items-center justify-center mx-auto mb-2">
                <Bot className="h-4 w-4 text-blue-600" />
              </div>
              <p className="text-xs font-medium">Agents collaborate</p>
              <p className="text-[10px] text-muted-foreground mt-0.5">
                Specialists get delegated tasks
              </p>
            </div>
            <div className="p-3 rounded-lg bg-muted/50 border border-border text-center">
              <div className="h-8 w-8 rounded-full bg-emerald-100 flex items-center justify-center mx-auto mb-2">
                <Gavel className="h-4 w-4 text-emerald-600" />
              </div>
              <p className="text-xs font-medium">Decisions made</p>
              <p className="text-[10px] text-muted-foreground mt-0.5">
                Structured outcomes & action items
              </p>
            </div>
          </div>

          {/* CTA */}
          <div className="text-center">
            <Button
              size="sm"
              className="gap-2 h-9 px-5"
              onClick={() => setCreateOpen(true)}
            >
              <Plus className="h-4 w-4" />
              Create a War Room
            </Button>
            <p className="text-[10px] text-muted-foreground mt-2">
              Or type <code className="bg-muted px-1 py-0.5 rounded">/warroom</code> in the Chat tab
            </p>
          </div>
        </div>

        <CreateRoomDialog
          open={createOpen}
          onClose={() => setCreateOpen(false)}
          onCreate={handleCreate}
        />
      </div>
    );
  }

  // ── Rooms exist: 3-panel layout ──
  return (
    <div className="flex flex-1 overflow-hidden">
      {/* ── Left: Room List ── */}
      <div className="w-64 border-r border-border flex flex-col bg-sidebar overflow-hidden">
        <div className="flex items-center justify-between px-3 py-2.5 border-b border-border">
          <h3 className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">
            War Rooms
          </h3>
          <div className="flex items-center gap-1">
            <Badge variant="outline" className="text-[10px] px-1.5 py-0 tabular-nums">
              {rooms.length}
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
            <Button
              variant="ghost"
              size="icon"
              className="h-6 w-6"
              onClick={() => setCreateOpen(true)}
            >
              <Plus className="h-3 w-3" />
            </Button>
          </div>
        </div>

        <div className="flex-1 overflow-y-auto">
          {rooms.map((room) => (
            <RoomCard
              key={room.id}
              room={room}
              selected={selectedRoomId === room.id}
              onClick={() => selectRoom(room.id)}
            />
          ))}
        </div>
      </div>

      {/* ── Center: Chat Thread ── */}
      <div className="flex-1 flex flex-col overflow-hidden">
        {selectedRoom ? (
          <>
            {/* Header */}
            <div className="flex items-center gap-3 px-4 py-2.5 border-b border-border">
              <div className="flex-1 min-w-0">
                <div className="flex items-center gap-2">
                  <h3 className="text-sm font-semibold truncate">
                    {selectedRoom.wr_topic}
                  </h3>
                  <Badge
                    variant="outline"
                    className={`text-[10px] px-1.5 py-0 shrink-0 ${
                      STATE_STYLE[selectedRoom.wr_state] || ""
                    }`}
                  >
                    {selectedRoom.wr_state}
                  </Badge>
                  {selectedRoom.wr_state === "deciding" && (
                    <span className="relative flex h-2 w-2">
                      <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-amber-400 opacity-75" />
                      <span className="relative inline-flex rounded-full h-2 w-2 bg-amber-500" />
                    </span>
                  )}
                </div>
                {monitorAgent && (
                  <span className="text-[10px] text-muted-foreground flex items-center gap-1">
                    <Crown className="h-2.5 w-2.5 text-amber-500" />
                    {monitorAgent}
                    <span className="text-muted-foreground/50 mx-1">|</span>
                    <Eye className="h-2.5 w-2.5" />
                    You are observing
                  </span>
                )}
              </div>
            </div>

            {/* Messages */}
            <div ref={scrollRef} className="flex-1 overflow-y-auto">
              {roomMessages.length === 0 ? (
                <div className="flex flex-col items-center justify-center h-full text-muted-foreground py-12">
                  <MessageSquare className="h-8 w-8 mb-2 opacity-30" />
                  <p className="text-sm">No messages yet</p>
                  <p className="text-xs mt-1">Send a message to kick things off</p>
                </div>
              ) : (
                roomMessages.map((msg) => (
                  <MessageEntry key={msg.id} msg={msg} monitorAgent={monitorAgent} />
                ))
              )}
            </div>

            {/* Input */}
            {selectedRoom.wr_state !== "closed" && (
              <div className="border-t border-border p-3">
                <div className="flex gap-2">
                  <Input
                    placeholder="Type a message..."
                    value={msgInput}
                    onChange={(e) => setMsgInput(e.target.value)}
                    onKeyDown={(e) => {
                      if (e.key === "Enter" && !e.shiftKey) {
                        e.preventDefault();
                        handleSend();
                      }
                    }}
                    className="h-8 text-sm flex-1"
                    disabled={sending}
                  />
                  <Button
                    size="sm"
                    className="h-8 px-3"
                    disabled={!msgInput.trim() || sending}
                    onClick={handleSend}
                  >
                    <Send className="h-3.5 w-3.5" />
                  </Button>
                </div>
              </div>
            )}
          </>
        ) : (
          <div className="flex flex-col items-center justify-center h-full text-muted-foreground py-12">
            <Swords className="h-10 w-10 mb-3 opacity-20" />
            <p className="text-sm font-medium">Select a War Room</p>
            <p className="text-xs mt-1">Pick a room from the left to see the conversation</p>
          </div>
        )}
      </div>

      {/* ── Right: Room Detail ── */}
      <div className="w-72 border-l border-border flex flex-col overflow-hidden">
        <div className="px-3 py-2.5 border-b border-border">
          <h3 className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">
            Room Detail
          </h3>
        </div>
        <div className="flex-1 overflow-hidden">
          <RoomDetailPanel
            room={selectedRoom}
            onTransition={handleTransition}
            onMonitorChange={handleMonitorChange}
          />
        </div>
      </div>

      {/* Create dialog */}
      <CreateRoomDialog
        open={createOpen}
        onClose={() => setCreateOpen(false)}
        onCreate={handleCreate}
      />
    </div>
  );
}
