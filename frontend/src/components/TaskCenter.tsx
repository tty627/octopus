import { useEffect, useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  AlertTriangle,
  Check,
  ChevronDown,
  CircleStop,
  ListChecks,
  LoaderCircle,
  RotateCcw,
  X,
} from "lucide-react";
import { ApiError, api } from "../api";
import type { ServiceJob, WorkspaceJobProgress } from "../types";
import { relativeTime } from "../utils";
import { isActiveJob } from "../workspaceUi";

const SUCCESS_STATUSES = new Set<ServiceJob["status"]>(["succeeded"]);
const FAILURE_STATUSES = new Set<ServiceJob["status"]>(["failed", "canceled", "interrupted"]);

export function TaskCenter({ workspaceId }: { workspaceId: string }) {
  const queryClient = useQueryClient();
  const [open, setOpen] = useState(false);
  const [notice, setNotice] = useState<{ id: string; text: string; failed: boolean } | null>(null);
  const [actionError, setActionError] = useState("");
  const statuses = useRef(new Map<string, ServiceJob["status"]>());
  const jobs = useQuery({
    queryKey: ["jobs", workspaceId],
    queryFn: ({ signal }) => api.jobs(workspaceId, signal),
    enabled: Boolean(workspaceId),
    refetchInterval: (query) => query.state.data?.some(isActiveJob) ? 1_000 : 8_000,
  });
  const allJobs = jobs.data ?? [];
  const visibleJobs = allJobs.filter((job, index) => index < 12 || isActiveJob(job));
  const activeCount = allJobs.filter(isActiveJob).length;

  useEffect(() => {
    visibleJobs.forEach((job) => {
      const previous = statuses.current.get(job.job_id);
      statuses.current.set(job.job_id, job.status);
      if (!previous || (!isActiveStatus(previous))) return;
      if (SUCCESS_STATUSES.has(job.status)) {
        setNotice({ id: job.job_id, text: `${jobLabel(job.kind)}已完成。`, failed: false });
      } else if (FAILURE_STATUSES.has(job.status)) {
        setNotice({
          id: job.job_id,
          text: job.status === "canceled"
            ? `${jobLabel(job.kind)}已取消。`
            : `${jobLabel(job.kind)}未完成：${job.error_message || "请查看任务详情。"}`,
          failed: job.status !== "canceled",
        });
      }
    });
  }, [visibleJobs]);

  const remember = (job: ServiceJob) => {
    queryClient.setQueryData<ServiceJob[]>(["jobs", workspaceId], (current = []) => [
      job,
      ...current.filter((item) => item.job_id !== job.job_id),
    ]);
  };
  const cancel = async (job: ServiceJob) => {
    setActionError("");
    try {
      remember(await api.cancelJob(workspaceId, job.job_id));
    } catch (reason) {
      setActionError(reason instanceof ApiError ? reason.message : "任务暂时无法取消。");
    }
  };
  const retry = async (job: ServiceJob) => {
    setActionError("");
    try {
      const payload = job.result.progress?.retry_payload;
      let next: ServiceJob;
      if (job.kind === "workspace_sync") {
        next = await api.syncWorkspace(workspaceId);
      } else if (job.kind === "workspace_ai_index") {
        next = await api.startAIIndex(workspaceId, { retry_failed: true });
      } else if (job.kind === "workspace_research" && payload?.kind === "workspace_research") {
        next = await api.startResearch(workspaceId, payload.question, payload.filters);
      } else if (job.kind === "task_proposal" && payload?.kind === "task_proposal") {
        next = await api.startResearchProposal(
          workspaceId,
          payload.goal,
          payload.title,
          payload.template_id,
        );
      } else if (job.kind === "task_export" && payload?.kind === "task_export") {
        const task = await api.task(workspaceId, payload.task_id);
        next = await api.startTaskExport(task, {
          citation_style: payload.citation_style,
          include_sources: payload.include_sources,
        });
      } else {
        throw new ApiError("该任务缺少可重试参数，请回到原操作页面重试。", 409);
      }
      remember(next);
    } catch (reason) {
      setActionError(reason instanceof ApiError ? reason.message : "重试任务没有开始。");
    }
  };

  return (
    <div className="taskCenter">
      <button className={`taskCenterButton ${activeCount ? "taskCenterActive" : ""}`} onClick={() => setOpen((value) => {
        const next = !value;
        if (next) setNotice(null);
        return next;
      })} aria-expanded={open} aria-label="任务中心">
        {activeCount ? <LoaderCircle className="spin" size={16} /> : <ListChecks size={16} />}
        <span>{activeCount ? `${activeCount} 个任务进行中` : "任务中心"}</span>
        <ChevronDown size={14} />
      </button>
      {open && (
        <section className="taskCenterPanel" aria-label="后台任务">
          <header><div><strong>任务中心</strong><small>当前资料空间</small></div><button className="iconButton" onClick={() => setOpen(false)} aria-label="关闭任务中心"><X size={16} /></button></header>
          {actionError && <div className="taskActionError" role="alert">{actionError}</div>}
          <div className="taskCenterList">
            {visibleJobs.map((job) => <JobRow key={job.job_id} job={job} onCancel={cancel} onRetry={retry} />)}
            {!jobs.isLoading && visibleJobs.length === 0 && <div className="taskCenterEmpty">还没有后台任务</div>}
          </div>
        </section>
      )}
      {notice && (
        <div className={`globalNotice ${notice.failed ? "globalNoticeError" : ""}`} role={notice.failed ? "alert" : "status"}>
          {notice.failed ? <AlertTriangle size={17} /> : <Check size={17} />}
          <span>{notice.text}</span>
          <button className="iconButton" onClick={() => setNotice(null)} aria-label="关闭通知"><X size={15} /></button>
        </div>
      )}
    </div>
  );
}

