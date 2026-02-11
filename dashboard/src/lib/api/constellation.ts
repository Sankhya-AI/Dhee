import { get } from "./client";
import type { ConstellationData } from "../types/constellation";

export function getConstellationData(params?: {
  user_id?: string;
  limit?: number;
}): Promise<ConstellationData> {
  const q = new URLSearchParams();
  if (params?.user_id) q.set("user_id", params.user_id);
  if (params?.limit) q.set("limit", String(params.limit));
  const qs = q.toString();
  return get(`/v1/dashboard/constellation${qs ? `?${qs}` : ""}`);
}
