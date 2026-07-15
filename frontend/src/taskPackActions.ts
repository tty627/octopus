import { useQueryClient } from "@tanstack/react-query";
import { api } from "./api";
import { useAppStore } from "./store";
import type { SearchResultV2, WorkspaceTask } from "./types";

export function appendEvidence(task: WorkspaceTask, result: SearchResultV2): WorkspaceTask {
  const evidence = result.best_evidence;
  const duplicate = task.items.some((item) =>
    item.document_id === result.document_id &&
    item.page_number === evidence.page_number &&
    item.excerpt === evidence.excerpt
  );
  if (duplicate) return task;
  const pending = result.readability !== "readable";
  const sortedSlots = [...task.slots].sort((left, right) => left.position - right.position);
  const target = pending ? sortedSlots.at(-1) : sortedSlots[0];
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
        excerpt: evidence.excerpt,
        rationale: evidence.reason,
        slot_id: target.slot_id,
        review_state: pending ? "pending" : "confirmed",
        source_status: "resolved",
        position: task.items.filter((item) => item.slot_id === target.slot_id).length,
        added_at: new Date().toISOString(),
      },
    ],
  };
}

export function useTaskActions() {
  const workspaceId = useAppStore((state) => state.workspaceId);
  const activeTask = useAppStore((state) => state.activeTask);
  const setTask = useAppStore((state) => state.setTask);
  const query = useAppStore((state) => state.query);
  const queryClient = useQueryClient();

  const addResult = async (result: SearchResultV2, defaultTitle?: string) => {
    if (!workspaceId) return;
    let task = activeTask;
    if (!task) {
      task = await api.createTask(
        workspaceId,
        defaultTitle || query.trim() || "资料核对任务",
        query.trim(),
      );
      await queryClient.invalidateQueries({ queryKey: ["tasks", workspaceId] });
    }
    const updated = appendEvidence(task, result);
    setTask(updated, updated !== task);
  };

  return { addResult };
}
