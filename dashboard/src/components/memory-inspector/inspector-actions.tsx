"use client";

import { useState } from "react";
import { ArrowUp, ArrowDown, Trash2, Pencil } from "lucide-react";
import { promoteMemory, demoteMemory, deleteMemory, updateMemory } from "@/lib/api/memories";
import { useInspectorStore } from "@/lib/stores/inspector-store";
import type { Memory } from "@/lib/types/memory";
import type { KeyedMutator } from "swr";
import { NEURAL } from "@/lib/utils/neural-palette";

export function InspectorActions({
  memory,
  onMutate,
}: {
  memory: Memory;
  onMutate: KeyedMutator<Memory>;
}) {
  const close = useInspectorStore((s) => s.close);
  const [editing, setEditing] = useState(false);
  const [editContent, setEditContent] = useState(memory.content);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [loading, setLoading] = useState(false);

  const handlePromote = async () => {
    setLoading(true);
    await promoteMemory(memory.id);
    await onMutate();
    setLoading(false);
  };

  const handleDemote = async () => {
    setLoading(true);
    await demoteMemory(memory.id);
    await onMutate();
    setLoading(false);
  };

  const handleDelete = async () => {
    setLoading(true);
    await deleteMemory(memory.id);
    setLoading(false);
    close();
  };

  const handleSaveEdit = async () => {
    setLoading(true);
    await updateMemory(memory.id, { content: editContent });
    await onMutate();
    setEditing(false);
    setLoading(false);
  };

  if (editing) {
    return (
      <div className="border-t p-4 space-y-3" style={{ borderColor: 'rgba(124,58,237,0.12)' }}>
        <textarea
          value={editContent}
          onChange={(e) => setEditContent(e.target.value)}
          className="w-full rounded-lg p-2 text-sm focus:outline-none focus:ring-1 focus:ring-purple-500"
          style={{
            backgroundColor: NEURAL.synapse,
            color: '#e2e8f0',
            border: `1px solid rgba(124,58,237,0.15)`,
          }}
          rows={3}
        />
        <div className="flex gap-2">
          <button
            onClick={handleSaveEdit}
            disabled={loading}
            className="rounded-lg bg-purple-600 px-3 py-1.5 text-xs font-medium text-white hover:bg-purple-700 disabled:opacity-50"
          >
            Save
          </button>
          <button
            onClick={() => { setEditing(false); setEditContent(memory.content); }}
            className="rounded-lg px-3 py-1.5 text-xs font-medium hover:bg-white/[0.05]"
            style={{ color: NEURAL.shallow, border: `1px solid rgba(124,58,237,0.15)` }}
          >
            Cancel
          </button>
        </div>
      </div>
    );
  }

  if (confirmDelete) {
    return (
      <div className="border-t p-4" style={{ borderColor: 'rgba(124,58,237,0.12)' }}>
        <p className="text-sm text-slate-300 mb-3">
          Delete this memory? This action cannot be undone.
        </p>
        <div className="flex gap-2">
          <button
            onClick={handleDelete}
            disabled={loading}
            className="rounded-lg bg-red-600 px-3 py-1.5 text-xs font-medium text-white hover:bg-red-700 disabled:opacity-50"
          >
            Confirm Delete
          </button>
          <button
            onClick={() => setConfirmDelete(false)}
            className="rounded-lg px-3 py-1.5 text-xs font-medium hover:bg-white/[0.05]"
            style={{ color: NEURAL.shallow, border: `1px solid rgba(124,58,237,0.15)` }}
          >
            Cancel
          </button>
        </div>
      </div>
    );
  }

  return (
    <div className="border-t px-4 py-3 flex items-center gap-2" style={{ borderColor: 'rgba(124,58,237,0.12)' }}>
      <button
        onClick={() => setEditing(true)}
        className="inline-flex items-center gap-1.5 rounded-lg px-2.5 py-1.5 text-xs font-medium hover:bg-white/[0.05] transition-colors"
        style={{ color: NEURAL.shallow, border: `1px solid rgba(124,58,237,0.15)` }}
      >
        <Pencil className="h-3 w-3" /> Edit
      </button>
      {memory.layer === "sml" ? (
        <button
          onClick={handlePromote}
          disabled={loading}
          className="inline-flex items-center gap-1.5 rounded-lg px-2.5 py-1.5 text-xs font-medium disabled:opacity-50 transition-colors"
          style={{
            backgroundColor: `${NEURAL.lml}15`,
            color: NEURAL.lml,
            border: `1px solid ${NEURAL.lml}30`,
          }}
        >
          <ArrowUp className="h-3 w-3" /> Promote to LML
        </button>
      ) : (
        <button
          onClick={handleDemote}
          disabled={loading}
          className="inline-flex items-center gap-1.5 rounded-lg px-2.5 py-1.5 text-xs font-medium disabled:opacity-50 transition-colors"
          style={{
            backgroundColor: `${NEURAL.sml}15`,
            color: NEURAL.sml,
            border: `1px solid ${NEURAL.sml}30`,
          }}
        >
          <ArrowDown className="h-3 w-3" /> Demote to SML
        </button>
      )}
      <button
        onClick={() => setConfirmDelete(true)}
        className="ml-auto inline-flex items-center gap-1.5 rounded-lg px-2.5 py-1.5 text-xs font-medium transition-colors"
        style={{
          color: NEURAL.conflict,
          border: `1px solid ${NEURAL.conflict}30`,
        }}
      >
        <Trash2 className="h-3 w-3" /> Delete
      </button>
    </div>
  );
}
