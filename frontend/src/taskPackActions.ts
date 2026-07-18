import { useCallback, useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { ApiError, api } from "./api";
import { clearLocalDraft, saveLocalDraft, useAppStore } from "./store";
import type { SearchResultV2, WorkspaceEvidence, WorkspaceTask } from "./types";

export function appendEvidence(
  task: WorkspaceTask,
  result: SearchResultV2,
  evidence: WorkspaceEvidence = result.best_evidence,
): WorkspaceTask {
  const duplicate = task.items.some((item) =>
    item.document_id === result.document_id &&
    item.page_number === evidence.page_number &&
    item.excerpt === evidence.excerpt
  );
  if (duplicate) return task;
  const sortedSlots = [...task.slots].sort((left, right) => left.position - right.position);
  const target = sortedSlots.at(-1);
  if (!target) return task;
  return {
    ...task,
    items: [
      ...task.items,
      {
        item_id: crypto.randomUUID(),
        document_id: result.document_id,
        content_hash: result.content_hash,
        name: result.name,
        relative_path: result.relative_path,
        page_number: evidence.page_number,
        locator: evidence.locator ?? null,
        excerpt: evidence.excerpt,
        rationale: evidence.reason,
        slot_id: target.slot_id,
        review_state: "pending",
        source_status: "resolved",
        source_ref: result.source_ref ?? null,
        quality_flags: result.quality_flags ?? [],
        error_code: result.error_code ?? "",
        freshness_status: result.freshness_status ?? "unverified",
        verified_content_hash: "",
        verified_at: "",
        position: task.items.filter((item) => item.slot_id === target.slot_id).length,
        added_at: new Date().toISOString(),
      },
    ],
  };
}

export function useTaskActions() {
  const queryClient = useQueryClient();
  const queue = useRef<Promise<void>>(Promise.resolve());
  const pendingCount = useRef(0);
  const [adding, setAdding] = useState(false);
  const [actionError, setActionError] = useState("");

  const addResult = useCallback(async (
    result: SearchResultV2,
    evidence: WorkspaceEvidence = result.best_evidence,
    defaultTitle?: string,
    sourceWorkspaceId = useAppStore.getState().workspaceId,
  ) => {
    if (!sourceWorkspaceId) return;
    pendingCount.current += 1;
    setAdding(true);
    setActionError("");

    const operation = queue.current
      .catch(() => undefined)
      .then(async () => {
        const state = useAppStore.getState();
        if (state.workspaceId !== sourceWorkspaceId) {
          throw new ApiError("资料空间已切换，这条证据没有加入任务。", 409);
        }

        let task = state.activeTask;
        if (!task) {
          const sourceQuery = state.submittedQuery.trim() || state.query.trim();
          task = await api.createTask(
            sourceWorkspaceId,
            defaultTitle || sourceQuery || "资料核对任务",
            sourceQuery,
          );
          await queryClient.invalidateQueries({ queryKey: ["tasks", sourceWorkspaceId] });
        }

        const updated = appendEvidence(task, result, evidence);
        if (useAppStore.getState().workspaceId !== sourceWorkspaceId) {
          if (updated !== task) {
            saveLocalDraft(updated);
            await api.saveTask(updated);
            clearLocalDraft(updated);
          }
          return;
        }
        const latestState = useAppStore.getState();
        latestState.setTask(updated, latestState.taskDirty || updated !== task);
      });
    queue.current = operation.catch(() => undefined);

    try {
      await operation;
    } catch (reason) {
      setActionError(
        reason instanceof ApiError
          ? reason.message
          : "证据没有加入任务，请重试。",
      );
    } finally {
      pendingCount.current -= 1;
      if (pendingCount.current === 0) setAdding(false);
    }
  }, [queryClient]);

  const clearActionError = useCallback(() => setActionError(""), []);
  return { addResult, adding, actionError, clearActionError };
}
