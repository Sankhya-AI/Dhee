import { get } from "./client";
import type { Stats } from "../types/stats";

export function getStats(params?: {
  user_id?: string;
  agent_id?: string;
}): Promise<Stats> {
  const q = new URLSearchParams();
  if (params?.user_id) q.set("user_id", params.user_id);
  if (params?.agent_id) q.set("agent_id", params.agent_id);
  const qs = q.toString();
  return get(`/v1/stats${qs ? `?${qs}` : ""}`);
}
