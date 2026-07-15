import { useEffect, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Archive,
  ArrowLeft,
  Check,
  Download,
  FilePlus2,
  FolderOpen,
  LoaderCircle,
  Plus,
  Save,
  Search,
  Trash2,
} from "lucide-react";
import { ApiError, api } from "../api";
import { saveTextFile } from "../bridge";
import {
  clearLocalDraft,
  loadLocalDraft,
  rebaseTaskDraft,
  useAppStore,
} from "../store";
import { flushActiveTask } from "../taskPersistence";
import type { WorkspaceTaskItem, WorkspaceTaskSlot } from "../types";
import { relativeTime, safeFileName } from "../utils";

export function TaskPacksView() {
  const workspaceId = useAppStore((state) => state.workspaceId);
  const task = useAppStore((state) => state.activeTask);
  const setTask = useAppStore((state) => state.setTask);
  const updateTask = useAppStore((state) => state.updateTask);
  const saveState = useAppStore((state) => state.saveState);
  const taskDirty = useAppStore((state) => state.taskDirty);
  const setPage = useAppStore((state) => state.setPage);
  const queryClient = useQueryClient();
  const [title, setTitle] = useState("新的证据任务");
  const [goal, setGoal] = useState("");
  const [error, setError] = useState("");
  const [exporting, setExporting] = useState(false);
  const [archiving, setArchiving] = useState(false);
  const [returningToList, setReturningToList] = useState(false);
  const [resolvingConflict, setResolvingConflict] = useState(false);
  const activationSequence = useRef(0);
  const summaries = useQuery({
    queryKey: ["tasks", workspaceId],
    queryFn: () => api.tasks(workspaceId),
    enabled: Boolean(workspaceId),
  });
  useEffect(() => {
    activationSequence.current += 1;
    setTitle("新的证据任务");
    setGoal("");
    setError("");
  }, [workspaceId]);
  const create = useMutation({
    mutationFn: ({ sourceWorkspaceId, taskTitle, taskGoal }: {
      sourceWorkspaceId: string;
      taskTitle: string;
      taskGoal: string;
      requestId: number;
    }) => api.createTask(sourceWorkspaceId, taskTitle, taskGoal),
    onSuccess: (created, variables) => {
      void queryClient.invalidateQueries({ queryKey: ["tasks", variables.sourceWorkspaceId] });
      if (
        useAppStore.getState().workspaceId !== variables.sourceWorkspaceId ||
        activationSequence.current !== variables.requestId ||
        created.workspace_id !== variables.sourceWorkspaceId
      ) return;
      setTask(created);
      setError("");
    },
    onError: (reason, variables) => {
      if (
        useAppStore.getState().workspaceId !== variables.sourceWorkspaceId ||
        activationSequence.current !== variables.requestId
      ) return;
      setError(reason instanceof ApiError ? reason.message : "任务创建失败。");
    },
  });

  const loadTask = async (taskId: string) => {
    const sourceWorkspaceId = workspaceId;
    const requestId = ++activationSequence.current;
    try {
      const loaded = await api.task(sourceWorkspaceId, taskId);
      if (
        useAppStore.getState().workspaceId !== sourceWorkspaceId ||
        activationSequence.current !== requestId ||
        loaded.workspace_id !== sourceWorkspaceId
      ) return;
      setTask(loaded);
      setError("");
    } catch (reason) {
      if (
        useAppStore.getState().workspaceId !== sourceWorkspaceId ||
        activationSequence.current !== requestId
      ) return;
      setError(reason instanceof ApiError ? reason.message : "任务载入失败。 ");
    }
  };

  if (!task) {
    return (
      <div className="tasksPage">
        <div className="pageHeading"><div><h1>任务</h1><p>把已核对的页面和文本证据收集到同一任务中。</p></div></div>
        <form className="newTaskBar" onSubmit={(event) => { event.preventDefault(); create.mutate({ sourceWorkspaceId: workspaceId, taskTitle: title, taskGoal: goal, requestId: ++activationSequence.current }); }}>
          <FilePlus2 size={20} />
          <input aria-label="任务名称" value={title} onChange={(event) => setTitle(event.target.value)} />
          <input aria-label="任务目标" value={goal} onChange={(event) => setGoal(event.target.value)} placeholder="目标（可选）" />
          <button className="primaryButton" disabled={!title.trim() || create.isPending}>{create.isPending ? <LoaderCircle className="spin" size={17} /> : <Plus size={17} />}新建</button>
        </form>
        {error && <div className="errorBox" role="alert">{error}</div>}
        <div className="taskSummaryList">
          {summaries.data?.map((item) => (
            <button key={item.task_id} onClick={() => void loadTask(item.task_id)}>
              <FolderOpen size={18} />
              <span><strong>{item.title}</strong><small>{item.goal || "未填写目标"}</small></span>
              <span>{item.item_count} 条证据</span>
              {item.unresolved_count > 0 && <span className="unresolvedText">{item.unresolved_count} 条待确认来源</span>}
              <small>{relativeTime(item.updated_at)}</small>
            </button>
          ))}
          {summaries.data?.length === 0 && <div className="inlineEmpty"><FolderOpen size={23} /><span>还没有任务。可以先搜索并加入第一条证据。</span></div>}
        </div>
      </div>
    );
  }

  const returnToTaskList = async () => {
    const sourceWorkspaceId = task.workspace_id;
    const sourceTaskId = task.task_id;
    setReturningToList(true);
    setError("");
    try {
      const state = useAppStore.getState();
      if (state.taskDirty) {
        await flushActiveTask(queryClient, sourceWorkspaceId, sourceTaskId);
      }
      const current = useAppStore.getState();
      if (current.workspaceId === sourceWorkspaceId && current.activeTask?.task_id === sourceTaskId) {
        setTask(null);
      }
    } catch (reason) {
      const current = useAppStore.getState();
      if (current.workspaceId !== sourceWorkspaceId || current.activeTask?.task_id !== sourceTaskId) return;
      setError(reason instanceof ApiError ? reason.message : "任务没有保存，仍保留在编辑器中。");
    } finally {
      setReturningToList(false);
    }
  };
  const archive = async () => {
    const sourceWorkspaceId = task.workspace_id;
    const sourceTaskId = task.task_id;
    setArchiving(true);
    setError("");
    try {
      const latest = await flushActiveTask(queryClient, task.workspace_id, task.task_id);
      await api.archiveTask(latest);
      clearLocalDraft(latest);
      const state = useAppStore.getState();
      if (state.workspaceId === sourceWorkspaceId && state.activeTask?.task_id === sourceTaskId) {
        setTask(null);
      }
      await queryClient.invalidateQueries({ queryKey: ["tasks", sourceWorkspaceId] });
    } catch (reason) {
      if (useAppStore.getState().workspaceId !== sourceWorkspaceId) return;
      setError(reason instanceof ApiError ? reason.message : "任务没有归档，请重试。");
    } finally {
      setArchiving(false);
    }
  };
  const exportMarkdown = async () => {
    const sourceWorkspaceId = task.workspace_id;
    const sourceTaskId = task.task_id;
    setExporting(true);
    setError("");
    try {
      const latest = await flushActiveTask(queryClient, task.workspace_id, task.task_id);
      const current = useAppStore.getState();
      if (current.workspaceId !== sourceWorkspaceId || current.activeTask?.task_id !== sourceTaskId) return;
      const markdown = await api.taskMarkdown(latest);
      const afterMarkdown = useAppStore.getState();
      if (afterMarkdown.workspaceId !== sourceWorkspaceId || afterMarkdown.activeTask?.task_id !== sourceTaskId) return;
      await saveTextFile(`${safeFileName(latest.title)}.md`, markdown);
    } catch (reason) {
      if (useAppStore.getState().workspaceId !== sourceWorkspaceId) return;
      setError(reason instanceof ApiError ? reason.message : "导出没有完成。 ");
    } finally {
      setExporting(false);
    }
  };
  const recoverDraft = async () => {
    const sourceWorkspaceId = task.workspace_id;
    const sourceTaskId = task.task_id;
    setResolvingConflict(true);
    setError("");
    try {
      const draft = loadLocalDraft(task);
      const authoritative = await api.task(sourceWorkspaceId, sourceTaskId);
      const state = useAppStore.getState();
      if (state.workspaceId !== sourceWorkspaceId || state.activeTask?.task_id !== sourceTaskId) return;
      if (!draft) {
        setTask(authoritative);
        setError("没有找到可恢复的本地草稿，已载入服务器版本。");
        return;
      }
      setTask(rebaseTaskDraft(draft, authoritative), true);
    } catch (reason) {
      if (useAppStore.getState().workspaceId !== sourceWorkspaceId) return;
      setError(reason instanceof ApiError ? reason.message : "本地草稿暂时无法恢复。");
    } finally {
      setResolvingConflict(false);
    }
  };
  const discardDraft = async () => {
    const sourceWorkspaceId = task.workspace_id;
    const sourceTaskId = task.task_id;
    setResolvingConflict(true);
    setError("");
    try {
      const authoritative = await api.task(sourceWorkspaceId, sourceTaskId);
      const state = useAppStore.getState();
      if (state.workspaceId !== sourceWorkspaceId || state.activeTask?.task_id !== sourceTaskId) return;
      clearLocalDraft(authoritative);
      setTask(authoritative);
    } catch (reason) {
      if (useAppStore.getState().workspaceId !== sourceWorkspaceId) return;
      setError(reason instanceof ApiError ? reason.message : "最新任务版本暂时无法载入。");
    } finally {
      setResolvingConflict(false);
    }
  };
  const removeItem = (itemId: string) => updateTask((value) => ({
    ...value,
    items: value.items.filter((item) => item.item_id !== itemId),
  }));
  const updateItem = (itemId: string, changes: Partial<WorkspaceTaskItem>) => updateTask((value) => ({
    ...value,
    items: value.items.map((item) => item.item_id === itemId ? { ...item, ...changes } : item),
  }));
  const sortedSlots = [...task.slots].sort((left, right) => left.position - right.position);

  return (
    <div className="taskEditor">
      <div className="taskEditorHeader">
        <button className="iconButton" disabled={returningToList || exporting || archiving} onClick={() => void returnToTaskList()} aria-label="返回任务列表" title="返回">{returningToList ? <LoaderCircle className="spin" size={19} /> : <ArrowLeft size={19} />}</button>
        <div className="taskIdentity">
          <input aria-label="任务名称" value={task.title} onChange={(event) => updateTask((value) => ({ ...value, title: event.target.value }))} />
          <textarea aria-label="任务目标" value={task.goal} onChange={(event) => updateTask((value) => ({ ...value, goal: event.target.value }))} placeholder="任务目标" />
        </div>
        <div className="taskHeaderActions">
          <span className={`saveIndicator save-${saveState}`}><Save size={15} />{saveState === "saving" ? "保存中" : saveState === "offline" ? "本地草稿" : saveState === "conflict" ? "需要重新载入" : taskDirty ? "等待保存" : "已保存"}</span>
          <button className="secondaryButton" onClick={() => setPage("search")}><Search size={17} />继续搜索</button>
          <button className="primaryButton" disabled={exporting || archiving || saveState === "conflict"} onClick={() => void exportMarkdown()}>{exporting ? <LoaderCircle className="spin" size={17} /> : <Download size={17} />}导出</button>
        </div>
      </div>
      {error && <div className="errorBox" role="alert">{error}</div>}
      {saveState === "conflict" && (
        <div className="conflictNotice" role="alert">
          <span>任务已在其他窗口更新。可以把本地草稿应用到最新版本，或放弃本地草稿。</span>
          <button className="secondaryButton smallButton" disabled={resolvingConflict} onClick={() => void recoverDraft()}>恢复本地草稿</button>
          <button className="textButton smallButton dangerText" disabled={resolvingConflict} onClick={() => void discardDraft()}>放弃本地草稿</button>
        </div>
      )}
      <div className="taskStats"><span>{task.items.length} 条证据</span><span>{task.items.filter((item) => item.source_status === "source_unconfirmed" || item.review_state === "pending").length} 条待核验</span><button className="textButton dangerText" disabled={archiving || exporting || saveState === "conflict"} onClick={() => void archive()}>{archiving ? <LoaderCircle className="spin" size={16} /> : <Archive size={16} />}归档</button></div>
      <div className="slotList">
        {sortedSlots.map((slot) => (
          <TaskSlot key={slot.slot_id} slot={slot} taskItems={task.items} slots={sortedSlots} updateItem={updateItem} removeItem={removeItem} />
        ))}
      </div>
    </div>
  );
}

