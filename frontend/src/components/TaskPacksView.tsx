import { useEffect, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Archive,
  ArrowLeft,
  Check,
  BookOpen,
  Download,
  ExternalLink,
  FileArchive,
  FilePlus2,
  FolderOpen,
  LoaderCircle,
  Plus,
  RefreshCw,
  Save,
  Search,
  Sparkles,
  Trash2,
} from "lucide-react";
import { ApiError, api } from "../api";
import {
  openLocalUri,
  revealSavedFile,
  saveBlobFile,
  saveExportFile,
  saveTextFile,
} from "../bridge";
import { waitForJob } from "../jobs";
import {
  clearLocalDraft,
  loadLocalDraft,
  rebaseTaskDraft,
  useAppStore,
} from "../store";
import { flushActiveTask } from "../taskPersistence";
import type {
  CitationRecord,
  CitationStyle,
  ExportArtifact,
  ResearchTaskProposal,
  ServiceJob,
  TaskTemplateId,
  WorkspaceTask,
  WorkspaceTaskItem,
  WorkspaceTaskSlot,
} from "../types";
import {
  hasFreshnessIssue,
  relativeTime,
  safeFileName,
  taskSummaryIssueCount,
} from "../utils";
import { locatorLabel } from "./researchLabels";

const taskTemplates: Array<{ id: TaskTemplateId; name: string; description: string; title: string }> = [
  { id: "literature_review", name: "文献综述", description: "按背景、方法、结论、相反证据和研究缺口整理。", title: "新的文献综述" },
  { id: "course_report", name: "课程报告", description: "围绕论点、材料、分析和参考资料组织证据。", title: "新的课程报告" },
  { id: "free_research", name: "自由研究", description: "从空白分组开始，自行定义研究结构。", title: "新的研究资料包" },
];

