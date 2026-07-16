import type {
  EvidenceLocator,
  FreshnessStatus,
  IndexingState,
  Readability,
  SourceKind,
  SourceRef,
  WorkspaceEvidence,
  WorkspaceTaskSummary,
} from "./types";

export function formatBytes(value: number): string {
  if (value < 1024) return `${value} B`;
  const units = ["KiB", "MiB", "GiB", "TiB"];
  let amount = value / 1024;
  let unit = units[0];
  for (const candidate of units.slice(1)) {
    if (amount < 1024) break;
    amount /= 1024;
    unit = candidate;
  }
  return `${amount.toFixed(amount >= 10 ? 0 : 1)} ${unit}`;
}

export function relativeTime(value?: string): string {
  if (!value) return "尚未同步";
  const time = Date.parse(value);
  if (Number.isNaN(time)) return "时间未知";
  const minutes = Math.max(0, Math.round((Date.now() - time) / 60_000));
  if (minutes < 1) return "刚刚";
  if (minutes < 60) return `${minutes} 分钟前`;
  const hours = Math.round(minutes / 60);
  if (hours < 24) return `${hours} 小时前`;
  return new Date(time).toLocaleDateString("zh-CN");
}

export function taskSummaryIssueCount(summary: WorkspaceTaskSummary): number {
  const stale = summary.stale_count ?? summary.freshness_issue_count ?? 0;
  return Math.max(stale, summary.unresolved_count);
}

export function hasFreshnessIssue(value?: FreshnessStatus): boolean {
  return value === "changed" || value === "missing" || value === "stale" ||
    value === "unavailable" || value === "needs_review";
}

export function readabilityLabel(value: Readability): string {
  if (value === "readable") return "正文可读";
  if (value === "partial") return "部分可读";
  return "识别质量低";
}

export function documentQualityLabel(
  indexingState: IndexingState,
  readability: Readability,
): string {
  if (indexingState === "failed") return "处理失败";
  if (indexingState === "metadata_only") return "仅文件信息";
  return readabilityLabel(readability);
}

export function searchEvidenceText(
  indexingState: IndexingState,
  readability: Readability,
  excerpt: string,
): string {
  if (indexingState === "failed") return "文件处理失败，可按文件名查找。";
  if (indexingState === "metadata_only") return "当前仅提供文件名、路径和元数据检索。";
  if (readability === "low") return "正文识别质量较低，可按文件名查找。";
  return excerpt;
}

export function safeFileName(value: string): string {
  return value.replace(/[<>:"/\\|?*]+/g, "-").trim() || "Octopus-任务";
}

export function sourceKind(source?: SourceRef | null): SourceKind {
  return source?.kind ?? source?.source_kind ?? "physical";
}

export function sourceKindLabel(source?: SourceRef | null): string {
  const kind = sourceKind(source);
  if (kind === "archive_member") return "ZIP 成员";
  if (kind === "archive") return "ZIP 容器";
  return "本地文件";
}

export function sourceDisplayPath(
  source: SourceRef | null | undefined,
  relativePath: string,
): string {
  return source?.virtual_path || relativePath;
}

export function locatorLabel(
  locator?: EvidenceLocator | null,
  pageNumber?: number | null,
): string {
  if (locator?.label) return locator.label;
  const page = locator?.page_number ?? pageNumber;
  if (locator?.kind === "page" || page) return `第 ${page} 页`;
  if (locator?.kind === "paragraph" && locator.paragraph_index) return `第 ${locator.paragraph_index} 段`;
  if (locator?.kind === "table" && locator.table_index) return `表格 ${locator.table_index}`;
  if (locator?.kind === "sheet") {
    return [locator.sheet_name, locator.cell_range].filter(Boolean).join(" · ") || "工作表";
  }
  if (locator?.kind === "slide" && locator.slide_number) return `第 ${locator.slide_number} 张幻灯片`;
  if (locator?.kind === "image") return "图片内容";
  if (locator?.kind === "text" && locator.line_start) {
    return locator.line_end && locator.line_end !== locator.line_start
      ? `第 ${locator.line_start}-${locator.line_end} 行`
      : `第 ${locator.line_start} 行`;
  }
  return "文档内容";
}

export function evidenceLocator(evidence: WorkspaceEvidence): EvidenceLocator | null {
  return evidence.locator ?? (evidence.page_number
    ? { kind: "page", page_number: evidence.page_number }
    : null);
}

export function freshnessLabel(value?: FreshnessStatus): string {
  if (value === "stale" || value === "needs_review") return "待重新核验";
  if (value === "unavailable") return "来源不可用";
  return "来源最新";
}
