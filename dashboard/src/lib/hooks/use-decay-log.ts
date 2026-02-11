import useSWR from "swr";
import { fetcher } from "../api/client";
import type { DecayLogEntry } from "../api/decay";

export function useDecayLog(limit = 20) {
  return useSWR<{ entries: DecayLogEntry[] }>(
    `/v1/decay-log?limit=${limit}`,
    fetcher
  );
}
