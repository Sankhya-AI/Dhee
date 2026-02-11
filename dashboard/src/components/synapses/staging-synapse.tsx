"use client";

import { useState } from "react";
import { GitBranch, Check, X } from "lucide-react";
import { approveCommit, rejectCommit } from "@/lib/api/staging";
import type { StagingCommit } from "@/lib/types/staging";
import { NEURAL } from "@/lib/utils/neural-palette";
import { timeAgo } from "@/lib/utils/format";
import type { KeyedMutator } from "swr";

export function StagingSynapse({
  commit,
  onMutate,
}: {
  commit: StagingCommit;
  onMutate: KeyedMutator<{ commits: StagingCommit[] }>;
}) {
  const [loading, setLoading] = useState(false);
  const isPending = commit.status === "PENDING";

  const handleApprove = async () => {
    setLoading(true);
    try {
      await approveCommit(commit.id);
      await onMutate();
    } finally {
      setLoading(false);
    }
  };

  const handleReject = async () => {
    setLoading(true);
    try {
      await rejectCommit(commit.id, "Rejected from dashboard");
      await onMutate();
    } finally {
      setLoading(false);
    }
  };

  const statusColor = commit.status === "APPROVED" ? NEURAL.success :
    commit.status === "REJECTED" ? NEURAL.conflict : NEURAL.pending;

  return (
    <div
      className="glass p-4"
      style={isPending ? { borderColor: `${NEURAL.pending}20`, boxShadow: `0 0 15px ${NEURAL.pending}08` } : undefined}
    >
      <div className="flex items-start gap-3 mb-3">
        <GitBranch className="h-4 w-4 shrink-0 mt-0.5" style={{ color: statusColor }} />
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2">
            <span
              className="inline-flex rounded-full px-2 py-0.5 text-[10px] font-semibold uppercase ring-1"
              style={{
                color: statusColor,
                backgroundColor: `${statusColor}15`,
                borderColor: `${statusColor}30`,
              }}
            >
              {commit.status}
            </span>
            <span className="text-[10px]" style={{ color: NEURAL.forgotten }}>
              {timeAgo(commit.created_at)}
            </span>
            <span className="text-[10px]" style={{ color: NEURAL.shallow }}>
              from {commit.agent_id}
            </span>
          </div>
        </div>
      </div>

      <p className="text-sm text-slate-300 mb-3 line-clamp-3">{commit.content}</p>

      {isPending && (
        <div className="flex gap-2">
          <button
            onClick={handleApprove}
            disabled={loading}
            className="flex items-center gap-1 rounded-lg px-3 py-1.5 text-xs font-medium disabled:opacity-50 transition-colors"
            style={{
              color: NEURAL.success,
              border: `1px solid ${NEURAL.success}30`,
              backgroundColor: `${NEURAL.success}08`,
            }}
          >
            <Check className="h-3 w-3" /> Approve
          </button>
          <button
            onClick={handleReject}
            disabled={loading}
            className="flex items-center gap-1 rounded-lg px-2.5 py-1.5 text-xs font-medium disabled:opacity-50 transition-colors"
            style={{
              color: NEURAL.conflict,
              border: `1px solid ${NEURAL.conflict}30`,
              backgroundColor: `${NEURAL.conflict}08`,
            }}
          >
            <X className="h-3 w-3" /> Reject
          </button>
        </div>
      )}

      {commit.rejection_reason && (
        <p className="text-xs mt-2" style={{ color: NEURAL.conflict }}>
          Reason: {commit.rejection_reason}
        </p>
      )}
    </div>
  );
}
