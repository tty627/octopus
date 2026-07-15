import type { IndexingState, Readability } from "./types";

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
