import useSWR from "swr";
import { fetcher } from "../api/client";
import type { Profile } from "../types/profile";

export function useProfiles() {
  return useSWR<{ profiles: Profile[] }>("/v1/profiles", fetcher);
}
