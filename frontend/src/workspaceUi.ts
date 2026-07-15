import type { ServiceJob, Workspace } from "./types";
import { relativeTime } from "./utils";

function pathSegments(path: string): string[] {
  return path.replace(/[\\/]+$/, "").split(/[\\/]+/).filter(Boolean);
}

function suffix(path: string, depth: number): string {
  return pathSegments(path).slice(-depth).join("\\");
}

function uniquePathSuffix(workspace: Workspace, group: Workspace[]): string {
  const segments = pathSegments(workspace.raw_path);
  for (let depth = 1; depth <= segments.length; depth += 1) {
    const candidate = suffix(workspace.raw_path, depth);
    const matches = group.filter(
      (item) => suffix(item.raw_path, depth).toLocaleLowerCase() === candidate.toLocaleLowerCase(),
    );
    if (matches.length === 1) return candidate;
  }
  const fallback = suffix(workspace.raw_path, Math.min(2, segments.length)) || "资料空间";
  return `${fallback} (${workspace.workspace_id.slice(-8)})`;
}

export function workspaceOptionLabels(workspaces: Workspace[]): Map<string, string> {
  const groups = new Map<string, Workspace[]>();
  for (const workspace of workspaces) {
    const key = workspace.name.trim().toLocaleLowerCase();
    groups.set(key, [...(groups.get(key) ?? []), workspace]);
  }

  return new Map(workspaces.map((workspace) => {
    const group = groups.get(workspace.name.trim().toLocaleLowerCase()) ?? [];
    const label = group.length > 1
      ? `${workspace.name} - ${uniquePathSuffix(workspace, group)}`
      : workspace.name;
    return [workspace.workspace_id, label];
  }));
}

export function workspaceSyncStatusText(workspace: Workspace): string {
  if (!workspace.available) return "原始资料不可访问";
  return workspace.health.last_sync_at
    ? `已同步 ${relativeTime(workspace.health.last_sync_at)}`
    : "尚未同步";
}

export function isActiveWorkspaceJob(job: ServiceJob): boolean {
  return job.kind === "workspace_sync" && (job.status === "queued" || job.status === "running");
}

export function latestWorkspaceJob(jobs: ServiceJob[]): ServiceJob | undefined {
  let latest: ServiceJob | undefined;
  let latestTime = Number.NEGATIVE_INFINITY;
  for (const job of jobs) {
    if (job.kind !== "workspace_sync") continue;
    const parsed = Date.parse(job.created_at ?? "");
    const time = Number.isNaN(parsed) ? Number.NEGATIVE_INFINITY : parsed;
    if (!latest || time > latestTime) {
      latest = job;
      latestTime = time;
    }
  }
  return latest;
}

export function workspaceJobProgressText(job: ServiceJob): string {
  if (job.status === "queued") return "后台任务已排队，可以继续使用其他功能。";
  const progress = job.result.progress;
  if (job.status === "running" && progress) {
    const processed = progress.processed ?? 0;
    const discovered = progress.discovered ?? 0;
    const count = discovered > 0 ? ` ${processed}/${discovered}` : "";
    const current = progress.current_file ? `：${progress.current_file}` : "";
    if (progress.phase === "discovering") return `正在扫描资料${discovered > 0 ? `，已发现 ${discovered} 个文件` : ""}${current}`;
    if (progress.phase === "finalizing") return "正在整理搜索索引，马上完成。";
    const currentPage = progress.current_page ?? 0;
    const pageCount = progress.page_count ?? 0;
    if (currentPage > 0 && pageCount > 0) {
      const page = `第 ${currentPage}/${pageCount} 页`;
      if (progress.extraction_stage === "ocr") return `正在 OCR ${page}${current}`;
      if (progress.extraction_stage === "pypdf") return `正在尝试备用文本提取 ${page}${current}`;
      if (progress.extraction_stage === "pdfium") return `正在读取 PDF 文本 ${page}${current}`;
      if (progress.extraction_stage === "page_complete") return `正在整理页面 ${page}${current}`;
      return `正在处理 ${page}${current}`;
    }
    const completedPages = progress.pages_completed ?? 0;
    if (completedPages > 0 && pageCount > 0) {
      const ocr = (progress.ocr_pages_completed ?? 0) > 0
        ? `，其中 OCR ${progress.ocr_pages_completed} 页`
        : "";
      return `已处理 ${completedPages}/${pageCount} 页${ocr}${current}`;
    }
    return `正在后台处理资料${count}${current}`;
  }
  return "正在后台处理资料，可以继续使用其他功能。";
}
