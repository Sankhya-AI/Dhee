import { get } from "./client";
import type { Profile } from "../types/profile";

export function listProfiles(params?: {
  user_id?: string;
}): Promise<{ profiles: Profile[] }> {
  const q = new URLSearchParams();
  if (params?.user_id) q.set("user_id", params.user_id);
  const qs = q.toString();
  return get(`/v1/profiles${qs ? `?${qs}` : ""}`);
}
