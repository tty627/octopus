import { useCallback, useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { ApiError, api } from "./api";
import { clearLocalDraft, saveLocalDraft, useAppStore } from "./store";
import type {
  SearchResultV2,
  TaskTemplateId,
  WorkspaceEvidence,
  WorkspaceTask,
  WorkspaceTaskSlot,
} from "./types";

const TEMPLATE_SLOT_PREFERENCES: Record<TaskTemplateId, string[]> = {
  literature_review: ["核心文献", "文献"],
  course_report: ["论据与材料", "论据", "材料"],
  free_research: ["待核验", "核心证据", "补充证据"],
};

function normalizedSlotName(slot: WorkspaceTaskSlot): string {
  return slot.name.trim().replace(/\s+/g, "");
}

function inferredTemplate(task: WorkspaceTask): TaskTemplateId | undefined {
  if (task.template_id) return task.template_id;
  const names = task.slots.map(normalizedSlotName);
  if (names.some((name) => name.includes("核心文献") || name.includes("研究缺口"))) {
    return "literature_review";
  }
  if (names.some((name) => name.includes("论据与材料") || name.includes("题目与要求"))) {
    return "course_report";
  }
  if (names.some((name) => name.includes("待核验")) && names.some((name) => name.includes("核心证据"))) {
    return "free_research";
  }
  return undefined;
}

export function evidenceTargetSlot(task: WorkspaceTask): WorkspaceTaskSlot | undefined {
  const slots = [...task.slots].sort((left, right) => left.position - right.position);
  const template = inferredTemplate(task);
  const preferences = template ? TEMPLATE_SLOT_PREFERENCES[template] : [];
  for (const preference of preferences) {
    const preferred = slots.find((slot) => normalizedSlotName(slot).includes(preference));
    if (preferred) return preferred;
  }

  const specialized = (slot: WorkspaceTaskSlot) => {
    const name = normalizedSlotName(slot);
    if (template === "free_research" && name.includes("待核验")) return false;
    return /待核验|研究缺口|缺口|相反|反例|结论/.test(name);
  };
  const evidenceNamed = slots.find((slot) =>
    !specialized(slot) && /证据|文献|论据|材料|资料/.test(normalizedSlotName(slot))
  );
  return evidenceNamed
    ?? slots.find((slot) => slot.required && !specialized(slot))
    ?? slots.find((slot) => !specialized(slot));
}

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
  const existingTarget = evidenceTargetSlot(task);
  const target: WorkspaceTaskSlot = existingTarget ?? {
    slot_id: crypto.randomUUID(),
    name: "收集证据",
    description: "手动收集、尚待归类的证据。",
    position: Math.max(-1, ...task.slots.map((slot) => slot.position)) + 1,
    required: false,
  };
  return {
    ...task,
    slots: existingTarget ? task.slots : [...task.slots, target],
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
