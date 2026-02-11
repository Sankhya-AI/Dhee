import { get, post, put, del } from "./client";
import type { Memory, MemoryListResponse, MemoryHistoryEntry, MemoryUpdatePayload } from "../types/memory";

export function listMemories(params?: {
  user_id?: string;
  layer?: string;
  limit?: number;
}): Promise<MemoryListResponse> {
  const q = new URLSearchParams();
  if (params?.user_id) q.set("user_id", params.user_id);
  if (params?.layer) q.set("layer", params.layer);
  if (params?.limit) q.set("limit", String(params.limit));
  const qs = q.toString();
  return get(`/v1/memories${qs ? `?${qs}` : ""}`);
}

export function getMemory(id: string): Promise<Memory> {
  return get(`/v1/memories/${id}`);
}

export function updateMemory(id: string, payload: MemoryUpdatePayload): Promise<Memory> {
  return put(`/v1/memories/${id}`, payload);
}

export function deleteMemory(id: string): Promise<{ status: string; id: string }> {
  return del(`/v1/memories/${id}`);
}

export function getMemoryHistory(id: string): Promise<MemoryHistoryEntry[]> {
  return get(`/v1/memories/${id}/history`);
}

export function promoteMemory(id: string): Promise<{ status: string; id: string }> {
  return post(`/v1/memories/${id}/promote`);
}

export function demoteMemory(id: string): Promise<{ status: string; id: string }> {
  return post(`/v1/memories/${id}/demote`);
}

export function searchMemories(query: string, params?: {
  user_id?: string;
  limit?: number;
  categories?: string[];
}): Promise<{ results: Memory[]; count: number }> {
  return post("/v1/search", { query, ...params });
}
