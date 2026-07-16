import type { EvidenceLocator, SearchResultV2 } from "../types";

export function locatorLabel(locator: EvidenceLocator | null | undefined, pageNumber: number | null = null): string {
  if (!locator) return pageNumber ? `第 ${pageNumber} 页` : "";
  if (locator.label) return locator.label;
  if (locator.kind === "page" && locator.page_number) return `第 ${locator.page_number} 页`;
  if (locator.kind === "paragraph" && locator.paragraph_index !== null && locator.paragraph_index !== undefined) return `第 ${locator.paragraph_index + 1} 段`;
  if (locator.kind === "table" && locator.table_index !== null && locator.table_index !== undefined) return `表格 ${locator.table_index + 1}`;
  if (locator.kind === "sheet") return [locator.sheet_name, locator.cell_range].filter(Boolean).join(" · ");
  if (locator.kind === "slide" && locator.slide_number) return `第 ${locator.slide_number} 张幻灯片`;
  if (locator.kind === "image") return "图片 OCR";
  if (locator.kind === "text" && locator.line_start) return locator.line_end && locator.line_end !== locator.line_start ? `第 ${locator.line_start}-${locator.line_end} 行` : `第 ${locator.line_start} 行`;
  return pageNumber ? `第 ${pageNumber} 页` : "文档内容";
}

export function sourceKindLabel(result: Pick<SearchResultV2, "source_ref">): string {
  const kind = result.source_ref?.kind ?? result.source_ref?.source_kind ?? "physical";
  if (kind === "archive_member") return "ZIP 内文件";
  if (kind === "archive") return "ZIP 压缩包";
  return "普通文件";
}
