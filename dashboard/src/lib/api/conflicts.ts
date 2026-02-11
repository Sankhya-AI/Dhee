import { get, post } from "./client";
import type { Conflict } from "../types/conflict";

export function listConflicts(params?: {
  user_id?: string;
  resolution?: string;
  limit?: number;
}): Promise<{ conflicts: Conflict[] }> {
  const q = new URLSearchParams();
  if (params?.user_id) q.set("user_id", params.user_id);
  if (params?.resolution) q.set("resolution", params.resolution);
  if (params?.limit) q.set("limit", String(params.limit));
  const qs = q.toString();
  return get(`/v1/conflicts${qs ? `?${qs}` : ""}`);
}

export function resolveConflict(
  stashId: string,
  resolution: string
): Promise<Record<string, unknown>> {
  return post(`/v1/conflicts/${stashId}/resolve`, { resolution });
}
