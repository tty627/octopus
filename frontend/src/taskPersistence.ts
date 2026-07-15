import type { QueryClient } from "@tanstack/react-query";
import { ApiError, api } from "./api";
import {
  clearLocalDraft,
  loadLocalDraft,
  mergeTaskSave,
  rebaseTaskDraft,
  saveLocalDraft,
  useAppStore,
} from "./store";
import type { WorkspaceTask } from "./types";

const saveQueues = new Map<string, Promise<WorkspaceTask | null>>();

function taskKey(workspaceId: string, taskId: string): string {
  return `${workspaceId}:${taskId}`;
}

async function saveActiveTaskOnce(
  queryClient: QueryClient,
  workspaceId: string,
  taskId: string,
): Promise<WorkspaceTask | null> {
  const state = useAppStore.getState();
  const submitted = state.activeTask;
  if (!submitted || submitted.workspace_id !== workspaceId || submitted.task_id !== taskId) {
    return null;
  }
  if (!state.taskDirty) return submitted;

  state.setSaveState("saving");
  try {
    const saved = await api.saveTask(submitted);
    const current = useAppStore.getState().activeTask;
    const merged = mergeTaskSave(saved, submitted, current);
    if (merged) {
      if (merged.dirty) saveLocalDraft(merged.task);
      else clearLocalDraft(merged.task);
      useAppStore.getState().setTask(merged.task, merged.dirty);
    } else {
      const draft = loadLocalDraft(submitted);
      if (draft?.revision === submitted.revision) {
        if (JSON.stringify(draft) === JSON.stringify(submitted)) clearLocalDraft(draft);
        else saveLocalDraft(rebaseTaskDraft(draft, saved));
      }
    }
    await queryClient.invalidateQueries({ queryKey: ["tasks", workspaceId] });
    return merged?.task ?? saved;
  } catch (error) {
    const current = useAppStore.getState().activeTask;
    if (current?.workspace_id === workspaceId && current.task_id === taskId) {
      saveLocalDraft(current);
      useAppStore.getState().setSaveState(
        error instanceof ApiError && error.status === 409 ? "conflict" : "offline",
      );
    }
    throw error;
  }
}

export function persistActiveTask(
  queryClient: QueryClient,
  workspaceId: string,
  taskId: string,
): Promise<WorkspaceTask | null> {
  const key = taskKey(workspaceId, taskId);
  const previous = saveQueues.get(key) ?? Promise.resolve(null);
  const current = previous
    .catch(() => null)
    .then(() => saveActiveTaskOnce(queryClient, workspaceId, taskId));
  saveQueues.set(key, current);
  void current.then(
    () => { if (saveQueues.get(key) === current) saveQueues.delete(key); },
    () => { if (saveQueues.get(key) === current) saveQueues.delete(key); },
  );
  return current;
}

export async function flushActiveTask(
  queryClient: QueryClient,
  workspaceId: string,
  taskId: string,
): Promise<WorkspaceTask> {
  for (let attempt = 0; attempt < 20; attempt += 1) {
    await persistActiveTask(queryClient, workspaceId, taskId);
    const state = useAppStore.getState();
    const current = state.activeTask;
    if (!current || current.workspace_id !== workspaceId || current.task_id !== taskId) {
      throw new ApiError("任务已切换，操作没有执行。", 409);
    }
    if (!state.taskDirty) return current;
  }
  throw new ApiError("任务仍在更新，请稍后重试。", 409);
}
