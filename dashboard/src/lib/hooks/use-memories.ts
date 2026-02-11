import useSWR from "swr";
import { fetcher } from "../api/client";
import type { MemoryListResponse } from "../types/memory";

export function useMemories(params?: { layer?: string; limit?: number }) {
  const q = new URLSearchParams();
  if (params?.layer && params.layer !== "all") q.set("layer", params.layer);
  if (params?.limit) q.set("limit", String(params.limit));
  const qs = q.toString();
  return useSWR<MemoryListResponse>(
    `/v1/memories${qs ? `?${qs}` : ""}`,
    fetcher
  );
}
