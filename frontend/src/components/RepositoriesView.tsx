import { useEffect, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  AlertTriangle,
  CheckCircle2,
  File,
  LoaderCircle,
  RefreshCw,
  RotateCcw,
  Sparkles,
} from "lucide-react";
import { ApiError, api } from "../api";
import { useAppStore } from "../store";
import type { ServiceJob, Workspace, WorkspaceDocument } from "../types";
import { documentQualityLabel, formatBytes, relativeTime } from "../utils";
import {
  isActiveWorkspaceJob,
  latestWorkspaceJob,
  workspaceJobProgressText,
} from "../workspaceUi";
import { Onboarding } from "./Onboarding";

export function RepositoriesView({ workspace }: { workspace: Workspace }) {
  const workspaceId = useAppStore((state) => state.workspaceId);
  const setWorkspaceId = useAppStore((state) => state.setWorkspaceId);
  const queryClient = useQueryClient();
  const [notice, setNotice] = useState("");
  const [error, setError] = useState("");
  const [confirmAIIndex, setConfirmAIIndex] = useState(false);
  const [addingWorkspace, setAddingWorkspace] = useState(false);
  const settledJob = useRef("");
  const jobStatuses = useRef(new Map<string, ServiceJob["status"]>());
  const documents = useQuery({
    queryKey: ["documents", workspaceId],
    queryFn: () => api.documents(workspaceId),
    enabled: Boolean(workspaceId),
    refetchInterval: 20_000,
  });
  const jobs = useQuery({
    queryKey: ["jobs", workspaceId],
    queryFn: ({ signal }) => api.jobs(workspaceId, signal),
    enabled: Boolean(workspaceId),
    refetchInterval: (query) => query.state.data?.some(isActiveWorkspaceJob) ? 1_000 : 15_000,
  });
  const aiIndex = useQuery({
    queryKey: ["ai-index", workspaceId],
    queryFn: () => api.aiIndexStatus(workspaceId),
    enabled: Boolean(workspaceId),
    refetchInterval: 5_000,
  });
  const workspaceJobs = (jobs.data ?? []).filter((item) =>
    item.kind === "workspace_sync" || item.kind === "workspace_rebuild"
  );
  const latestJob = latestWorkspaceJob(workspaceJobs);
  const activeJob = latestJob && isActiveWorkspaceJob(latestJob) ? latestJob : undefined;
  const failedJob = latestJob?.status === "failed" ? latestJob : undefined;
  const partialFailureCount = latestJob?.status === "succeeded"
    ? latestJob.result.progress?.failed ?? 0
    : 0;

  const rememberJob = (job: ServiceJob) => {
    const jobWorkspaceId = job.repository_id || workspaceId;
    queryClient.setQueryData<ServiceJob[]>(["jobs", jobWorkspaceId], (current = []) => [
      job,
      ...current.filter((item) => item.job_id !== job.job_id),
    ]);
    void queryClient.invalidateQueries({ queryKey: ["jobs", jobWorkspaceId] });
  };

  useEffect(() => {
    setNotice("");
    setError("");
  }, [workspaceId]);

  useEffect(() => {
    if (!latestJob) return;
    const previousStatus = jobStatuses.current.get(latestJob.job_id);
    jobStatuses.current.set(latestJob.job_id, latestJob.status);
    if (latestJob.status !== "succeeded" && latestJob.status !== "failed") return;
    const signature = `${latestJob.job_id}:${latestJob.status}`;
    if (settledJob.current === signature) return;
    settledJob.current = signature;
    if (latestJob.status === "succeeded" && (previousStatus === "queued" || previousStatus === "running")) {
      setNotice((latestJob.result.progress?.failed ?? 0) > 0 ? "" : "后台处理完成，资料状态已更新。");
      setError("");
    }
    void queryClient.invalidateQueries({ queryKey: ["workspaces"] });
    void queryClient.invalidateQueries({ queryKey: ["documents", workspaceId] });
  }, [latestJob, queryClient, workspaceId]);

  const sync = useMutation({
    mutationFn: () => api.syncWorkspace(workspaceId),
    onMutate: async () => {
      setNotice("");
      setError("");
      await queryClient.cancelQueries({ queryKey: ["jobs", workspaceId] });
    },
    onSuccess: (job) => rememberJob(job),
    onError: (reason) => setError(reason instanceof ApiError ? reason.message : "同步没有开始。"),
  });
  const aiRun = useMutation({
    mutationFn: (retryFailed: boolean) => api.startAIIndex(workspaceId, {
      scope: "all",
      retry_failed: retryFailed,
    }),
    onSuccess: (job) => {
      rememberJob(job);
      void queryClient.invalidateQueries({ queryKey: ["ai-index", workspaceId] });
    },
    onError: (reason) => setError(reason instanceof ApiError ? reason.message : "AI 索引没有开始。"),
  });
  const reprocess = useMutation({
    mutationFn: (documentId: string) => api.reprocessDocument(workspaceId, documentId),
    onMutate: async () => {
      setNotice("");
      setError("");
      await queryClient.cancelQueries({ queryKey: ["jobs", workspaceId] });
    },
    onSuccess: (job) => rememberJob(job),
    onError: (reason) => setError(reason instanceof ApiError ? reason.message : "重新处理没有开始。"),
  });
  const health = workspace.health;
  const visibleFailureCount = partialFailureCount > 0 ? partialFailureCount : health.failed_count;
  const aiJob = jobs.data?.find((item) => item.kind === "workspace_ai_index" && (item.status === "queued" || item.status === "running"));
  const aiPendingCalls = aiIndex.data?.estimated_calls ?? 0;
  const aiFailedCount = (aiIndex.data?.failed_document_count ?? 0) +
    (aiIndex.data?.failed_folder_count ?? 0);

  return (
    <div className="documentsPage">
      <div className="pageHeading">
        <div><h1>资料</h1><p>{workspace.raw_path}</p></div>
        <button className="primaryButton" disabled={sync.isPending || Boolean(activeJob) || Boolean(aiJob)} onClick={() => sync.mutate()}>
          {sync.isPending || activeJob ? <LoaderCircle className="spin" size={17} /> : <RefreshCw size={17} />}
          {sync.isPending || activeJob ? "处理中" : "同步"}
        </button>
      </div>

      <div className="healthStrip">
        <div><strong>{health.document_count}</strong><span>文档</span></div>
        <div className="healthGood"><strong>{health.readable_count}</strong><span>正文可读</span></div>
        <div className="healthPartial"><strong>{health.partial_count}</strong><span>部分可读</span></div>
        <div className="healthLow"><strong>{health.low_quality_count}</strong><span>识别质量低</span></div>
        <div><strong>{health.metadata_only_count}</strong><span>仅文件信息</span></div>
        <div className="healthFailed"><strong>{health.failed_count}</strong><span>处理失败</span></div>
        {(health.queued_count ?? 0) > 0 && <div><strong>{health.queued_count}</strong><span>等待处理</span></div>}
        {(health.processing_count ?? 0) > 0 && <div><strong>{health.processing_count}</strong><span>处理中</span></div>}
      </div>
      <div className="syncLine"><CheckCircle2 size={15} />上次同步：{relativeTime(health.last_sync_at)}</div>
      {activeJob && <div className="jobStatusBox" role="status"><LoaderCircle className="spin" size={17} /><span>{workspaceJobProgressText(activeJob)}</span></div>}
      <section className="aiIndexPanel">
        <div className="settingsSectionTitle"><Sparkles size={18} /><div><h2>AI 资料索引</h2><span>{aiIndex.data ? `${aiIndex.data.indexed_document_count}/${aiIndex.data.document_count} 份资料卡，${aiIndex.data.indexed_folder_count}/${aiIndex.data.folder_count} 个文件夹卡` : "正在读取索引状态"}</span></div></div>
        <div className="aiIndexActions">
          <span>{aiPendingCalls ? `预计调用 ${aiPendingCalls} 次；确认后将自动完成全部范围` : "AI 索引已是最新"}</span>
          <div>
            {confirmAIIndex && aiPendingCalls > 0 ? (
              <>
                <button className="primaryButton smallButton" disabled={Boolean(aiJob) || aiRun.isPending} onClick={() => { setConfirmAIIndex(false); aiRun.mutate(false); }}>确认并补全</button>
                <button className="textButton smallButton" onClick={() => setConfirmAIIndex(false)}>取消</button>
              </>
            ) : (
              <button className="secondaryButton" disabled={Boolean(aiJob) || aiRun.isPending || !aiPendingCalls} onClick={() => setConfirmAIIndex(true)}>{aiJob || aiRun.isPending ? <LoaderCircle className="spin" size={16} /> : <Sparkles size={16} />}{aiJob ? "索引处理中" : "一键补全 AI 索引"}</button>
            )}
            {aiFailedCount > 0 && <button className="secondaryButton" disabled={Boolean(aiJob) || aiRun.isPending} onClick={() => aiRun.mutate(true)}><RotateCcw size={16} />单独重试失败项 ({aiFailedCount})</button>}
          </div>
        </div>
      </section>
      {notice && <div className="successBox" role="status">{notice}</div>}
      {visibleFailureCount > 0 && <div className="warningBox" role="status"><AlertTriangle size={16} /><span>{partialFailureCount > 0 ? "后台处理已完成，其中" : "当前有"} {visibleFailureCount} 个文件处理失败。可在下方重新处理。</span></div>}
      {error && <div className="errorBox" role="alert"><AlertTriangle size={16} />{error}</div>}
      {failedJob && <div className="errorBox" role="alert"><AlertTriangle size={16} /><span>后台处理失败。请检查原始资料是否可访问，然后重试。</span></div>}

      <section className="documentTable" aria-label="文档处理状态">
        <div className="documentTableHeader"><span>文件</span><span>正文质量</span><span>大小</span><span>操作</span></div>
        {documents.isLoading && <div className="tableLoading"><LoaderCircle className="spin" size={20} />正在读取文档状态</div>}
        {documents.data?.map((document) => (
          <div className="documentRow" key={document.document_id}>
            <File size={18} />
            <span className="documentIdentity"><strong>{document.name}</strong><small>{document.relative_path}</small></span>
            <span className={`qualityBadge quality-${document.processing_state === "failed" || document.indexing_state === "failed" ? "failed" : document.processing_state === "queued" || document.processing_state === "processing" ? "pending" : document.indexing_state === "metadata_only" ? "metadata" : document.readability}`} title={document.processing_error || ""}>
              {processingLabel(document)}
            </span>
            <span>{formatBytes(document.size_bytes)}{(document.ocr_pending_pages ?? 0) > 0 && <small className="ocrPending">{document.ocr_pending_pages} 页待 OCR</small>}</span>
            <button className="iconButton" disabled={reprocess.isPending || document.processing_state === "processing"} onClick={() => reprocess.mutate(document.document_id)} aria-label={(document.ocr_pending_pages ?? 0) > 0 ? `继续 OCR ${document.name}` : `重新处理 ${document.name}`} title={(document.ocr_pending_pages ?? 0) > 0 ? "继续 OCR" : "重新处理"}><RotateCcw size={16} /></button>
          </div>
        ))}
      </section>

      <details
        className="addWorkspacePanel"
        open={addingWorkspace}
        onToggle={(event) => setAddingWorkspace(event.currentTarget.open)}
      >
        <summary>添加另一个资料空间</summary>
        <Onboarding compact onCreated={(created, job) => {
          queryClient.setQueryData<Workspace[]>(["workspaces"], (current = []) => [
            created,
            ...current.filter((item) => item.workspace_id !== created.workspace_id),
          ]);
          queryClient.setQueryData<ServiceJob[]>(["jobs", created.workspace_id], [job]);
          setAddingWorkspace(false);
          setWorkspaceId(created.workspace_id);
          void queryClient.invalidateQueries({ queryKey: ["workspaces"] });
        }} />
      </details>
    </div>
  );
}

function processingLabel(document: WorkspaceDocument): string {
  if (document.processing_state === "queued") return "等待处理";
  if (document.processing_state === "processing") return "处理中";
  if (document.processing_state === "failed") return "处理失败";
  if ((document.ocr_pending_pages ?? 0) > 0) return `正文可用 · ${document.ocr_pending_pages} 页待 OCR`;
  return documentQualityLabel(document.indexing_state, document.readability);
}
