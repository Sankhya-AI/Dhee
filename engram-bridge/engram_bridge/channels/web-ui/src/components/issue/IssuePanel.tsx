import { useState, useCallback, useEffect, useRef } from "react";
import { useNavigate } from "react-router-dom";
import { X, Trash2, Plus, Send, MessageSquare } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { Badge } from "@/components/ui/badge";
import { useProjectContext } from "@/contexts/ProjectContext";
import { PriorityIcon } from "@/components/primitives/PriorityIcon";
import { StatusDot } from "@/components/primitives/StatusDot";
import { UserAvatar } from "@/components/primitives/UserAvatar";
import type { Issue, Priority } from "@/types";

interface Props {
  issue?: Issue;
  createMode?: boolean;
  defaultStatusId?: string | null;
  onClose: () => void;
  onIssueChange?: (issue: Issue) => void;
}

const PRIORITIES: Priority[] = ["urgent", "high", "medium", "low"];

export function IssuePanel({ issue, createMode, defaultStatusId, onClose, onIssueChange }: Props) {
  const { statuses, tags, createIssue, updateIssue, deleteIssue, addComment, createTag } = useProjectContext();

  const [title, setTitle] = useState(issue?.title || "");
  const [description, setDescription] = useState(issue?.description || "");
  const [priority, setPriority] = useState<Priority>(issue?.priority as Priority || "medium");
  const [statusId, setStatusId] = useState(issue?.status_id || defaultStatusId || statuses[0]?.id || "");
  const [assigneeIds, setAssigneeIds] = useState<string[]>(issue?.assignee_ids || []);
  const [tagIds, setTagIds] = useState<string[]>(issue?.tag_ids || []);
  const [commentText, setCommentText] = useState("");
  const [newAssignee, setNewAssignee] = useState("");
  const [saving, setSaving] = useState(false);

  const navigate = useNavigate();
  const titleRef = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    if (createMode) titleRef.current?.focus();
  }, [createMode]);

  // Refresh panel when issue changes externally
  useEffect(() => {
    if (issue) {
      setTitle(issue.title);
      setDescription(issue.description);
      setPriority(issue.priority as Priority);
      setStatusId(issue.status_id || "");
      setAssigneeIds(issue.assignee_ids);
      setTagIds(issue.tag_ids);
    }
  }, [issue]);

  const handleSave = useCallback(async () => {
    if (!title.trim()) return;
    setSaving(true);
    try {
      if (createMode) {
        const created = await createIssue({
          title: title.trim(),
          description,
          priority,
          status_id: statusId,
          assignee_ids: assigneeIds,
          tag_ids: tagIds,
        });
        onIssueChange?.(created);
        onClose();
      } else if (issue) {
        const updated = await updateIssue(issue.id, {
          title: title.trim(),
          description,
          priority,
          status_id: statusId,
          assignee_ids: assigneeIds,
          tag_ids: tagIds,
        });
        onIssueChange?.(updated);
      }
    } finally {
      setSaving(false);
    }
  }, [title, description, priority, statusId, assigneeIds, tagIds, createMode, issue, createIssue, updateIssue, onIssueChange, onClose]);

  const handleDelete = useCallback(async () => {
    if (!issue) return;
    await deleteIssue(issue.id);
    onClose();
  }, [issue, deleteIssue, onClose]);

  const handleAddComment = useCallback(async () => {
    if (!issue || !commentText.trim()) return;
    await addComment(issue.id, commentText.trim());
    setCommentText("");
    // Refresh
    const { getIssue } = await import("@/hooks/use-api").then(m => ({ getIssue: m.api.getIssue }));
    const updated = await getIssue(issue.id);
    onIssueChange?.(updated);
  }, [issue, commentText, addComment, onIssueChange]);

  const handleAddAssignee = useCallback(() => {
    if (newAssignee.trim() && !assigneeIds.includes(newAssignee.trim())) {
      setAssigneeIds(prev => [...prev, newAssignee.trim()]);
      setNewAssignee("");
    }
  }, [newAssignee, assigneeIds]);

  const toggleTag = useCallback((tagId: string) => {
    setTagIds(prev =>
      prev.includes(tagId) ? prev.filter(t => t !== tagId) : [...prev, tagId]
    );
  }, []);

  const currentStatus = statuses.find(s => s.id === statusId);

  // Auto-save on field changes (debounced for edit mode)
  useEffect(() => {
    if (createMode || !issue) return;
    const timer = setTimeout(() => {
      if (
        title !== issue.title ||
        description !== issue.description ||
        priority !== issue.priority ||
        statusId !== issue.status_id ||
        JSON.stringify(assigneeIds) !== JSON.stringify(issue.assignee_ids) ||
        JSON.stringify(tagIds) !== JSON.stringify(issue.tag_ids)
      ) {
        handleSave();
      }
    }, 800);
    return () => clearTimeout(timer);
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [title, description, priority, statusId, assigneeIds, tagIds]);

  return (
    <div className="fixed inset-0 z-50 flex justify-end">
      {/* Backdrop */}
      <div className="absolute inset-0 bg-black/50" onClick={onClose} />

      {/* Panel */}
      <div className="relative w-full max-w-lg bg-background border-l border-border shadow-xl flex flex-col overflow-hidden">
        {/* Header */}
        <div className="flex items-center justify-between px-4 py-3 border-b border-border/50">
          <div className="flex items-center gap-2">
            {issue?.issue_number ? (
              <span className="text-xs font-mono text-muted-foreground">#{issue.issue_number}</span>
            ) : (
              <span className="text-xs text-muted-foreground">New Issue</span>
            )}
          </div>
          <div className="flex items-center gap-1">
            {!createMode && issue && (
              <Button
                variant="ghost"
                size="sm"
                onClick={() => { onClose(); navigate(`/task/${issue.id}`); }}
                className="h-7 gap-1 px-2 text-xs text-muted-foreground hover:text-foreground"
                title="Open task chat"
              >
                <MessageSquare className="h-3.5 w-3.5" />
                Chat
              </Button>
            )}
            {!createMode && issue && (
              <Button variant="ghost" size="sm" onClick={handleDelete} className="h-7 w-7 p-0 text-destructive hover:text-destructive">
                <Trash2 className="h-3.5 w-3.5" />
              </Button>
            )}
            <Button variant="ghost" size="sm" onClick={onClose} className="h-7 w-7 p-0">
              <X className="h-4 w-4" />
            </Button>
          </div>
        </div>

        {/* Body */}
        <div className="flex-1 overflow-y-auto p-4 space-y-4">
          {/* Title */}
          <textarea
            ref={titleRef}
            value={title}
            onChange={e => setTitle(e.target.value)}
            placeholder="Issue title"
            rows={1}
            className="w-full text-lg font-semibold bg-transparent border-none resize-none focus:outline-none placeholder:text-muted-foreground/50"
            onKeyDown={e => {
              if (e.key === "Enter") {
                e.preventDefault();
                if (createMode) handleSave();
              }
            }}
          />

          {/* Properties */}
          <div className="space-y-2">
            {/* Status */}
            <PropertyRow label="Status">
              <div className="flex flex-wrap gap-1">
                {statuses.map(s => (
                  <button
                    key={s.id}
                    onClick={() => setStatusId(s.id)}
                    className={`flex items-center gap-1.5 text-xs px-2 py-1 rounded-md transition-colors ${
                      statusId === s.id
                        ? "bg-primary/10 text-primary border border-primary/30"
                        : "bg-muted/50 text-muted-foreground hover:bg-muted"
                    }`}
                  >
                    <StatusDot color={s.color} size={6} />
                    {s.name}
                  </button>
                ))}
              </div>
            </PropertyRow>

            {/* Priority */}
            <PropertyRow label="Priority">
              <div className="flex gap-1">
                {PRIORITIES.map(p => (
                  <button
                    key={p}
                    onClick={() => setPriority(p)}
                    className={`flex items-center gap-1 text-xs px-2 py-1 rounded-md transition-colors ${
                      priority === p
                        ? "bg-primary/10 text-primary border border-primary/30"
                        : "bg-muted/50 text-muted-foreground hover:bg-muted"
                    }`}
                  >
                    <PriorityIcon priority={p} className="h-3 w-3" />
                    {p.charAt(0).toUpperCase() + p.slice(1)}
                  </button>
                ))}
              </div>
            </PropertyRow>

            {/* Assignees */}
            <PropertyRow label="Assignees">
              <div className="flex flex-wrap items-center gap-1">
                {assigneeIds.map(a => (
                  <Badge
                    key={a}
                    variant="secondary"
                    className="gap-1 text-xs cursor-pointer"
                    onClick={() => setAssigneeIds(prev => prev.filter(id => id !== a))}
                  >
                    <UserAvatar name={a} size="xs" />
                    {a}
                    <X className="h-2.5 w-2.5" />
                  </Badge>
                ))}
                <div className="flex items-center gap-1">
                  <Input
                    value={newAssignee}
                    onChange={e => setNewAssignee(e.target.value)}
                    placeholder="Add..."
                    className="h-6 w-24 text-xs bg-transparent border-none px-1"
                    onKeyDown={e => { if (e.key === "Enter") { e.preventDefault(); handleAddAssignee(); } }}
                  />
                  {newAssignee && (
                    <button onClick={handleAddAssignee} className="text-primary">
                      <Plus className="h-3 w-3" />
                    </button>
                  )}
                </div>
              </div>
            </PropertyRow>

            {/* Tags */}
            <PropertyRow label="Tags">
              <div className="flex flex-wrap gap-1">
                {tags.map(t => (
                  <button
                    key={t.id}
                    onClick={() => toggleTag(t.id)}
                    className={`text-[11px] px-2 py-0.5 rounded-full transition-colors ${
                      tagIds.includes(t.id) ? "ring-1 ring-offset-1 ring-offset-background" : "opacity-60 hover:opacity-100"
                    }`}
                    style={{
                      backgroundColor: `${t.color}20`,
                      color: t.color,
                      borderColor: t.color,
                      ...(tagIds.includes(t.id) ? { ringColor: t.color } : {}),
                    }}
                  >
                    {t.name}
                  </button>
                ))}
              </div>
            </PropertyRow>
          </div>

          {/* Description */}
          <div>
            <label className="text-xs text-muted-foreground mb-1 block">Description</label>
            <Textarea
              value={description}
              onChange={e => setDescription(e.target.value)}
              placeholder="Add a description..."
              className="min-h-[80px] text-sm bg-muted/20 border-border/40"
            />
          </div>

          {/* Create button (only in create mode) */}
          {createMode && (
            <Button onClick={handleSave} disabled={!title.trim() || saving} className="w-full">
              {saving ? "Creating..." : "Create Issue"}
            </Button>
          )}

          {/* Comments (edit mode only) */}
          {!createMode && issue && (
            <div className="border-t border-border/30 pt-4">
              <h3 className="text-xs font-medium text-muted-foreground mb-3">Comments</h3>
              <div className="space-y-3">
                {issue.comments.map((c, i) => (
                  <div key={c.id || i} className="flex gap-2">
                    <UserAvatar name={c.agent} size="sm" />
                    <div className="flex-1">
                      <div className="flex items-center gap-2">
                        <span className="text-xs font-medium">{c.agent}</span>
                        <span className="text-[10px] text-muted-foreground">
                          {new Date(c.timestamp).toLocaleString()}
                        </span>
                      </div>
                      <p className="text-sm mt-0.5">{c.text}</p>
                      {c.reactions && c.reactions.length > 0 && (
                        <div className="flex gap-1 mt-1">
                          {c.reactions.map((r, j) => (
                            <span key={j} className="text-xs bg-muted/50 px-1.5 py-0.5 rounded">
                              {r.emoji}
                            </span>
                          ))}
                        </div>
                      )}
                    </div>
                  </div>
                ))}
              </div>

              {/* Add comment */}
              <div className="flex gap-2 mt-3">
                <Input
                  value={commentText}
                  onChange={e => setCommentText(e.target.value)}
                  placeholder="Add a comment..."
                  className="text-sm h-8"
                  onKeyDown={e => { if (e.key === "Enter") { e.preventDefault(); handleAddComment(); } }}
                />
                <Button size="sm" variant="ghost" onClick={handleAddComment} disabled={!commentText.trim()} className="h-8">
                  <Send className="h-3.5 w-3.5" />
                </Button>
              </div>
            </div>
          )}

          {/* Relationships (edit mode) */}
          {!createMode && issue && issue.relationships.length > 0 && (
            <div className="border-t border-border/30 pt-4">
              <h3 className="text-xs font-medium text-muted-foreground mb-2">Relationships</h3>
              <div className="space-y-1">
                {issue.relationships.map((r, i) => (
                  <div key={i} className="flex items-center gap-2 text-xs text-muted-foreground">
                    <Badge variant="outline" className="text-[10px]">{r.type}</Badge>
                    <span className="font-mono">{r.related_task_id.slice(0, 8)}</span>
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

function PropertyRow({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex items-start gap-3">
      <span className="text-xs text-muted-foreground w-20 pt-1 flex-shrink-0">{label}</span>
      <div className="flex-1">{children}</div>
    </div>
  );
}
