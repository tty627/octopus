import type { SearchResult } from "./types";

export type ResultGroup = "核心资料" | "相关文件夹" | "补充资料" | "需要核验";

export function groupSearchResults(results: SearchResult[]): Record<ResultGroup, SearchResult[]> {
  const grouped: Record<ResultGroup, SearchResult[]> = {
    核心资料: [],
    相关文件夹: [],
    补充资料: [],
    需要核验: [],
  };
  for (const result of results) {
    if (
      result.quality_flags.length > 0 ||
      result.risk_flags.length > 0 ||
      !["clean", "indexed"].includes(result.status)
    ) {
      grouped.需要核验.push(result);
    } else if (result.index_type === "foldernode") {
      grouped.相关文件夹.push(result);
    } else if (result.rank <= 3) {
      grouped.核心资料.push(result);
    } else {
      grouped.补充资料.push(result);
    }
  }
  return grouped;
}

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

export function humanStatus(status: string): string {
  const values: Record<string, string> = {
    clean: "可用",
    indexed: "已索引",
    pending: "等待处理",
    pending_edit: "文件仍在编辑",
    pending_stable: "等待文件稳定",
    failed: "处理失败",
    stale: "可能已过期",
    orphaned: "来源不可访问",
  };
  return values[status] ?? "需要留意";
}

export function relativeTime(value?: string): string {
  if (!value) return "尚未完成同步";
  const time = Date.parse(value);
  if (Number.isNaN(time)) return value;
  const minutes = Math.max(0, Math.round((Date.now() - time) / 60_000));
  if (minutes < 1) return "刚刚";
  if (minutes < 60) return `${minutes} 分钟前`;
  const hours = Math.round(minutes / 60);
  if (hours < 24) return `${hours} 小时前`;
  return new Date(time).toLocaleDateString("zh-CN");
}

export function safeFileName(value: string): string {
  return value.replace(/[<>:"/\\|?*]+/g, "-").trim() || "Octopus-任务包";
}
