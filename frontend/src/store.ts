import { create } from "zustand";
import type { PageId, SearchFilters, SearchResult, TaskPack } from "./types";

export const EMPTY_FILTERS: SearchFilters = {
  index_types: [],
  path_prefix: "",
  statuses: [],
  quality_flags: [],
  modified_after: "",
  modified_before: "",
};

interface AppState {
  page: PageId;
  repositoryId: string;
  inspector: SearchResult | null;
  inspectorOpen: boolean;
  activeTaskPack: TaskPack | null;
  taskPackDirty: boolean;
  saveState: "idle" | "saving" | "saved" | "offline" | "conflict";
  trayExpanded: boolean;
  query: string;
  filters: SearchFilters;
  aiEnabled: boolean;
  setPage: (page: PageId) => void;
  setRepositoryId: (repositoryId: string) => void;
  inspect: (result: SearchResult | null) => void;
  closeInspector: () => void;
  setTaskPack: (pack: TaskPack | null, dirty?: boolean) => void;
  updateTaskPack: (updater: (pack: TaskPack) => TaskPack) => void;
  setSaveState: (state: AppState["saveState"]) => void;
  setTrayExpanded: (expanded: boolean) => void;
  setQuery: (query: string) => void;
  setFilters: (filters: SearchFilters) => void;
  setAiEnabled: (enabled: boolean) => void;
}

export const useAppStore = create<AppState>((set) => ({
  page: "workbench",
  repositoryId: "",
  inspector: null,
  inspectorOpen: false,
  activeTaskPack: null,
  taskPackDirty: false,
  saveState: "idle",
  trayExpanded: false,
  query: "",
  filters: EMPTY_FILTERS,
  aiEnabled: false,
  setPage: (page) => set({ page }),
  setRepositoryId: (repositoryId) => set({ repositoryId, activeTaskPack: null }),
  inspect: (inspector) => set({ inspector, inspectorOpen: inspector !== null }),
  closeInspector: () => set({ inspectorOpen: false }),
  setTaskPack: (activeTaskPack, dirty = false) =>
    set({ activeTaskPack, taskPackDirty: dirty, saveState: dirty ? "idle" : "saved" }),
  updateTaskPack: (updater) =>
    set((state) => ({
      activeTaskPack: state.activeTaskPack ? updater(state.activeTaskPack) : null,
      taskPackDirty: state.activeTaskPack !== null,
      saveState: "idle",
    })),
  setSaveState: (saveState) =>
    set((state) => ({
      saveState,
      taskPackDirty: saveState === "saved" ? false : state.taskPackDirty,
    })),
  setTrayExpanded: (trayExpanded) => set({ trayExpanded }),
  setQuery: (query) => set({ query }),
  setFilters: (filters) => set({ filters }),
  setAiEnabled: (aiEnabled) => set({ aiEnabled }),
}));

export function draftStorageKey(repositoryId: string, taskPackId: string): string {
  return `octopus:task-pack-draft:${repositoryId}:${taskPackId}`;
}

export function saveLocalDraft(pack: TaskPack): void {
  localStorage.setItem(draftStorageKey(pack.repository_id, pack.task_pack_id), JSON.stringify(pack));
}

export function clearLocalDraft(pack: TaskPack): void {
  localStorage.removeItem(draftStorageKey(pack.repository_id, pack.task_pack_id));
}

export function rebaseTaskPackDraft(draft: TaskPack, server: TaskPack): TaskPack {
  return {
    ...draft,
    revision: server.revision,
    lifecycle: server.lifecycle,
    created_at: server.created_at,
    updated_at: server.updated_at,
  };
}

export function mergeTaskPackSave(
  saved: TaskPack,
  submitted: TaskPack,
  current: TaskPack | null,
): { pack: TaskPack; dirty: boolean } | null {
  if (!current || current.task_pack_id !== submitted.task_pack_id) return null;
  if (current === submitted) return { pack: saved, dirty: false };
  return { pack: rebaseTaskPackDraft(current, saved), dirty: true };
}
