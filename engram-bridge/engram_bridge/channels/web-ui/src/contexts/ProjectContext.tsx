import { createContext, useContext, useCallback, useEffect, useState, type ReactNode } from "react";
import { api } from "@/hooks/use-api";
import type { Project, ProjectStatus, ProjectTag, Issue } from "@/types";

interface ProjectContextValue {
  // Data
  projects: Project[];
  currentProject: Project | null;
  statuses: ProjectStatus[];
  tags: ProjectTag[];
  issues: Issue[];

  // Project actions
  selectProject: (id: string) => void;
  createProject: (data: { name: string; color?: string; description?: string }) => Promise<Project>;
  deleteProject: (id: string) => Promise<void>;

  // Issue actions
  createIssue: (data: Partial<Issue> & { title: string }) => Promise<Issue>;
  updateIssue: (id: string, data: Partial<Issue>) => Promise<Issue>;
  deleteIssue: (id: string) => Promise<void>;
  bulkUpdateIssues: (updates: Partial<Issue>[]) => Promise<void>;

  // Status actions
  createStatus: (data: { name: string; color?: string; sort_order?: number }) => Promise<ProjectStatus>;
  updateStatus: (id: string, data: Partial<ProjectStatus>) => Promise<ProjectStatus>;

  // Tag actions
  createTag: (data: { name: string; color?: string }) => Promise<ProjectTag>;

  // Comment actions
  addComment: (issueId: string, text: string) => Promise<void>;

  // Refresh
  refreshIssues: () => Promise<void>;
  refreshAll: () => Promise<void>;
  loading: boolean;
}

const ProjectContext = createContext<ProjectContextValue | null>(null);

export function useProjectContext() {
  const ctx = useContext(ProjectContext);
  if (!ctx) throw new Error("useProjectContext must be used within ProjectProvider");
  return ctx;
}

export function ProjectProvider({ children }: { children: ReactNode }) {
  const [projects, setProjects] = useState<Project[]>([]);
  const [currentProject, setCurrentProject] = useState<Project | null>(null);
  const [statuses, setStatuses] = useState<ProjectStatus[]>([]);
  const [tags, setTags] = useState<ProjectTag[]>([]);
  const [issues, setIssues] = useState<Issue[]>([]);
  const [loading, setLoading] = useState(true);

  const loadProjects = useCallback(async () => {
    try {
      const list = await api.listProjects();
      setProjects(list);
      // Auto-select first project if none selected
      if (list.length > 0 && !currentProject) {
        setCurrentProject(list[0]);
      }
      return list;
    } catch {
      return [];
    }
  }, [currentProject]);

  const loadProjectData = useCallback(async (projectId: string) => {
    try {
      const [s, t, i] = await Promise.all([
        api.listStatuses(projectId),
        api.listTags(projectId),
        api.listIssues(projectId),
      ]);
      setStatuses(s);
      setTags(t);
      setIssues(i);
    } catch {
      setStatuses([]);
      setTags([]);
      setIssues([]);
    }
  }, []);

  // Initial load
  useEffect(() => {
    setLoading(true);
    loadProjects().then(list => {
      if (list.length === 0) {
        // Auto-create default project
        api.createProject({ name: "My Project", color: "#6366f1" })
          .then(p => {
            setProjects([p]);
            setCurrentProject(p);
          })
          .catch(() => {})
          .finally(() => setLoading(false));
      } else {
        setLoading(false);
      }
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Load data when project changes
  useEffect(() => {
    if (currentProject) {
      loadProjectData(currentProject.id);
    }
  }, [currentProject, loadProjectData]);

  const selectProject = useCallback((id: string) => {
    const p = projects.find(p => p.id === id);
    if (p) setCurrentProject(p);
  }, [projects]);

  const createProjectFn = useCallback(async (data: { name: string; color?: string; description?: string }) => {
    const p = await api.createProject(data);
    setProjects(prev => [...prev, p]);
    setCurrentProject(p);
    return p;
  }, []);

  const deleteProjectFn = useCallback(async (id: string) => {
    await api.deleteProject(id);
    setProjects(prev => prev.filter(p => p.id !== id));
    if (currentProject?.id === id) {
      setCurrentProject(projects.find(p => p.id !== id) || null);
    }
  }, [currentProject, projects]);

  const createIssueFn = useCallback(async (data: Partial<Issue> & { title: string }) => {
    if (!currentProject) throw new Error("No project selected");
    const issue = await api.createIssue({ ...data, project_id: currentProject.id });
    setIssues(prev => [...prev, issue]);
    return issue;
  }, [currentProject]);

  const updateIssueFn = useCallback(async (id: string, data: Partial<Issue>) => {
    const issue = await api.updateIssue(id, data);
    setIssues(prev => prev.map(i => i.id === id ? issue : i));
    return issue;
  }, []);

  const deleteIssueFn = useCallback(async (id: string) => {
    await api.deleteIssue(id);
    setIssues(prev => prev.filter(i => i.id !== id));
  }, []);

  const bulkUpdateIssuesFn = useCallback(async (updates: Partial<Issue>[]) => {
    await api.bulkUpdateIssues(updates);
    if (currentProject) {
      const fresh = await api.listIssues(currentProject.id);
      setIssues(fresh);
    }
  }, [currentProject]);

  const createStatusFn = useCallback(async (data: { name: string; color?: string; sort_order?: number }) => {
    if (!currentProject) throw new Error("No project selected");
    const s = await api.createStatus(currentProject.id, data);
    setStatuses(prev => [...prev, s].sort((a, b) => a.sort_order - b.sort_order));
    return s;
  }, [currentProject]);

  const updateStatusFn = useCallback(async (id: string, data: Partial<ProjectStatus>) => {
    const s = await api.updateStatus(id, data);
    setStatuses(prev => prev.map(st => st.id === id ? s : st).sort((a, b) => a.sort_order - b.sort_order));
    return s;
  }, []);

  const createTagFn = useCallback(async (data: { name: string; color?: string }) => {
    if (!currentProject) throw new Error("No project selected");
    const t = await api.createTag(currentProject.id, data);
    setTags(prev => [...prev, t]);
    return t;
  }, [currentProject]);

  const addCommentFn = useCallback(async (issueId: string, text: string) => {
    await api.addComment(issueId, "user", text);
    // Refresh issue
    const issue = await api.getIssue(issueId);
    setIssues(prev => prev.map(i => i.id === issueId ? issue : i));
  }, []);

  const refreshIssues = useCallback(async () => {
    if (currentProject) {
      const i = await api.listIssues(currentProject.id);
      setIssues(i);
    }
  }, [currentProject]);

  const refreshAll = useCallback(async () => {
    await loadProjects();
    if (currentProject) {
      await loadProjectData(currentProject.id);
    }
  }, [loadProjects, currentProject, loadProjectData]);

  return (
    <ProjectContext.Provider value={{
      projects, currentProject, statuses, tags, issues,
      selectProject, createProject: createProjectFn, deleteProject: deleteProjectFn,
      createIssue: createIssueFn, updateIssue: updateIssueFn,
      deleteIssue: deleteIssueFn, bulkUpdateIssues: bulkUpdateIssuesFn,
      createStatus: createStatusFn, updateStatus: updateStatusFn,
      createTag: createTagFn, addComment: addCommentFn,
      refreshIssues, refreshAll, loading,
    }}>
      {children}
    </ProjectContext.Provider>
  );
}