function TaskSlot({
  slot,
  taskItems,
  slots,
  updateItem,
  removeItem,
}: {
  slot: WorkspaceTaskSlot;
  taskItems: WorkspaceTaskItem[];
  slots: WorkspaceTaskSlot[];
  updateItem: (itemId: string, changes: Partial<WorkspaceTaskItem>) => void;
  removeItem: (itemId: string) => void;
}) {
  const items = taskItems.filter((item) => item.slot_id === slot.slot_id).sort((left, right) => left.position - right.position);
  return (
    <section className="taskSlot">
      <div className="slotHeader"><div><h2>{slot.name}</h2>{slot.description && <p>{slot.description}</p>}</div><span>{items.length} 条</span></div>
      {items.length === 0 ? <div className="slotEmpty">暂无证据</div> : (
        <div className="taskItemList">
          {items.map((item) => {
            const sourceUnconfirmed = item.source_status === "source_unconfirmed";
            return (
              <article className="taskItem" key={item.item_id}>
                <span className={sourceUnconfirmed || item.review_state === "pending" ? "pendingDot" : "confirmedDot"} />
                <div className="taskItemBody">
                  <strong>{item.name}</strong>
                  <small>{item.page_number ? `第 ${item.page_number} 页 · ` : ""}{item.relative_path}</small>
                  {item.excerpt && <p>{item.excerpt}</p>}
                </div>
                <select value={item.slot_id} onChange={(event) => updateItem(item.item_id, { slot_id: event.target.value })} aria-label={`${item.name} 所属分组`}>
                  {slots.map((candidate) => <option key={candidate.slot_id} value={candidate.slot_id}>{candidate.name}</option>)}
                </select>
                {sourceUnconfirmed ? (
                  <span className="reviewToggle unresolvedText" role="status">来源待重新确认</span>
                ) : (
                  <label className="reviewToggle"><input type="checkbox" checked={item.review_state === "confirmed"} onChange={(event) => updateItem(item.item_id, { review_state: event.target.checked ? "confirmed" : "pending" })} />{item.review_state === "confirmed" ? <Check size={15} /> : null}<span>{item.review_state === "confirmed" ? "已确认" : "待核验"}</span></label>
                )}
                <button className="iconButton" onClick={() => removeItem(item.item_id)} aria-label={`移除 ${item.name}`} title="移除"><Trash2 size={16} /></button>
              </article>
            );
          })}
        </div>
      )}
    </section>
  );
}
