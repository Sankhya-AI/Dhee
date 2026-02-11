import useSWR from "swr";
import { fetcher } from "../api/client";
import type { ConstellationData } from "../types/constellation";

export function useConstellation(limit = 1000) {
  return useSWR<ConstellationData>(
    `/v1/dashboard/constellation?limit=${limit}`,
    fetcher
  );
}
