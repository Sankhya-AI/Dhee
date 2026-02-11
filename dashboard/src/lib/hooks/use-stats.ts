import useSWR from "swr";
import { fetcher } from "../api/client";
import type { Stats } from "../types/stats";

export function useStats() {
  return useSWR<Stats>("/v1/stats", fetcher);
}