export function TaskPacksView() {
  const workspaceId = useAppStore((state) => state.workspaceId);
  const task = useAppStore((state) => state.activeTask);
  const setTask = useAppStore((state) => state.setTask);
  const updateTask = useAppStore((state) => state.updateTask);
  const saveState = useAppStore((state) => state.saveState);
  const taskDirty = useAppStore((state) => state.taskDirty);
  const setPage = useAppStore((state) => state.setPage);
  const queryClient = useQueryClient();
  const [templateId, setTemplateId] = useState<TaskTemplateId>("literature_review");
  const [title, setTitle] = useState("新的文献综述");
  const [goal, setGoal] = useState("");
  const [error, setError] = useState("");
  const [exporting, setExporting] = useState(false);
  const [savedExport, setSavedExport] = useState<ExportArtifact | null>(null);
  const [savedExportName, setSavedExportName] = useState("");
  const [includeSources, setIncludeSources] = useState(false);
  const [revalidating, setRevalidating] = useState(false);
  const [proposal, setProposal] = useState<ResearchTaskProposal | null>(null);
  const [archiving, setArchiving] = useState(false);
  const [archiveConfirmation, setArchiveConfirmation] = useState(false);
  const [recentlyArchived, setRecentlyArchived] = useState<WorkspaceTask | null>(null);
  const [restoringArchive, setRestoringArchive] = useState(false);
  const [returningToList, setReturningToList] = useState(false);
  const [resolvingConflict, setResolvingConflict] = useState(false);
  const [openedEvidence, setOpenedEvidence] = useState<Set<string>>(new Set());
  const activationSequence = useRef(0);
  const summaries = useQuery({
    queryKey: ["tasks", workspaceId],
    queryFn: () => api.tasks(workspaceId),
    enabled: Boolean(workspaceId),
  });
  useEffect(() => {
    activationSequence.current += 1;
    setTemplateId("literature_review");
    setTitle("新的文献综述");
    setGoal("");
    setError("");
    setSavedExport(null);
    setSavedExportName("");
    setArchiveConfirmation(false);
    setRecentlyArchived(null);
    setOpenedEvidence(new Set());
  }, [workspaceId]);
  useEffect(() => {
    setArchiveConfirmation(false);
  }, [task?.task_id]);
  const create = useMutation({
    mutationFn: ({ sourceWorkspaceId, taskTitle, taskGoal, template }: {
      sourceWorkspaceId: string;
      taskTitle: string;
      taskGoal: string;
      template: TaskTemplateId;
      requestId: number;
    }) => api.createTask(sourceWorkspaceId, taskTitle, taskGoal, template),
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
      setError(reason instanceof ApiError ? reason.message : "资料包创建失败。");
    },
  });
  const propose = useMutation({
    mutationFn: async () => {
      const started = await api.startResearchProposal(workspaceId, goal, title, templateId);
      queryClient.setQueryData<ServiceJob[]>(["jobs", workspaceId], (current = []) => [
        started,
        ...current.filter((item) => item.job_id !== started.job_id),
      ]);
      const completed = await waitForJob(started, {
        onUpdate: (job) => queryClient.setQueryData<ServiceJob[]>(
          ["jobs", workspaceId],
          (current = []) => [job, ...current.filter((item) => item.job_id !== job.job_id)],
        ),
      });
      return completed.result.proposal as ResearchTaskProposal;
    },
    onSuccess: (value) => { setProposal(value); setError(""); },
    onError: (reason) => setError(reason instanceof ApiError ? reason.message : "AI 资料提案生成失败。"),
  });
  const confirmProposal = useMutation({
    mutationFn: (value: ResearchTaskProposal) => api.confirmResearchProposal(workspaceId, value),
    onSuccess: (created) => {
      setProposal(null);
      setTask(created);
      void queryClient.invalidateQueries({ queryKey: ["tasks", workspaceId] });
    },
    onError: (reason) => setError(reason instanceof ApiError ? reason.message : "资料提案没有保存。"),
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
      setError(reason instanceof ApiError ? reason.message : "资料包载入失败。 ");
    }
  };

  const restoreRecentlyArchived = async () => {
    if (!recentlyArchived) return;
    const archived = recentlyArchived;
    setRestoringArchive(true);
    setError("");
    try {
      const restored = await api.saveTask({ ...archived, lifecycle: "saved" });
      if (useAppStore.getState().workspaceId !== archived.workspace_id) return;
      setRecentlyArchived(null);
      setTask(restored);
      await queryClient.invalidateQueries({ queryKey: ["tasks", archived.workspace_id] });
    } catch (reason) {
      if (useAppStore.getState().workspaceId !== archived.workspace_id) return;
      setError(reason instanceof ApiError ? reason.message : "资料包暂时无法恢复，请重试。");
    } finally {
      setRestoringArchive(false);
    }
  };

  if (!task) {
    return (
      <div className="tasksPage">
        <div className="pageHeading"><div><h1>资料包</h1><p>把可核验的来源、摘录和引用整理成一份研究成果底稿。</p></div></div>
        {recentlyArchived && (
          <div className="successBox exportSuccess" role="status">
            <Archive size={17} />
            <span><strong>“{recentlyArchived.title}”已归档</strong><small>已从活动资料包列表移除，可以立即恢复。</small></span>
            <button className="secondaryButton smallButton" disabled={restoringArchive} onClick={() => void restoreRecentlyArchived()}>
              {restoringArchive ? <LoaderCircle className="spin" size={15} /> : <RefreshCw size={15} />}
              撤销归档
            </button>
          </div>
        )}
        <form className="newPackPanel" onSubmit={(event) => { event.preventDefault(); create.mutate({ sourceWorkspaceId: workspaceId, taskTitle: title, taskGoal: goal, template: templateId, requestId: ++activationSequence.current }); }}>
          <div className="templateChooser" role="radiogroup" aria-label="资料包模板">
            {taskTemplates.map((template) => (
              <button
                type="button"
                role="radio"
                aria-checked={templateId === template.id}
                key={template.id}
                className={templateId === template.id ? "templateActive" : ""}
                onClick={() => {
                  const currentDefault = taskTemplates.find((item) => item.id === templateId)?.title;
                  setTemplateId(template.id);
                  if (!title.trim() || title === currentDefault) setTitle(template.title);
                }}
              >
                <BookOpen size={18} />
                <span><strong>{template.name}</strong><small>{template.description}</small></span>
                {templateId === template.id && <Check size={16} />}
              </button>
            ))}
          </div>
          <div className="newTaskBar">
            <FilePlus2 size={20} />
            <input aria-label="资料包名称" value={title} onChange={(event) => setTitle(event.target.value)} />
            <input aria-label="研究目标" value={goal} onChange={(event) => setGoal(event.target.value)} placeholder="研究目标（可选）" />
            <button className="primaryButton" disabled={!title.trim() || create.isPending}>{create.isPending ? <LoaderCircle className="spin" size={17} /> : <Plus size={17} />}创建资料包</button>
            <button type="button" className="secondaryButton" disabled={!goal.trim() || propose.isPending} onClick={() => propose.mutate()}>{propose.isPending ? <LoaderCircle className="spin" size={17} /> : <Sparkles size={17} />}AI 生成资料包</button>
          </div>
        </form>
        {error && <div className="errorBox" role="alert">{error}</div>}
        {proposal && <ResearchProposalPreview proposal={proposal} onChange={setProposal} onConfirm={() => confirmProposal.mutate(proposal)} confirming={confirmProposal.isPending} />}
        <div className="taskSummaryList">
          {summaries.data?.map((item) => {
            const reviewCount = taskSummaryIssueCount(item);
            return (
              <button key={item.task_id} onClick={() => void loadTask(item.task_id)}>
                <FolderOpen size={18} />
                <span><strong>{item.title}</strong><small>{item.goal || "未填写目标"}</small></span>
                <span>{item.item_count} 条证据</span>
                {reviewCount > 0 && <span className="unresolvedText">{reviewCount} 条待复核</span>}
                <small>{relativeTime(item.updated_at)}</small>
              </button>
            );
          })}
          {summaries.data?.length === 0 && <div className="inlineEmpty"><FolderOpen size={23} /><span>还没有资料包。选择模板创建，或先搜索并收集第一条证据。</span></div>}
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
    setArchiveConfirmation(false);
    setArchiving(true);
    setError("");
    try {
      const latest = await flushActiveTask(queryClient, task.workspace_id, task.task_id);
      const archived = await api.archiveTask(latest);
      clearLocalDraft(archived);
      const state = useAppStore.getState();
      if (state.workspaceId === sourceWorkspaceId && state.activeTask?.task_id === sourceTaskId) {
        setRecentlyArchived(archived);
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
  const exportResearchPack = async () => {
    const sourceWorkspaceId = task.workspace_id;
    const sourceTaskId = task.task_id;
    setExporting(true);
    setError("");
    try {
      const latest = await flushActiveTask(queryClient, task.workspace_id, task.task_id);
      const current = useAppStore.getState();
      if (current.workspaceId !== sourceWorkspaceId || current.activeTask?.task_id !== sourceTaskId) return;
      const citationStyle = latest.citation_style ?? "gb-t-7714-2015";
      const started = await api.startTaskExport(latest, {
        citation_style: citationStyle,
        include_sources: includeSources,
      });
      queryClient.setQueryData<ServiceJob[]>(["jobs", sourceWorkspaceId], (currentJobs = []) => [
        started,
        ...currentJobs.filter((item) => item.job_id !== started.job_id),
      ]);
      const completed = await waitForJob(started, {
        onUpdate: (job) => queryClient.setQueryData<ServiceJob[]>(
          ["jobs", sourceWorkspaceId],
          (currentJobs = []) => [
            job,
            ...currentJobs.filter((item) => item.job_id !== job.job_id),
          ],
        ),
      });
      const artifact = completed.result as unknown as ExportArtifact;
      const afterExport = useAppStore.getState();
      if (afterExport.workspaceId !== sourceWorkspaceId || afterExport.activeTask?.task_id !== sourceTaskId) return;
      const suggestedName = artifact.file_name || `${safeFileName(latest.title)}.zip`;
      const nativeResult = await saveExportFile(
        sourceWorkspaceId,
        artifact.artifact_id,
        suggestedName,
      );
      if (nativeResult === null) {
        const pack = await api.exportArtifact(sourceWorkspaceId, artifact.artifact_id);
        await saveBlobFile(suggestedName, pack);
        setSavedExportName(suggestedName);
      } else if (!nativeResult.saved) {
        return;
      } else {
        setSavedExportName(nativeResult.file || suggestedName);
      }
      setSavedExport(artifact);
    } catch (reason) {
      if (useAppStore.getState().workspaceId !== sourceWorkspaceId) return;
      setError(reason instanceof ApiError ? reason.message : "研究资料包导出没有完成。 ");
    } finally {
      setExporting(false);
    }
  };
  const revalidateSources = async () => {
    const sourceWorkspaceId = task.workspace_id;
    const sourceTaskId = task.task_id;
    setRevalidating(true);
    setError("");
    try {
      const latest = await flushActiveTask(queryClient, sourceWorkspaceId, sourceTaskId);
      const refreshed = await api.revalidateTask(latest);
      const state = useAppStore.getState();
      if (state.workspaceId !== sourceWorkspaceId || state.activeTask?.task_id !== sourceTaskId) return;
      setTask(refreshed);
      await queryClient.invalidateQueries({ queryKey: ["tasks", sourceWorkspaceId] });
    } catch (reason) {
      if (useAppStore.getState().workspaceId !== sourceWorkspaceId) return;
      setError(reason instanceof ApiError ? reason.message : "来源复核没有完成。 ");
    } finally {
      setRevalidating(false);
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
  const addSlot = () => updateTask((value) => ({
    ...value,
    slots: [...value.slots, {
      slot_id: crypto.randomUUID(),
      name: "新分组",
      description: "",
      position: value.slots.length,
      required: false,
    }],
  }));
  const updateSlot = (slotId: string, changes: Partial<WorkspaceTaskSlot>) => updateTask((value) => ({
    ...value,
    slots: value.slots.map((slot) => slot.slot_id === slotId ? { ...slot, ...changes } : slot),
  }));
  const removeSlot = (slotId: string) => updateTask((value) => ({
    ...value,
    slots: value.slots.filter((slot) => slot.slot_id !== slotId).map((slot, position) => ({ ...slot, position })),
  }));
  const sortedSlots = [...task.slots].sort((left, right) => left.position - right.position);
  const freshnessIssues = task.items.filter((item) =>
    item.source_status === "source_unconfirmed" ||
    hasFreshnessIssue(item.freshness_status)
  ).length;
  const unavailableCount = task.items.filter((item) =>
    item.source_status === "source_unconfirmed" ||
    item.freshness_status === "missing" ||
    item.freshness_status === "unavailable"
  ).length;
  const changedCount = task.items.filter((item) => hasFreshnessIssue(item.freshness_status) &&
    item.freshness_status !== "missing" && item.freshness_status !== "unavailable").length;
  const unverifiedCount = task.items.filter((item) => item.review_state !== "confirmed").length;
  const eligibleSourceCount = task.items.filter((item) =>
    item.review_state === "confirmed" &&
    item.source_status === "resolved" &&
    (!item.freshness_status || item.freshness_status === "current") &&
    (item.verified_content_hash ?? item.confirmed_content_hash) === item.content_hash
  ).length;
  const notCopiedCount = includeSources
    ? task.items.length - eligibleSourceCount
    : task.items.length;

  const openEvidence = async (item: WorkspaceTaskItem) => {
    try {
      const target = await api.openTarget(task.workspace_id, item.document_id);
      await openLocalUri(target.uri);
      setOpenedEvidence((current) => new Set(current).add(`${item.item_id}:${item.content_hash}`));
      setError("");
    } catch (reason) {
      setError(reason instanceof ApiError ? reason.message : "证据原文无法打开，暂时不能确认。");
    }
  };
  const confirmEvidence = (item: WorkspaceTaskItem) => updateItem(item.item_id, {
    review_state: "confirmed",
    verified_content_hash: item.content_hash,
    confirmed_content_hash: item.content_hash,
    verified_at: new Date().toISOString(),
  });
  const markAllPending = () => updateTask((value) => ({
    ...value,
    items: value.items.map((item) => ({
      ...item,
      review_state: "pending" as const,
      verified_content_hash: undefined,
      confirmed_content_hash: undefined,
      verified_at: undefined,
    })),
  }));

  return (
    <div className="taskEditor">
      <div className="taskEditorHeader">
        <button className="iconButton" disabled={returningToList || exporting || archiving} onClick={() => void returnToTaskList()} aria-label="返回资料包列表" title="返回">{returningToList ? <LoaderCircle className="spin" size={19} /> : <ArrowLeft size={19} />}</button>
        <div className="taskIdentity">
          <input aria-label="资料包名称" value={task.title} onChange={(event) => updateTask((value) => ({ ...value, title: event.target.value }))} />
          <textarea aria-label="研究目标" value={task.goal} onChange={(event) => updateTask((value) => ({ ...value, goal: event.target.value }))} placeholder="研究目标" />
        </div>
        <div className="taskHeaderActions">
          <span className={`saveIndicator save-${saveState}`}><Save size={15} />{saveState === "saving" ? "保存中" : saveState === "offline" ? "本地草稿" : saveState === "conflict" ? "需要重新载入" : taskDirty ? "等待保存" : "已保存"}</span>
          <button className="secondaryButton" onClick={() => setPage("search")}><Search size={17} />继续搜索</button>
          <button className="secondaryButton" disabled={exporting || archiving || saveState === "conflict"} onClick={() => void exportMarkdown()}><Download size={17} />Markdown</button>
        </div>
      </div>
      {error && <div className="errorBox" role="alert">{error}</div>}
      {saveState === "conflict" && (
        <div className="conflictNotice" role="alert">
          <span>资料包已在其他窗口更新。可以把本地草稿应用到最新版本，或放弃本地草稿。</span>
          <button className="secondaryButton smallButton" disabled={resolvingConflict} onClick={() => void recoverDraft()}>恢复本地草稿</button>
          <button className="textButton smallButton dangerText" disabled={resolvingConflict} onClick={() => void discardDraft()}>放弃本地草稿</button>
        </div>
      )}
      <div className="taskStats">
        <span>{task.items.length} 条证据</span>
        <span>{task.items.filter((item) => item.review_state === "pending" || item.source_status === "source_unconfirmed" || (item.freshness_status && item.freshness_status !== "current")).length} 条待核验</span>
        <span className={freshnessIssues > 0 ? "unresolvedText" : ""}>{freshnessIssues} 条来源待复核</span>
        {freshnessIssues > 0 && <button className="textButton smallButton" disabled={revalidating || saveState === "conflict"} onClick={() => void revalidateSources()}>{revalidating ? <LoaderCircle className="spin" size={15} /> : <RefreshCw size={15} />}重新核验来源</button>}
        {task.items.length > 0 && <button className="textButton smallButton" disabled={saveState === "conflict"} onClick={markAllPending}>全部标为待核验</button>}
        <button className="textButton dangerText" disabled={archiving || exporting || archiveConfirmation || saveState === "conflict"} onClick={() => setArchiveConfirmation(true)}>{archiving ? <LoaderCircle className="spin" size={16} /> : <Archive size={16} />}归档</button>
      </div>
      {archiveConfirmation && (
        <div className="conflictNotice" role="alert">
          <span><strong>归档“{task.title}”？</strong> 归档后会从活动资料包列表移除；完成后仍可立即撤销。</span>
          <button className="secondaryButton smallButton" disabled={archiving} onClick={() => void archive()}>{archiving ? <LoaderCircle className="spin" size={15} /> : <Archive size={15} />}确认归档</button>
          <button className="textButton smallButton" disabled={archiving} onClick={() => setArchiveConfirmation(false)}>取消</button>
        </div>
      )}
      <section className="packExportBar" aria-label="研究资料包导出">
        <div><FileArchive size={19} /><span><strong>研究资料包</strong><small>包含 research.md、references.bib、task.json 和 manifest.json</small></span></div>
        <label>引用样式<select value={task.citation_style ?? "gb-t-7714-2015"} onChange={(event) => updateTask((value) => ({ ...value, citation_style: event.target.value as CitationStyle }))}><option value="gb-t-7714-2015">GB/T 7714-2015</option><option value="apa">APA</option></select></label>
        <label className="includeSources" title="只复制已人工核验、哈希未变化且当前可访问的来源"><input type="checkbox" checked={includeSources} onChange={(event) => setIncludeSources(event.target.checked)} />包含符合条件的原件</label>
        <button className="primaryButton" disabled={exporting || archiving || saveState === "conflict"} onClick={() => void exportResearchPack()}>{exporting ? <LoaderCircle className="spin" size={17} /> : <Download size={17} />}导出研究包</button>
        <div className="exportChecks">
          <span>{unverifiedCount} 未核验</span>
          <span>{changedCount} 已变化</span>
          <span>{unavailableCount} 不可访问</span>
          <span>{notCopiedCount} 不复制原件</span>
        </div>
      </section>
      {savedExport && (
        <div className="successBox exportSuccess" role="status">
          <Check size={17} />
          <span><strong>研究包已保存</strong><small>{savedExportName} · 工件保留至 {relativeTime(savedExport.expires_at)}</small></span>
          <button className="secondaryButton smallButton" onClick={() => void revealSavedFile(savedExport.artifact_id)}>打开所在位置</button>
        </div>
      )}
      <div className="slotList">
        {sortedSlots.map((slot) => (
          <TaskSlot
            key={slot.slot_id}
            slot={slot}
            taskItems={task.items}
            slots={sortedSlots}
            openedEvidence={openedEvidence}
            updateItem={updateItem}
            removeItem={removeItem}
            updateSlot={updateSlot}
            removeSlot={removeSlot}
            openEvidence={openEvidence}
            confirmEvidence={confirmEvidence}
          />
        ))}
        <button className="textButton addSlotButton" onClick={addSlot}><Plus size={16} />添加分组</button>
      </div>
    </div>
  );
}

function TaskSlot({
  slot,
  taskItems,
  slots,
  openedEvidence,
  updateItem,
  removeItem,
  updateSlot,
  removeSlot,
  openEvidence,
  confirmEvidence,
}: {
  slot: WorkspaceTaskSlot;
  taskItems: WorkspaceTaskItem[];
  slots: WorkspaceTaskSlot[];
  openedEvidence: Set<string>;
  updateItem: (itemId: string, changes: Partial<WorkspaceTaskItem>) => void;
  removeItem: (itemId: string) => void;
  updateSlot: (slotId: string, changes: Partial<WorkspaceTaskSlot>) => void;
  removeSlot: (slotId: string) => void;
  openEvidence: (item: WorkspaceTaskItem) => Promise<void>;
  confirmEvidence: (item: WorkspaceTaskItem) => void;
}) {
  const items = taskItems.filter((item) => item.slot_id === slot.slot_id).sort((left, right) => left.position - right.position);
  return (
    <section className="taskSlot">
      <div className="slotHeader">
        <div className="slotIdentity">
          <input aria-label={`${slot.name} 分组名称`} value={slot.name} onChange={(event) => updateSlot(slot.slot_id, { name: event.target.value })} />
          <input aria-label={`${slot.name} 分组说明`} value={slot.description} onChange={(event) => updateSlot(slot.slot_id, { description: event.target.value })} placeholder="分组说明（可选）" />
        </div>
        <span>{items.length} 条</span>
        {!slot.required && items.length === 0 && <button className="iconButton" onClick={() => removeSlot(slot.slot_id)} aria-label={`删除 ${slot.name} 分组`} title="删除空分组"><Trash2 size={15} /></button>}
      </div>
      {items.length === 0 ? <div className="slotEmpty">暂无证据</div> : (
        <div className="taskItemList">
          {items.map((item) => {
            const sourceUnconfirmed = item.source_status === "source_unconfirmed";
            const freshnessIssue = hasFreshnessIssue(item.freshness_status);
            const opened = openedEvidence.has(`${item.item_id}:${item.content_hash}`);
            return (
              <article className={`taskItem ${freshnessIssue ? "taskItemStale" : ""}`} key={item.item_id}>
                <span className={sourceUnconfirmed || freshnessIssue || item.review_state === "pending" ? "pendingDot" : "confirmedDot"} />
                <div className="taskItemBody">
                  <strong>{item.name}</strong>
                  <small>{locatorLabel(item.locator, item.page_number) ? `${locatorLabel(item.locator, item.page_number)} · ` : ""}{item.source_ref?.virtual_path || item.relative_path}</small>
                  {item.excerpt && <p>{item.excerpt}</p>}
                  <CitationEditor item={item} updateItem={updateItem} />
                </div>
                <select value={item.slot_id} onChange={(event) => updateItem(item.item_id, { slot_id: event.target.value })} aria-label={`${item.name} 所属分组`}>
                  {slots.map((candidate) => <option key={candidate.slot_id} value={candidate.slot_id}>{candidate.name}</option>)}
                </select>
                <div className="evidenceReviewActions">
                  <button className="secondaryButton smallButton" disabled={sourceUnconfirmed} onClick={() => void openEvidence(item)}><ExternalLink size={14} />打开证据</button>
                  {sourceUnconfirmed || freshnessIssue ? (
                    <span className="unresolvedText" role="status">{freshnessLabel(item)}</span>
                  ) : item.review_state === "confirmed" ? (
                    <button className="textButton smallButton" onClick={() => updateItem(item.item_id, { review_state: "pending", verified_content_hash: undefined, confirmed_content_hash: undefined, verified_at: undefined })}><Check size={14} />已人工核验</button>
                  ) : (
                    <button className="secondaryButton smallButton" disabled={!opened} onClick={() => confirmEvidence(item)}>确认已对照原文</button>
                  )}
                </div>
                <button className="iconButton" onClick={() => removeItem(item.item_id)} aria-label={`移除 ${item.name}`} title="移除"><Trash2 size={16} /></button>
              </article>
            );
          })}
        </div>
      )}
    </section>
  );
}

function ResearchProposalPreview({
  proposal,
  onChange,
  onConfirm,
  confirming,
}: {
  proposal: ResearchTaskProposal;
  onChange: (value: ResearchTaskProposal) => void;
  onConfirm: () => void;
  confirming: boolean;
}) {
  const candidates = new Map(proposal.candidates.map((item) => [item.candidate_id, item]));
  const remove = (slotIndex: number, candidateId: string) => onChange({
    ...proposal,
    slots: proposal.slots.map((slot, index) => index === slotIndex
      ? { ...slot, candidate_ids: slot.candidate_ids.filter((value) => value !== candidateId) }
      : slot),
  });
  return (
    <section className="researchProposal">
      <div className="researchProposalHeader">
        <div><h2>{proposal.title}</h2><p>{proposal.summary || proposal.goal}</p></div>
        <button className="primaryButton" disabled={confirming} onClick={onConfirm}>{confirming ? <LoaderCircle className="spin" size={17} /> : <Check size={17} />}确认生成资料包</button>
      </div>
      {proposal.warnings.map((warning) => <div className="warningBox" role="status" key={warning}>{warning}</div>)}
      {proposal.gaps.length > 0 && <div className="researchGaps"><strong>待补足</strong>{proposal.gaps.map((gap) => <span key={gap}>{gap}</span>)}</div>}
      <div className="researchSlotPreview">
        {proposal.slots.map((slot, slotIndex) => (
          <section className="taskSlot" key={`${slot.name}-${slotIndex}`}>
            <div className="slotHeader"><div><h3>{slot.name}</h3><p>{slot.description}</p></div><span>{slot.candidate_ids.length} 条</span></div>
            {slot.candidate_ids.map((candidateId) => {
              const candidate = candidates.get(candidateId);
              if (!candidate) return null;
              return <article className="taskItem" key={candidateId}><div className="taskItemBody"><strong>{candidate.name}</strong><small>{locatorLabel(candidate.locator ?? null, candidate.page_number)} · {candidate.relative_path}</small><p>{candidate.excerpt}</p>{slot.rationales[candidateId] && <small>{slot.rationales[candidateId]}</small>}</div><button className="iconButton" onClick={() => remove(slotIndex, candidateId)} aria-label={`移除 ${candidate.name}`} title="移除"><Trash2 size={16} /></button></article>;
            })}
          </section>
        ))}
      </div>
    </section>
  );
}

function CitationEditor({
  item,
  updateItem,
}: {
  item: WorkspaceTaskItem;
  updateItem: (itemId: string, changes: Partial<WorkspaceTaskItem>) => void;
}) {
  const citation = citationValue(item);
  const updateCitation = (changes: Partial<CitationRecord>) => updateItem(item.item_id, {
    citation: { ...citation, ...changes },
  });
  return (
    <details className="citationEditor">
      <summary><BookOpen size={14} />引用信息{item.citation?.title ? <span>已填写</span> : <span>待补充</span>}</summary>
      <div className="citationGrid">
        <label className="citationWide">题名<input value={citation.title} onChange={(event) => updateCitation({ title: event.target.value })} /></label>
        <label className="citationWide">作者<input value={citation.authors.join("; ")} onChange={(event) => updateCitation({ authors: event.target.value.split(/[;；]/).map((value) => value.trim()).filter(Boolean) })} placeholder="多位作者用分号分隔" /></label>
        <label>年份<input value={citation.year} onChange={(event) => updateCitation({ year: event.target.value })} /></label>
        <label>载体<input value={citation.carrier} onChange={(event) => updateCitation({ carrier: event.target.value })} placeholder="期刊、图书、网页…" /></label>
        <label className="citationWide">出版信息<input value={citation.publication_title} onChange={(event) => updateCitation({ publication_title: event.target.value })} /></label>
        <label>页码<input value={citation.pages} onChange={(event) => updateCitation({ pages: event.target.value })} /></label>
        <label>DOI<input value={citation.doi} onChange={(event) => updateCitation({ doi: event.target.value })} /></label>
        <label className="citationWide">URL<input value={citation.url} onChange={(event) => updateCitation({ url: event.target.value })} /></label>
      </div>
    </details>
  );
}

function citationValue(item: WorkspaceTaskItem): CitationRecord {
  return item.citation ?? {
    title: item.name.replace(/\.[^.]+$/, ""),
    authors: [],
    year: "",
    carrier: "",
    publication_title: "",
    pages: item.page_number ? String(item.page_number) : "",
    doi: "",
    url: "",
    confidence: 0,
  };
}

function freshnessLabel(item: WorkspaceTaskItem): string {
  if (item.freshness_status === "stale" || item.freshness_status === "changed") return "来源已变化";
  if (item.freshness_status === "unavailable" || item.freshness_status === "missing") return "来源不可访问";
  if (item.freshness_status === "needs_review") return "来源待复核";
  return "来源待重新确认";
}
