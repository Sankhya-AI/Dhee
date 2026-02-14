// ── REST API client for engram-bridge ──

import { useCallback, useEffect, useState } from "react";
import type {
  Project, ProjectStatus, ProjectTag, Issue, FeedEvent, SystemInfo,
  MemoryItem, MemoryStats, MemoryCategory,
  CoordinationAgent, CoordinationEvent, RouteResult,
} from "@/types";
import type { AgentInfo } from "@/types/dashboard";

const BASE = "";

async function json<T>(res: Response): Promise<T> {
  if (!res.ok) {
    const body = await res.text();
    throw new Error(`API ${res.status}: ${body}`);
  }
  return res.json() as Promise<T>;
}

function get<T>(path: string): Promise<T> {
  return fetch(`${BASE}${path}`).then(r => json<T>(r));
}

function post<T>(path: string, body?: unknown): Promise<T> {
  return fetch(`${BASE}${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: body ? JSON.stringify(body) : undefined,
  }).then(r => json<T>(r));
}

function put<T>(path: string, body: unknown): Promise<T> {
  return fetch(`${BASE}${path}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  }).then(r => json<T>(r));
}

function del<T>(path: string): Promise<T> {
  return fetch(`${BASE}${path}`, { method: "DELETE" }).then(r => json<T>(r));
}

export const api = {
  // System
  info: () => get<SystemInfo>("/api/info"),
  health: () => get<{ status: string }>("/health"),
  feed: () => get<FeedEvent[]>("/api/feed"),
  agents: () => get<unknown[]>("/api/agents"),

  // Projects
  listProjects: () => get<Project[]>("/api/projects"),
  createProject: (data: { name: string; color?: string; description?: string }) =>
    post<Project>("/api/projects", data),
  getProject: (id: string) => get<Project>(`/api/projects/${id}`),
  updateProject: (id: string, data: Partial<Project>) =>
    put<Project>(`/api/projects/${id}`, data),
  deleteProject: (id: string) => del<{ ok: boolean }>(`/api/projects/${id}`),

  // Statuses
  listStatuses: (projectId: string) =>
    get<ProjectStatus[]>(`/api/projects/${projectId}/statuses`),
  createStatus: (projectId: string, data: { name: string; color?: string; sort_order?: number }) =>
    post<ProjectStatus>(`/api/projects/${projectId}/statuses`, data),
  updateStatus: (statusId: string, data: Partial<ProjectStatus>) =>
    put<ProjectStatus>(`/api/statuses/${statusId}`, data),
  deleteStatus: (statusId: string) => del<{ ok: boolean }>(`/api/statuses/${statusId}`),
  bulkUpdateStatuses: (updates: Partial<ProjectStatus>[]) =>
    post<ProjectStatus[]>("/api/statuses/bulk", { updates }),

  // Tags
  listTags: (projectId: string) =>
    get<ProjectTag[]>(`/api/projects/${projectId}/tags`),
  createTag: (projectId: string, data: { name: string; color?: string }) =>
    post<ProjectTag>(`/api/projects/${projectId}/tags`, data),
  updateTag: (tagId: string, data: Partial<ProjectTag>) =>
    put<ProjectTag>(`/api/tags/${tagId}`, data),
  deleteTag: (tagId: string) => del<{ ok: boolean }>(`/api/tags/${tagId}`),

  // Issues
  listIssues: (projectId: string) =>
    get<Issue[]>(`/api/projects/${projectId}/issues`),
  createIssue: (data: Partial<Issue> & { title: string; project_id: string }) =>
    post<Issue>("/api/issues", data),
  getIssue: (id: string) => get<Issue>(`/api/issues/${id}`),
  updateIssue: (id: string, data: Partial<Issue>) =>
    put<Issue>(`/api/issues/${id}`, data),
  deleteIssue: (id: string) => del<{ ok: boolean }>(`/api/issues/${id}`),
  bulkUpdateIssues: (updates: Partial<Issue>[]) =>
    post<Issue[]>("/api/issues/bulk", { updates }),

  // Comments
  listComments: (issueId: string) => get<unknown[]>(`/api/issues/${issueId}/comments`),
  addComment: (issueId: string, agent: string, text: string) =>
    post<unknown>(`/api/issues/${issueId}/comments`, { agent, text }),

  // Relationships
  addRelationship: (issueId: string, relatedId: string, type: string) =>
    post<unknown>(`/api/issues/${issueId}/relationships`, { related_id: relatedId, type }),

  // Assignees
  addAssignee: (issueId: string, userId: string) =>
    post<unknown>(`/api/issues/${issueId}/assignees`, { user_id: userId }),
  removeAssignee: (issueId: string, userId: string) =>
    del<unknown>(`/api/issues/${issueId}/assignees/${userId}`),

  // Tags on issues
  addIssueTag: (issueId: string, tagId: string) =>
    post<unknown>(`/api/issues/${issueId}/tags`, { tag_id: tagId }),
  removeIssueTag: (issueId: string, tagId: string) =>
    del<unknown>(`/api/issues/${issueId}/tags/${tagId}`),

  // Sub-issues
  listSubIssues: (issueId: string) =>
    get<Issue[]>(`/api/issues/${issueId}/sub-issues`),

  // Legacy
  listTasks: () => get<Issue[]>("/api/tasks"),

  // Memory
  memoryStats: () => get<MemoryStats>("/api/memory/stats"),
  memorySearch: (q: string, limit = 20) =>
    get<MemoryItem[]>(`/api/memory/search?q=${encodeURIComponent(q)}&limit=${limit}`),
  memoryAll: (opts?: { limit?: number; layer?: string; category?: string }) => {
    const params = new URLSearchParams();
    if (opts?.limit) params.set("limit", String(opts.limit));
    if (opts?.layer) params.set("layer", opts.layer);
    if (opts?.category) params.set("category", opts.category);
    const qs = params.toString();
    return get<MemoryItem[]>(`/api/memory/all${qs ? `?${qs}` : ""}`);
  },
  memoryCategories: () => get<MemoryCategory[]>("/api/memory/categories"),
  memoryGet: (id: string) => get<MemoryItem>(`/api/memory/${id}`),

  // Coordination
  coordinationAgents: () => get<CoordinationAgent[]>("/api/coordination/agents"),
  coordinationRegister: (name: string, data: {
    capabilities: string[];
    description: string;
    agent_type: string;
    model?: string;
    max_concurrent?: number;
  }) => post<CoordinationAgent>(`/api/coordination/agents/${name}/register`, data),
  coordinationMatch: (q: string) =>
    get<CoordinationAgent[]>(`/api/coordination/agents/match?q=${encodeURIComponent(q)}`),
  coordinationRoute: (taskId: string, force = false) =>
    post<Issue>(`/api/coordination/route/${taskId}`, { force }),
  coordinationRoutePending: () =>
    post<RouteResult>("/api/coordination/route-pending"),
  coordinationClaim: (taskId: string, agentName: string) =>
    post<Issue>(`/api/coordination/claim/${taskId}`, { agent_name: agentName }),
  coordinationEvents: (limit = 50) =>
    get<CoordinationEvent[]>(`/api/coordination/events?limit=${limit}`),
};

