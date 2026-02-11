import useSWR from "swr";
import { fetcher } from "../api/client";
import type { Scene } from "../types/scene";

export function useScenes(limit = 50) {
  return useSWR<{ scenes: Scene[] }>(`/v1/scenes?limit=${limit}`, fetcher);
}
