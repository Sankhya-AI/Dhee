import { get } from "./client";

export interface DecayLogEntry {
  timestamp: string;
  decayed: number;
  forgotten: number;
  promoted: number;
}

export function getDecayLog(limit = 20): Promise<{ entries: DecayLogEntry[] }> {
  return get(`/v1/decay-log?limit=${limit}`);
}
