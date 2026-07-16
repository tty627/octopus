import { create } from "zustand";
import type {
  PageId,
  SearchFiltersV2,
  SearchResultV2,
  WorkspaceTask,
} from "./types";

export const EMPTY_FILTERS: SearchFiltersV2 = {
  path_prefix: "",
  extensions: [],
  source_kinds: [],
  readability: [],
  indexing_states: [],
  modified_from: "",
  modified_to: "",
  task_id: "",
};

interface AppState {
  page: PageId;
  workspaceId: string;
  inspector: SearchResultV2 | null;
  inspectorOpen: boolean;
  activeTask: WorkspaceTask | null;
  taskDirty: boolean;
  saveState: "idle" | "saving" | "saved" | "offline" | "conflict";
  query: string;
  submittedQuery: string;
  filters: SearchFiltersV2;
  assistedEnabled: boolean;
  setPage: (page: PageId) => void;
  setWorkspaceId: (workspaceId: string) => void;
  inspect: (result: SearchResultV2 | null) => void;
  closeInspector: () => void;
  setTask: (task: WorkspaceTask | null, dirty?: boolean) => void;
  updateTask: (updater: (task: WorkspaceTask) => WorkspaceTask) => void;
  setSaveState: (state: AppState["saveState"]) => void;
  setQuery: (query: string) => void;
  setSubmittedQuery: (query: string) => void;
  setFilters: (filters: SearchFiltersV2) => void;
  setAssistedEnabled: (enabled: boolean) => void;
}

export const useAppStore = create<AppState>((set) => ({
  page: "home",
  workspaceId: "",
  inspector: null,
  inspectorOpen: false,
  activeTask: null,
  taskDirty: false,
  saveState: "idle",
  query: "",
  submittedQuery: "",
  filters: EMPTY_FILTERS,
  assistedEnabled: false,
  setPage: (page) => set({ page }),
  setWorkspaceId: (workspaceId) =>
    set((state) => {
      if (state.activeTask && state.taskDirty) saveLocalDraft(state.activeTask);
      return {
        workspaceId,
        activeTask: null,
        taskDirty: false,
        saveState: "idle",
        inspector: null,
        inspectorOpen: false,
        submittedQuery: "",
      };
    }),
  inspect: (inspector) => set({ inspector, inspectorOpen: inspector !== null }),
  closeInspector: () => set({ inspectorOpen: false }),
  setTask: (activeTask, dirty = false) => {
    if (activeTask && dirty) saveLocalDraft(activeTask);
    set({ activeTask, taskDirty: dirty, saveState: dirty ? "idle" : "saved" });
  },
  updateTask: (updater) =>
    set((state) => {
      if (!state.activeTask) return state;
      const activeTask = updater(state.activeTask);
      saveLocalDraft(activeTask);
      return { activeTask, taskDirty: true, saveState: "idle" };
    }),
  setSaveState: (saveState) =>
    set((state) => ({
      saveState,
      taskDirty: saveState === "saved" ? false : state.taskDirty,
    })),
  setQuery: (query) => set({ query }),
  setSubmittedQuery: (submittedQuery) => set({ submittedQuery }),
  setFilters: (filters) => set({ filters }),
  setAssistedEnabled: (assistedEnabled) => set({ assistedEnabled }),
}));

export function draftStorageKey(workspaceId: string, taskId: string): string {
  return `octopus:v2-task-draft:${workspaceId}:${taskId}`;
}

export function saveLocalDraft(task: WorkspaceTask): void {
  try {
    window.localStorage.setItem(
      draftStorageKey(task.workspace_id, task.task_id),
      JSON.stringify(task),
    );
  } catch {
    // The server save path still runs when browser storage is unavailable.
  }
}

export function clearLocalDraft(task: WorkspaceTask): void {
  try {
    window.localStorage.removeItem(draftStorageKey(task.workspace_id, task.task_id));
  } catch {
    // Ignore browser storage failures after a successful server save.
  }
}

export function loadLocalDraft(task: WorkspaceTask): WorkspaceTask | null {
  try {
    const stored = window.localStorage.getItem(draftStorageKey(task.workspace_id, task.task_id));
    if (!stored) return null;
    const draft = JSON.parse(stored) as WorkspaceTask;
    if (draft.workspace_id !== task.workspace_id || draft.task_id !== task.task_id) return null;
    return draft;
  } catch {
    return null;
  }
}

export function rebaseTaskDraft(
  draft: WorkspaceTask,
  server: WorkspaceTask,
): WorkspaceTask {
  return {
    ...draft,
    revision: server.revision,
    lifecycle: server.lifecycle,
    created_at: server.created_at,
    updated_at: server.updated_at,
  };
}

export function mergeTaskSave(
  saved: WorkspaceTask,
  submitted: WorkspaceTask,
  current: WorkspaceTask | null,
): { task: WorkspaceTask; dirty: boolean } | null {
  if (!current || current.task_id !== submitted.task_id) return null;
  if (current === submitted) return { task: saved, dirty: false };
  return { task: rebaseTaskDraft(current, saved), dirty: true };
}
