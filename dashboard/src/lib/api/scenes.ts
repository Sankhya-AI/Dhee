import { get } from "./client";
import type { Scene } from "../types/scene";

export function listScenes(params?: {
  user_id?: string;
  topic?: string;
  limit?: number;
}): Promise<{ scenes: Scene[] }> {
  const q = new URLSearchParams();
  if (params?.user_id) q.set("user_id", params.user_id);
  if (params?.topic) q.set("topic", params.topic);
  if (params?.limit) q.set("limit", String(params.limit));
  const qs = q.toString();
  return get(`/v1/scenes${qs ? `?${qs}` : ""}`);
}

export function getScene(id: string): Promise<Scene> {
  return get(`/v1/scenes/${id}`);
}
