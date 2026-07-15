import { useCallback } from "react";
import { api } from "./api";
import { useAppStore } from "./store";
import type { SearchResult, TaskPack, TaskPackItem } from "./types";

let pendingPackCreation: { repositoryId: string; promise: Promise<TaskPack> } | null = null;

function appendResult(pack: TaskPack, result: SearchResult): TaskPack {
  if (pack.items.some((item) => item.node_id === result.node_id)) return pack;
  const needsReview =
    result.quality_flags.length > 0 ||
    result.risk_flags.length > 0 ||
    !["clean", "indexed"].includes(result.status);
  const targetSlot = needsReview ? pack.slots.find((slot) => slot.name === "待核验") : pack.slots[0];
  if (!targetSlot) return pack;
  const item: TaskPackItem = {
    item_id: crypto.randomUUID(),
    node_id: result.node_id,
    name: result.name,
    index_type: result.index_type,
    raw_relative_path: result.raw_relative_path,
    content_id: result.content_id,
    status_snapshot: result.status,
    anchors: result.evidence.slice(0, 8),
    rationale: result.explanation || result.match_reasons[0] || "用户从搜索结果加入",
    slot_id: targetSlot.slot_id,
    review_state: needsReview ? "pending" : "confirmed",
    position: pack.items.filter((value) => value.slot_id === targetSlot.slot_id).length,
  };
  return { ...pack, items: [...pack.items, item] };
}

export function useTaskPackActions(): {
  addResult: (result: SearchResult, defaultTitle?: string) => Promise<void>;
} {
  const repositoryId = useAppStore((state) => state.repositoryId);
  const setTaskPack = useAppStore((state) => state.setTaskPack);
  const setTrayExpanded = useAppStore((state) => state.setTrayExpanded);

  const addResult = useCallback(
    async (result: SearchResult, defaultTitle = "资料整理任务") => {
      if (!repositoryId) return;
      let pack = useAppStore.getState().activeTaskPack;
      if (!pack) {
        if (!pendingPackCreation || pendingPackCreation.repositoryId !== repositoryId) {
          const promise = api.createTaskPack(repositoryId, defaultTitle, defaultTitle);
          pendingPackCreation = { repositoryId, promise };
          void promise.finally(() => {
            if (pendingPackCreation?.promise === promise) pendingPackCreation = null;
          });
        }
        const created = await pendingPackCreation.promise;
        pack = useAppStore.getState().activeTaskPack ?? created;
      }
      const updated = appendResult(pack, result);
      setTaskPack(updated, updated !== pack);
      setTrayExpanded(true);
    },
    [repositoryId, setTaskPack, setTrayExpanded],
  );

  return { addResult };
}

export { appendResult };
