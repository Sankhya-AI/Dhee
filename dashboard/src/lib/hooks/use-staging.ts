import useSWR from "swr";
import { fetcher } from "../api/client";
import type { StagingCommit } from "../types/staging";

export function useStaging(status?: string) {
  const q = status ? `?status=${status}` : "";
  return useSWR<{ commits: StagingCommit[] }>(`/v1/staging/commits${q}`, fetcher);
}
