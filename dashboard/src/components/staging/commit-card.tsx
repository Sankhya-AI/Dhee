"use client";

import { useState } from "react";
import { approveCommit, rejectCommit } from "@/lib/api/staging";
import type { StagingCommit } from "@/lib/types/staging";
import { timeAgo, truncate } from "@/lib/utils/format";
import { Check, X } from "lucide-react";

export function CommitCard({
  commit,
  onAction,
}: {
  commit: StagingCommit;
  onAction: () => void;
}) {
  const [loading, setLoading] = useState(false);
  const [showReject, setShowReject] = useState(false);
  const [reason, setReason] = useState("");

  const handleApprove = async () => {
    setLoading(true);
    await approveCommit(commit.id);
    setLoading(false);
    onAction();
  };

  const handleReject = async () => {
    setLoading(true);
    await rejectCommit(commit.id, reason);
    setLoading(false);
    setShowReject(false);
    onAction();
  };

  const isPending = commit.status === "PENDING";

  return (
    <div className="rounded-lg border border-gray-200 bg-white p-4">
      <div className="flex items-center justify-between mb-2">
        <div className="flex items-center gap-2">
          <span className="inline-flex rounded-full bg-gray-100 px-2 py-0.5 text-[10px] font-medium text-gray-600">
            {commit.agent_id}
          </span>
          <span
            className={cn(
              "inline-flex rounded-full px-2 py-0.5 text-[10px] font-semibold uppercase",
              commit.status === "PENDING"
                ? "bg-yellow-50 text-yellow-700"
                : commit.status === "APPROVED"
                ? "bg-green-50 text-green-700"
                : "bg-red-50 text-red-700"
            )}
          >
            {commit.status}
          </span>
        </div>
        <span className="text-xs text-gray-400">{timeAgo(commit.created_at)}</span>
      </div>

      <p className="text-sm text-gray-700 mb-3">{truncate(commit.content, 200)}</p>

      {showReject ? (
        <div className="space-y-2">
          <textarea
            value={reason}
            onChange={(e) => setReason(e.target.value)}
            placeholder="Rejection reason..."
            className="w-full rounded-md border border-gray-200 p-2 text-sm focus:border-purple-300 focus:outline-none focus:ring-1 focus:ring-purple-300"
            rows={2}
          />
          <div className="flex gap-2">
            <button
              onClick={handleReject}
              disabled={loading || !reason}
              className="rounded-md bg-red-600 px-3 py-1.5 text-xs font-medium text-white hover:bg-red-700 disabled:opacity-50"
            >
              Reject
            </button>
            <button
              onClick={() => setShowReject(false)}
              className="rounded-md border border-gray-200 px-3 py-1.5 text-xs font-medium text-gray-600 hover:bg-gray-50"
            >
              Cancel
            </button>
          </div>
        </div>
      ) : (
        isPending && (
          <div className="flex gap-2">
            <button
              onClick={handleApprove}
              disabled={loading}
              className="inline-flex items-center gap-1 rounded-md bg-green-600 px-3 py-1.5 text-xs font-medium text-white hover:bg-green-700 disabled:opacity-50"
            >
              <Check className="h-3 w-3" /> Approve
            </button>
            <button
              onClick={() => setShowReject(true)}
              disabled={loading}
              className="inline-flex items-center gap-1 rounded-md border border-red-200 px-3 py-1.5 text-xs font-medium text-red-600 hover:bg-red-50 disabled:opacity-50"
            >
              <X className="h-3 w-3" /> Reject
            </button>
          </div>
        )
      )}

      {commit.rejection_reason && (
        <p className="mt-2 text-xs text-red-500">
          Reason: {commit.rejection_reason}
        </p>
      )}
    </div>
  );
}

function cn(...classes: (string | false | null | undefined)[]): string {
  return classes.filter(Boolean).join(" ");
}
