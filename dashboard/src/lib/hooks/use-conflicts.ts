import useSWR from "swr";
import { fetcher } from "../api/client";
import type { Conflict } from "../types/conflict";

export function useConflicts(resolution?: string) {
  const q = resolution ? `?resolution=${resolution}` : "";
  return useSWR<{ conflicts: Conflict[] }>(`/v1/conflicts${q}`, fetcher);
}