// ── Legacy hooks for backward compatibility with old dashboard ──

export function useAgents() {
  const [agents, setAgents] = useState<AgentInfo[]>([]);
  const refresh = useCallback(async () => {
    try { setAgents(await get<AgentInfo[]>("/api/agents")); } catch { /* offline */ }
  }, []);
  useEffect(() => { refresh(); }, [refresh]);
  return { agents, refresh };
}

export function useTasks() {
  const [tasks, setTasks] = useState<Issue[]>([]);
  const refresh = useCallback(async () => {
    try { setTasks(await get<Issue[]>("/api/tasks")); } catch { /* offline */ }
  }, []);
  useEffect(() => { refresh(); }, [refresh]);
  const create = useCallback(async (data: Partial<Issue>) => {
    const task = await post<Issue>("/api/tasks", data);
    setTasks(prev => [...prev, task]);
    return task;
  }, []);
  const update = useCallback(async (id: string, data: Partial<Issue>) => {
    const task = await put<Issue>(`/api/tasks/${id}`, data);
    setTasks(prev => prev.map(t => t.id === id ? task : t));
    return task;
  }, []);
  const remove = useCallback(async (id: string) => {
    await del(`/api/tasks/${id}`);
    setTasks(prev => prev.filter(t => t.id !== id));
  }, []);
  return { tasks, refresh, create, update, remove };
}

export async function fetchTaskDetail(id: string): Promise<Issue> {
  return get<Issue>(`/api/tasks/${id}/detail`);
}

export function useFeed() {
  const [feed, setFeed] = useState<FeedEvent[]>([]);
  const refresh = useCallback(async () => {
    try { setFeed(await get<FeedEvent[]>("/api/feed")); } catch { /* offline */ }
  }, []);
  useEffect(() => { refresh(); }, [refresh]);
  const addEvent = useCallback((event: FeedEvent) => {
    setFeed(prev => [...prev.slice(-199), event]);
  }, []);
  return { feed, refresh, addEvent };
}
