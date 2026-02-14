import { create } from "zustand";
import { persist } from "zustand/middleware";
import type { Priority } from "@/types";

export interface TodoDraft {
  id: string;
  title: string;
  priority: Priority;
  selected: boolean;
  createdAt: string;
}

interface TodoState {
  drafts: TodoDraft[];
  addDraft: (title: string) => void;
  updateDraftTitle: (id: string, title: string) => void;
  updateDraftPriority: (id: string, priority: Priority) => void;
  removeDraft: (id: string) => void;
  toggleSelect: (id: string) => void;
  selectAll: () => void;
  deselectAll: () => void;
  reorder: (fromIndex: number, toIndex: number) => void;
  removeDrafts: (ids: string[]) => void;
}

export const useTodoStore = create<TodoState>()(
  persist(
    (set) => ({
      drafts: [],

      addDraft: (title) =>
        set((s) => ({
          drafts: [
            ...s.drafts,
            {
              id: crypto.randomUUID(),
              title,
              priority: "medium" as Priority,
              selected: false,
              createdAt: new Date().toISOString(),
            },
          ],
        })),

      updateDraftTitle: (id, title) =>
        set((s) => ({
          drafts: s.drafts.map((d) => (d.id === id ? { ...d, title } : d)),
        })),

      updateDraftPriority: (id, priority) =>
        set((s) => ({
          drafts: s.drafts.map((d) => (d.id === id ? { ...d, priority } : d)),
        })),

      removeDraft: (id) =>
        set((s) => ({
          drafts: s.drafts.filter((d) => d.id !== id),
        })),

      toggleSelect: (id) =>
        set((s) => ({
          drafts: s.drafts.map((d) =>
            d.id === id ? { ...d, selected: !d.selected } : d,
          ),
        })),

      selectAll: () =>
        set((s) => ({
          drafts: s.drafts.map((d) => ({ ...d, selected: true })),
        })),

      deselectAll: () =>
        set((s) => ({
          drafts: s.drafts.map((d) => ({ ...d, selected: false })),
        })),

      reorder: (fromIndex, toIndex) =>
        set((s) => {
          const drafts = [...s.drafts];
          const [moved] = drafts.splice(fromIndex, 1);
          drafts.splice(toIndex, 0, moved);
          return { drafts };
        }),

      removeDrafts: (ids) =>
        set((s) => ({
          drafts: s.drafts.filter((d) => !ids.includes(d.id)),
        })),
    }),
    {
      name: "engram-todos",
    },
  ),
);
