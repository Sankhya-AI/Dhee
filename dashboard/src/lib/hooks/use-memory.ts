import useSWR from "swr";
import { fetcher } from "../api/client";
import type { Memory, MemoryHistoryEntry } from "../types/memory";

export function useMemory(id: string | null) {
  return useSWR<Memory>(id ? `/v1/memories/${id}` : null, fetcher);
}

export function useMemoryHistory(id: string | null) {
  return useSWR<MemoryHistoryEntry[]>(
    id ? `/v1/memories/${id}/history` : null,
    fetcher
  );
}