function JobRow({
  job,
  onCancel,
  onRetry,
}: {
  job: ServiceJob;
  onCancel: (job: ServiceJob) => Promise<void>;
  onRetry: (job: ServiceJob) => Promise<void>;
}) {
  const progress = job.result.progress;
  const percent = progressPercent(progress);
  const canRetry = FAILURE_STATUSES.has(job.status) && (
    job.kind === "workspace_sync"
    || job.kind === "workspace_ai_index"
    || Boolean(job.result.progress?.retry_payload)
  );
  return (
    <article className="taskCenterRow">
      <div className="taskCenterRowTitle">
        <span>{jobStatusIcon(job)}</span>
        <div><strong>{jobLabel(job.kind)}</strong><small>{jobStatusLabel(job)} · {relativeTime(job.created_at)}</small></div>
        {isActiveJob(job) && <button className="iconButton" onClick={() => void onCancel(job)} aria-label={`取消${jobLabel(job.kind)}`} title="取消"><CircleStop size={16} /></button>}
        {canRetry && <button className="iconButton" onClick={() => void onRetry(job)} aria-label={`重试${jobLabel(job.kind)}`} title="重试"><RotateCcw size={16} /></button>}
      </div>
      {percent !== null && <div className="taskProgress"><span style={{ width: `${percent}%` }} /></div>}
      {progress?.current_file && <small className="taskCurrentFile" title={progress.current_file}>{progress.current_file}</small>}
      {job.error_message && FAILURE_STATUSES.has(job.status) && <p className="taskFailure">{job.error_message}</p>}
      {job.kind === "workspace_ai_index" && <UsageLine progress={progress} />}
    </article>
  );
}

function UsageLine({ progress }: { progress?: WorkspaceJobProgress }) {
  if (!progress) return null;
  return (
    <small className="taskUsage">
      <span>{progress.total_tokens ?? 0} token</span>
      <span>{progress.duration_ms ? `${(progress.duration_ms / 1000).toFixed(1)} 秒` : "耗时统计中"}</span>
      <span>{progress.cost_known && progress.estimated_cost !== null && progress.estimated_cost !== undefined ? `约 ${progress.estimated_cost.toFixed(4)}` : "费用未知"}</span>
    </small>
  );
}

function progressPercent(progress?: WorkspaceJobProgress): number | null {
  if (!progress) return null;
  const completed = progress.completed ?? progress.processed ?? progress.pages_completed;
  const total = progress.total ?? progress.discovered ?? progress.page_count;
  if (completed === undefined || !total) return null;
  return Math.max(0, Math.min(100, Math.round((completed / total) * 100)));
}

function isActiveStatus(status: ServiceJob["status"]): boolean {
  return status === "queued" || status === "running";
}

function jobLabel(kind: ServiceJob["kind"]): string {
  const labels: Partial<Record<ServiceJob["kind"], string>> = {
    workspace_sync: "资料同步",
    workspace_rebuild: "索引重建",
    workspace_ai_index: "AI 索引",
    workspace_research: "研究问题",
    task_proposal: "资料提案",
    task_export: "研究包导出",
  };
  return labels[kind] ?? "后台任务";
}

function jobStatusLabel(job: ServiceJob): string {
  if (job.status === "queued") return "等待中";
  if (job.status === "running") return phaseLabel(job.result.progress?.phase);
  if (job.status === "succeeded") return "已完成";
  if (job.status === "canceled") return "已取消";
  if (job.status === "interrupted") return "已中断";
  return "失败";
}

function phaseLabel(phase?: string): string {
  const labels: Record<string, string> = {
    discovering: "发现文件",
    processing: "逐文件处理",
    finalizing: "同步收尾",
    documents: "处理文档",
    document: "生成资料卡",
    folder: "生成文件夹卡",
    understanding: "理解问题",
    retrieving: "检索证据",
    composing: "组织回答",
    verifying: "核验来源",
    collecting_sources: "收集原件",
    packaging: "生成压缩包",
    completed: "完成",
  };
  return phase ? labels[phase] ?? "处理中" : "处理中";
}

function jobStatusIcon(job: ServiceJob) {
  if (isActiveJob(job)) return <LoaderCircle className="spin" size={16} />;
  if (job.status === "succeeded") return <Check size={16} />;
  return <AlertTriangle size={16} />;
}
