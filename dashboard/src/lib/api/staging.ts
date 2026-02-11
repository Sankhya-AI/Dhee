import { get, post } from "./client";
import type { StagingCommit } from "../types/staging";

export function listStagingCommits(params?: {
  user_id?: string;
  agent_id?: string;
  status?: string;
  limit?: number;
}): Promise<{ commits: StagingCommit[] }> {
  const q = new URLSearchParams();
  if (params?.user_id) q.set("user_id", params.user_id);
  if (params?.agent_id) q.set("agent_id", params.agent_id);
  if (params?.status) q.set("status", params.status);
  if (params?.limit) q.set("limit", String(params.limit));
  const qs = q.toString();
  return get(`/v1/staging/commits${qs ? `?${qs}` : ""}`);
}

export function approveCommit(id: string): Promise<Record<string, unknown>> {
  return post(`/v1/staging/commits/${id}/approve`);
}

export function rejectCommit(
  id: string,
  reason: string
): Promise<Record<string, unknown>> {
  return post(`/v1/staging/commits/${id}/reject`, { reason });
}
