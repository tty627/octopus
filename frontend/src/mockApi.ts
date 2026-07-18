import type {
  AISettingsInputV2,
  AISettingsV2,
  AIProviderPreset,
  SearchReportV2,
  SearchResultV2,
  ServiceJob,
  Workspace,
  WorkspaceDocument,
  WorkspaceTask,
  WorkspaceTaskSummary,
  AIIndexStatus,
  ResearchPackExportRequest,
  ResearchTaskProposal,
  TaskTemplateId,
  WorkspaceChange,
} from "./types";

const now = new Date().toISOString();
const workspace: Workspace = {
  workspace_id: "demo-workspace",
  name: "高数",
  raw_path: "C:\\Users\\Demo\\Documents\\高数",
  available: true,
  enabled: true,
  vision_enabled: false,
  legacy_index_present: true,
  health: {
    document_count: 17,
    readable_count: 13,
    partial_count: 2,
    low_quality_count: 1,
    metadata_only_count: 1,
    failed_count: 0,
    last_sync_at: now,
  },
};
const workspaces: Workspace[] = [workspace];

function workspaceIdFromPath(path: string): string {
  const match = path.match(/^\/v2\/workspaces\/([^/?]+)/);
  return match ? decodeURIComponent(match[1] ?? "") : "";
}

function workspaceData(workspaceId: string): Workspace {
  const value = workspaces.find((item) => item.workspace_id === workspaceId);
  if (value) return value;
  const error = new Error("资料空间不存在");
  Object.assign(error, { status: 404 });
  throw error;
}

function documentsFor(workspaceId: string): WorkspaceDocument[] {
  return workspaceId === workspace.workspace_id ? documents : [];
}

const evidence = (
  page: number | null,
  reason: string,
  excerpt: string,
  quality = 0.93,
) => ({ page_number: page, heading: "", excerpt, reason, quality_score: quality });

const result = (
  rank: number,
  name: string,
  relativePath: string,
  excerpt: string,
  page: number | null,
  overrides: Partial<SearchResultV2> = {},
): SearchResultV2 => ({
  document_id: `document-${rank}`,
  name,
  relative_path: relativePath,
  extension: name.slice(name.lastIndexOf(".")).toLowerCase(),
  content_hash: `demo-hash-${rank}`,
  size_bytes: 420_000 * rank,
  modified_at: new Date(Date.now() - rank * 86_400_000).toISOString(),
  page_count: name.endsWith(".pdf") ? 36 : 0,
  readability: "readable",
  readability_score: 0.93,
  indexing_state: "indexed",
  source_uri: `file:///C:/Users/Demo/Documents/${encodeURIComponent(relativePath)}`,
  source_ref: {
    kind: "physical",
    workspace_path: relativePath,
    virtual_path: relativePath,
    stable_id: `source-${rank}`,
  },
  parser_key: name.endsWith(".pdf") ? "pdf" : "text",
  parser_version: "2.1",
  freshness_status: "current",
  overview: excerpt,
  best_evidence: evidence(page, page ? "正文包含查询内容" : "文件名包含查询内容", excerpt),
  additional_evidence: [],
  rank,
  ...overrides,
});

const allResults: SearchResultV2[] = [
  result(
    1,
    "微分方程coursenotes.pdf",
    "第六章/微分方程coursenotes.pdf",
    "第六章：微分方程。常微分方程的基本概念、一阶微分方程与高阶线性微分方程。",
    4,
  ),
  result(
    2,
    "09 级数.pdf",
    "第九章/09 级数.pdf",
    "级数（series）包括数项级数、正项级数、幂级数及收敛判别。",
    2,
  ),
  result(
    3,
    "常微分方程复习提纲.txt",
    "复习/常微分方程复习提纲.txt",
    "整理变量分离、齐次方程和一阶线性方程的解题步骤。",
    null,
  ),
  result(
    4,
    "扫描习题.pdf",
    "习题/扫描习题.pdf",
    "正文识别质量较低，可按文件名查找。",
    8,
    {
      readability: "low",
      readability_score: 0.31,
      best_evidence: evidence(8, "文件名包含查询内容", "正文识别质量较低，可按文件名查找。", 0.31),
    },
  ),
  result(5, "研究方法综述.docx", "文献/研究方法综述.docx", "本研究采用混合研究方法，并从样本、变量和数据来源三个方面说明设计。", null, {
    parser_key: "docx",
    best_evidence: { ...evidence(null, "段落包含查询内容", "本研究采用混合研究方法，并从样本、变量和数据来源三个方面说明设计。"), locator: { kind: "paragraph", paragraph_index: 6 } },
  }),
  result(6, "实验数据.xlsx", "数据/实验数据.xlsx", "实验组与对照组的均值、标准差和样本数量。", null, {
    parser_key: "xlsx",
    best_evidence: { ...evidence(null, "工作表包含查询内容", "实验组与对照组的均值、标准差和样本数量。"), locator: { kind: "sheet", sheet_name: "结果", cell_range: "A1:F18" } },
  }),
  result(7, "课堂汇报.pptx", "汇报/课堂汇报.pptx", "研究结论、局限与后续工作。", null, {
    parser_key: "pptx",
    best_evidence: { ...evidence(null, "幻灯片包含查询内容", "研究结论、局限与后续工作。"), locator: { kind: "slide", slide_number: 12 } },
  }),
  result(8, "访谈编码表.png", "图像/访谈编码表.png", "访谈主题包括学习投入、反馈频率和协作体验。", null, {
    parser_key: "image_ocr",
    best_evidence: { ...evidence(null, "图片 OCR 包含查询内容", "访谈主题包括学习投入、反馈频率和协作体验。"), locator: { kind: "image", label: "整张图片" } },
  }),
  result(9, "归档论文.pdf", "课程材料.zip!/论文/归档论文.pdf", "归档论文讨论了证据可追溯性和研究资料复用。", 7, {
    source_uri: "octopus://archive-member/document-9",
    source_ref: {
      kind: "archive_member",
      workspace_path: "课程材料.zip",
      virtual_path: "课程材料.zip!/论文/归档论文.pdf",
      container_path: "课程材料.zip",
      member_path: "论文/归档论文.pdf",
      member_chain: ["论文/归档论文.pdf"],
      member_indexes: [4],
      archive_depth: 1,
      stable_id: "archive-member-9",
    },
    parser_key: "pdf",
    best_evidence: { ...evidence(7, "ZIP 内 PDF 正文包含查询内容", "归档论文讨论了证据可追溯性和研究资料复用。"), locator: { kind: "page", page_number: 7 } },
  }),
  result(10, "课程材料.zip", "课程材料.zip", "压缩包包含课程论文、数据表和课堂汇报。", null, {
    indexing_state: "metadata_only",
    readability: "low",
    readability_score: 0,
    source_uri: "file:///C:/Users/Demo/Documents/课程材料.zip",
    source_ref: {
      kind: "archive",
      workspace_path: "课程材料.zip",
      virtual_path: "课程材料.zip",
      stable_id: "archive-10",
    },
    best_evidence: evidence(null, "压缩包名称包含查询内容", "课程材料.zip"),
  }),
];

const documents: WorkspaceDocument[] = allResults.map((item) => ({
  document_id: item.document_id,
  name: item.name,
  relative_path: item.relative_path,
  extension: item.extension,
  content_hash: item.content_hash,
  size_bytes: item.size_bytes,
  modified_at: item.modified_at,
  title: item.name.replace(/\.[^.]+$/, ""),
  overview: item.overview,
  page_count: item.page_count,
  readability: item.readability,
  readability_score: item.readability_score,
  indexing_state: item.indexing_state,
  error: "",
  source_uri: item.source_uri,
  source_ref: item.source_ref,
  locator: item.locator,
  quality_flags: item.quality_flags,
  parser_key: item.parser_key,
  parser_version: item.parser_version,
  freshness_status: item.freshness_status,
}));

let aiSettings: AISettingsV2 = {
  workspace_id: workspace.workspace_id,
  enabled: false,
  provider: "deepseek",
  preset: "deepseek",
  base_url: "https://api.deepseek.com",
  model: "deepseek-v4-flash",
  credential_configured: false,
  credential_source: "none",
  credential_error: "",
  vision_enabled: false,
  capabilities: {
    text: true,
    structured_output: true,
    vision: false,
    file_upload: false,
  },
};

function emptyAISettings(workspaceId: string): AISettingsV2 {
  return {
    workspace_id: workspaceId,
    enabled: false,
    provider: "deepseek",
    preset: "deepseek",
    base_url: "https://api.deepseek.com",
    model: "deepseek-v4-flash",
    credential_configured: false,
    credential_source: "none",
    credential_error: "",
    vision_enabled: false,
    capabilities: {
      text: true,
      structured_output: true,
      vision: false,
      file_upload: false,
    },
  };
}

const aiSettingsByWorkspace = new Map<string, AISettingsV2>();

function settingsFor(workspaceId: string): AISettingsV2 {
  return workspaceId === workspace.workspace_id
    ? aiSettings
    : aiSettingsByWorkspace.get(workspaceId) ?? emptyAISettings(workspaceId);
}

function setSettingsFor(workspaceId: string, value: AISettingsV2): void {
  if (workspaceId === workspace.workspace_id) aiSettings = value;
  else aiSettingsByWorkspace.set(workspaceId, value);
}

const providerPresets: AIProviderPreset[] = [
  {
    preset: "deepseek",
    label: "DeepSeek",
    provider: "deepseek",
    base_url: "https://api.deepseek.com",
    capability_hints: {
      text: true,
      structured_output: true,
      vision: false,
      file_upload: false,
    },
  },
  {
    preset: "glm",
    label: "智谱 GLM",
    provider: "openai_compatible",
    base_url: "https://open.bigmodel.cn/api/paas/v4",
    capability_hints: {
      text: true,
      structured_output: "连接测试确认",
      vision: "取决于所选模型",
      file_upload: false,
    },
  },
  {
    preset: "custom",
    label: "OpenAI Compatible",
    provider: "openai_compatible",
    base_url: "",
    capability_hints: {
      text: "连接测试确认",
      structured_output: "连接测试确认",
      vision: "连接测试确认",
      file_upload: false,
    },
  },
];

let tasks: WorkspaceTask[] = [];
const serviceJobs: ServiceJob[] = [];
const exportArtifacts = new Map<string, Blob>();

let aiIndex: AIIndexStatus = {
  workspace_id: workspace.workspace_id,
  document_count: documents.length,
  indexed_document_count: 0,
  pending_document_count: documents.length,
  failed_document_count: 0,
  folder_count: 2,
  indexed_folder_count: 0,
  pending_folder_count: 2,
  failed_folder_count: 0,
  estimated_calls: documents.length + 2,
  last_run_at: "",
  last_error: "",
};

const aiIndexesByWorkspace = new Map<string, AIIndexStatus>();

function aiIndexFor(workspaceId: string): AIIndexStatus {
  if (workspaceId === workspace.workspace_id) return aiIndex;
  return aiIndexesByWorkspace.get(workspaceId) ?? {
    workspace_id: workspaceId,
    document_count: 0,
    indexed_document_count: 0,
    pending_document_count: 0,
    failed_document_count: 0,
    folder_count: 0,
    indexed_folder_count: 0,
    pending_folder_count: 0,
    failed_folder_count: 0,
    estimated_calls: 0,
    last_run_at: "",
    last_error: "",
  };
}

function setAIIndexFor(workspaceId: string, value: AIIndexStatus): void {
  if (workspaceId === workspace.workspace_id) aiIndex = value;
  else aiIndexesByWorkspace.set(workspaceId, value);
}

function createTask(
  title: string,
  goal: string,
  templateId: TaskTemplateId = "free_research",
  workspaceId = workspace.workspace_id,
): WorkspaceTask {
  const slotNames = templateId === "literature_review"
    ? ["研究背景", "核心文献", "方法与数据", "主要结论", "相反证据", "研究缺口"]
    : templateId === "course_report"
      ? ["核心论点", "课程材料", "分析证据", "参考资料"]
      : ["核心证据", "补充证据", "待核验"];
  const task: WorkspaceTask = {
    schema_version: "2.1",
    task_id: crypto.randomUUID(),
    workspace_id: workspaceId,
    revision: 1,
    lifecycle: "draft",
    title,
    goal,
    slots: slotNames.map((name, position) => ({ slot_id: crypto.randomUUID(), name, description: "", position, required: position === 0 })),
    items: [],
    created_at: now,
    updated_at: now,
    migrated_from_v1: false,
    template_id: templateId,
    citation_style: "gb-t-7714-2015",
  };
  tasks = [task, ...tasks];
  return task;
}

function summaries(workspaceId = workspace.workspace_id): WorkspaceTaskSummary[] {
  return tasks.filter((task) =>
    task.workspace_id === workspaceId && task.lifecycle !== "archived"
  ).map((task) => ({
    schema_version: task.schema_version,
    task_id: task.task_id,
    workspace_id: task.workspace_id,
    revision: task.revision,
    lifecycle: task.lifecycle,
    title: task.title,
    goal: task.goal,
    item_count: task.items.length,
    pending_count: task.items.filter((item) => item.review_state === "pending").length,
    unresolved_count: task.items.filter((item) => item.source_status === "source_unconfirmed").length,
    updated_at: task.updated_at,
    writable: true,
    template_id: task.template_id,
    stale_count: task.items.filter(
      (item) => item.freshness_status === "changed" || item.freshness_status === "missing",
    ).length,
  }));
}

const workspaceChanges: WorkspaceChange[] = [{
  change_id: "change-1",
  workspace_id: workspace.workspace_id,
  kind: "modified",
  document_id: "document-9",
  name: "课程材料.zip",
  relative_path: "课程材料.zip",
  occurred_at: now,
  message: "压缩包内容已变化，相关资料包需要复核。",
  affected_task_ids: [],
  acknowledged: false,
}];

function delay(ms: number, signal?: AbortSignal): Promise<void> {
  return new Promise((resolve, reject) => {
    const timer = window.setTimeout(resolve, ms);
    signal?.addEventListener("abort", () => {
      window.clearTimeout(timer);
      reject(new DOMException("Aborted", "AbortError"));
    }, { once: true });
  });
}

function searchReport(
  query: string,
  mode: "local" | "assisted",
  workspaceId = workspace.workspace_id,
): SearchReportV2 {
  const availableResults = workspaceId === workspace.workspace_id ? allResults : [];
  const filtered = query.includes("级数")
    ? availableResults.filter((item) => item.name.includes("级数"))
    : query.includes("微分方程")
      ? availableResults.filter((item) => item.name.includes("微分方程"))
      : availableResults;
  const settings = settingsFor(workspaceId);
  const assisted = mode === "assisted" && settings.enabled && settings.credential_configured;
  return {
    query,
    requested_mode: mode,
    actual_mode: assisted ? "assisted" : mode === "assisted" ? "degraded" : "local",
    degradation_reason: mode === "assisted" && !assisted ? "assisted_search_not_configured" : "",
    answer: assisted ? `已在 ${filtered.length} 份本地候选中完成辅助整理。` : `找到 ${filtered.length} 份相关资料。`,
    results: filtered.map((item, index) => ({ ...item, rank: index + 1 })),
    candidate_count: filtered.length,
    duration_ms: assisted ? 420 : 48,
  };
}

function job(
  workspaceId = workspace.workspace_id,
  documentCount = documentsFor(workspaceId).length,
): ServiceJob {
  const createdAt = new Date().toISOString();
  const value: ServiceJob = {
    job_id: crypto.randomUUID(),
    repository_id: workspaceId,
    kind: "workspace_sync",
    status: "queued",
    created_at: createdAt,
    started_at: "",
    finished_at: "",
    result: {
      progress: {
        phase: "discovering",
        discovered: documentCount,
        processed: 0,
        indexed: 0,
        unchanged: 0,
        failed: 0,
        removed: 0,
      },
    },
    error_code: "",
    error_message: "",
  };
  serviceJobs.unshift(value);
  window.setTimeout(() => {
    const index = serviceJobs.findIndex((item) => item.job_id === value.job_id);
    if (index < 0) return;
    const finishedAt = new Date().toISOString();
    const targetWorkspace = workspaces.find((item) => item.workspace_id === workspaceId);
    if (targetWorkspace) targetWorkspace.health.last_sync_at = finishedAt;
    serviceJobs[index] = {
      ...value,
      status: "succeeded",
      started_at: createdAt,
      finished_at: finishedAt,
      result: {
        progress: {
          phase: "completed",
          discovered: documentCount,
          processed: documentCount,
          indexed: documentCount,
          unchanged: 0,
          failed: 0,
          removed: 0,
        },
      },
    };
  }, 80);
  return value;
}

function backgroundJob(
  kind: ServiceJob["kind"],
  result: ServiceJob["result"],
  progress: ServiceJob["result"]["progress"],
  workspaceId = workspace.workspace_id,
): ServiceJob {
  const createdAt = new Date().toISOString();
  const value: ServiceJob = {
    job_id: crypto.randomUUID(),
    repository_id: workspaceId,
    kind,
    status: "queued",
    created_at: createdAt,
    started_at: "",
    finished_at: "",
    result: { progress },
    error_code: "",
    error_message: "",
  };
  serviceJobs.unshift(value);
  window.setTimeout(() => {
    const index = serviceJobs.findIndex((item) => item.job_id === value.job_id);
    if (index < 0 || serviceJobs[index]?.status === "canceled") return;
    serviceJobs[index] = {
      ...value,
      status: "succeeded",
      started_at: createdAt,
      finished_at: new Date().toISOString(),
      result,
    };
  }, 80);
  return value;
}

export function mockPreviewUrl(documentId: string, page: number): string {
  const document = documents.find((item) => item.document_id === documentId);
  const title = document?.name ?? "页面证据";
  const svg = `<svg xmlns="http://www.w3.org/2000/svg" width="760" height="1020"><rect width="100%" height="100%" fill="#fff"/><text x="70" y="90" font-family="sans-serif" font-size="25" fill="#17201e">${title}</text><text x="70" y="140" font-family="sans-serif" font-size="18" fill="#64706c">第 ${page} 页</text><line x1="70" y1="175" x2="690" y2="175" stroke="#ccd5d1"/><text x="70" y="235" font-family="sans-serif" font-size="20" fill="#283431">级数与微分方程的页面证据预览</text><rect x="65" y="270" width="630" height="54" fill="#fff1a8" opacity=".75"/><text x="78" y="305" font-family="sans-serif" font-size="18" fill="#283431">命中内容位于当前页面，原文件保持只读。</text></svg>`;
  return `data:image/svg+xml;charset=utf-8,${encodeURIComponent(svg)}`;
}

export function mockContentUrl(documentId: string): string {
  const document = documents.find((item) => item.document_id === documentId);
  const title = document?.name ?? "内容预览";
  const svg = `<svg xmlns="http://www.w3.org/2000/svg" width="1024" height="720"><rect width="100%" height="100%" fill="#f7f9f8"/><rect x="72" y="64" width="880" height="592" rx="4" fill="#fff" stroke="#ccd5d1"/><text x="112" y="128" font-family="sans-serif" font-size="28" fill="#17201e">${title}</text><text x="112" y="190" font-family="sans-serif" font-size="20" fill="#46524e">本地图片内容预览</text><rect x="108" y="228" width="720" height="58" fill="#fff1a8"/><text x="124" y="266" font-family="sans-serif" font-size="18" fill="#283431">OCR 命中：访谈主题、学习投入与反馈频率</text></svg>`;
  return `data:image/svg+xml;charset=utf-8,${encodeURIComponent(svg)}`;
}

export function mockResearchPackBlob(task: WorkspaceTask, options: ResearchPackExportRequest): Blob {
  return new Blob([JSON.stringify({
    mock_archive: true,
    files: ["research.md", "references.bib", "task.json", "manifest.json"],
    task,
    options,
  }, null, 2)], { type: "application/zip" });
}

export function mockExportArtifactBlob(artifactId: string): Blob {
  const artifact = exportArtifacts.get(artifactId);
  if (!artifact) throw new Error("导出工件不存在或已过期");
  return artifact;
}

export async function mockRequest<T>(
  path: string,
  method: string,
  body?: unknown,
  signal?: AbortSignal,
): Promise<T> {
  await delay(path.endsWith("/search") ? 100 : 35, signal);
  const routeWorkspaceId = workspaceIdFromPath(path);
  if (routeWorkspaceId) workspaceData(routeWorkspaceId);
  if (path === "/v2/workspaces" && method === "GET") {
    return structuredClone(workspaces) as T;
  }
  if (path === "/v2/workspaces" && method === "POST") {
    const request = body as { raw_path: string; name: string };
    const created: Workspace = {
      workspace_id: `demo-${crypto.randomUUID()}`,
      name: request.name.trim(),
      raw_path: request.raw_path,
      available: true,
      enabled: true,
      vision_enabled: false,
      legacy_index_present: false,
      health: {
        document_count: 0,
        readable_count: 0,
        partial_count: 0,
        low_quality_count: 0,
        metadata_only_count: 0,
        failed_count: 0,
        queued_count: 0,
        processing_count: 0,
        last_sync_at: "",
      },
    };
    workspaces.unshift(created);
    return { workspace: structuredClone(created), job: job(created.workspace_id, 0) } as T;
  }
  if (routeWorkspaceId && path === `/v2/workspaces/${routeWorkspaceId}` && method === "GET") {
    return structuredClone(workspaceData(routeWorkspaceId)) as T;
  }
  if (path.endsWith("/sync") && method === "POST") {
    return job(routeWorkspaceId) as T;
  }
  if (path.endsWith("/documents") && method === "GET") {
    return structuredClone(documentsFor(routeWorkspaceId)) as T;
  }
  if (path.endsWith("/members") && method === "GET") {
    return structuredClone(documentsFor(routeWorkspaceId).filter(
      (item) => item.source_ref?.kind === "archive_member",
    )) as T;
  }
  if (path.endsWith("/open-target") && method === "POST") {
    const documentId = path.split("/").at(-2) ?? "";
    const document = documentsFor(routeWorkspaceId).find((item) => item.document_id === documentId);
    return {
      uri: document?.source_uri ?? `file:///C:/Users/Demo/Documents/${documentId}`,
      temporary: document?.source_ref?.kind === "archive_member",
      expires_at: new Date(Date.now() + 86_400_000).toISOString(),
      display_name: document?.name ?? documentId,
      source_ref: document?.source_ref ?? null,
    } as T;
  }
  if (path.endsWith("/reprocess") && method === "POST") return job(routeWorkspaceId) as T;
  if (path.endsWith("/ai-index") && method === "GET") {
    return structuredClone(aiIndexFor(routeWorkspaceId)) as T;
  }
  if (path.endsWith("/ai-index") && method === "POST") {
    const value = job(routeWorkspaceId);
    const currentIndex = aiIndexFor(routeWorkspaceId);
    setAIIndexFor(routeWorkspaceId, { ...currentIndex, indexed_document_count: currentIndex.document_count, pending_document_count: 0, indexed_folder_count: currentIndex.folder_count, pending_folder_count: 0, estimated_calls: 0, last_run_at: new Date().toISOString() });
    value.kind = "workspace_ai_index";
    return value as T;
  }
  if (path.endsWith("/search") && method === "POST") {
    const request = body as { query: string; mode: "local" | "assisted" };
    return searchReport(request.query, request.mode, routeWorkspaceId) as T;
  }
  if (path === "/v2/ai-provider-presets" && method === "GET") {
    return structuredClone(providerPresets) as T;
  }
  if (path.endsWith("/research") && method === "POST") {
    const request = body as { question: string };
    const currentSettings = settingsFor(routeWorkspaceId);
    const results = searchReport(request.question, "local", routeWorkspaceId).results;
    const citations = results.map((item, index) => ({
      citation_id: `R${index + 1}`,
      document_id: item.document_id,
      name: item.name,
      relative_path: item.relative_path,
      page_number: item.best_evidence.page_number,
      locator: item.best_evidence.locator ?? null,
      excerpt: item.best_evidence.excerpt,
    }));
    return backgroundJob(
      "workspace_research",
      {
        query: request.question,
        requested_mode: "research",
        actual_mode: currentSettings.enabled ? "research" : "degraded",
        degradation_reason: currentSettings.enabled ? "" : "assisted_search_not_configured",
        answer: results.length
          ? `根据当前资料，相关结论可由 [R1] 支持。`
          : "当前资料空间没有找到足以回答该问题的证据。",
        results,
        candidate_count: results.length,
        duration_ms: 120,
        subqueries: [request.question],
        citations,
        warnings: currentSettings.enabled ? [] : ["辅助模型不可用，本次保留本地检索结果。"],
        usage: { calls: 0, input_tokens: 0, output_tokens: 0, total_tokens: 0 },
        cost_known: false,
        progress: {
          phase: "completed",
          completed: 1,
          total: 1,
          evidence_count: results.length,
          cost_known: false,
        },
      },
      { phase: "understanding", completed: 0, total: 3 },
      routeWorkspaceId,
    ) as T;
  }
  if (path.endsWith("/task-proposals/jobs") && method === "POST") {
    const request = body as { goal: string; title?: string };
    const candidates = (routeWorkspaceId === workspace.workspace_id ? allResults : []).slice(0, 3).map((item) => ({
      candidate_id: `${item.document_id}-0`,
      document_id: item.document_id,
      content_hash: item.content_hash,
      name: item.name,
      relative_path: item.relative_path,
      page_number: item.best_evidence.page_number,
      locator: item.best_evidence.locator ?? null,
      excerpt: item.best_evidence.excerpt,
      reason: item.best_evidence.reason,
      quality_score: item.best_evidence.quality_score,
      source_ref: item.source_ref ?? null,
      overview: item.overview,
    }));
    const proposal: ResearchTaskProposal = {
      title: request.title || "研究资料包",
      goal: request.goal,
      summary: `围绕“${request.goal}”整理了 ${candidates.length} 条候选证据。`,
      warnings: [],
      gaps: [],
      candidates,
      slots: [{
        name: "核心证据",
        description: "直接支持目标的证据。",
        required: true,
        candidate_ids: candidates.map((item) => item.candidate_id),
        rationales: {},
      }],
    };
    return backgroundJob(
      "task_proposal",
      { proposal, progress: { phase: "completed", completed: 3, total: 3 } },
      { phase: "understanding", completed: 0, total: 3 },
      routeWorkspaceId,
    ) as T;
  }
  if (path.endsWith("/task-proposals") && method === "POST") {
    const request = body as { goal: string; title?: string };
    const candidates = (routeWorkspaceId === workspace.workspace_id ? allResults : []).slice(0, 3).flatMap((item) => [{
      candidate_id: `${item.document_id}-0`,
      document_id: item.document_id,
      content_hash: item.content_hash,
      name: item.name,
      relative_path: item.relative_path,
      page_number: item.best_evidence.page_number,
      locator: null,
      excerpt: item.best_evidence.excerpt,
      reason: item.best_evidence.reason,
      quality_score: item.best_evidence.quality_score,
      source_ref: null,
      overview: item.overview,
    }]);
    return {
      title: request.title || "研究资料包",
      goal: request.goal,
      summary: `围绕“${request.goal}”整理了 ${candidates.length} 条候选证据。`,
      warnings: [],
      gaps: [],
      candidates,
      slots: [{ name: "核心证据", description: "直接支持目标的证据。", required: true, candidate_ids: candidates.map((item) => item.candidate_id), rationales: {} }],
    } as ResearchTaskProposal as T;
  }
  if (path.endsWith("/task-proposals/confirm") && method === "POST") {
    const proposal = (body as { proposal: ResearchTaskProposal }).proposal;
    return createTask(proposal.title, proposal.goal, "free_research", routeWorkspaceId) as T;
  }
  if (path.endsWith("/ai-settings/test") && method === "POST") {
    const request = body as AISettingsInputV2;
    const currentSettings = settingsFor(routeWorkspaceId);
    const configured = Boolean(request.api_key || currentSettings.credential_configured);
    const capabilities = {
      ...(currentSettings.capabilities ?? { text: true, structured_output: true, vision: false, file_upload: false }),
      vision: request.preset === "glm" && request.model.toLocaleLowerCase().endsWith("v"),
    };
    return {
      ok: configured,
      code: configured ? "connected" : "key_not_configured",
      message: configured ? `已连接 ${request.model}。` : "请先填写 API Key。",
      capabilities: configured ? capabilities : undefined,
    } as T;
  }
  if (path.endsWith("/ai-settings") && method === "GET") {
    return structuredClone(settingsFor(routeWorkspaceId)) as T;
  }
  if (path.endsWith("/ai-settings") && method === "PUT") {
    const request = body as AISettingsInputV2;
    const currentSettings = settingsFor(routeWorkspaceId);
    const configured = request.clear_api_key ? false : Boolean(request.api_key || currentSettings.credential_configured);
    if (request.enabled && !configured) {
      const error = new Error("启用 AI 前需要 API Key");
      Object.assign(error, { status: 422 });
      throw error;
    }
    const updated = { ...currentSettings, ...request, workspace_id: routeWorkspaceId, credential_configured: configured, credential_source: configured ? "windows_credential" as const : "none" as const };
    setSettingsFor(routeWorkspaceId, updated);
    return structuredClone(updated) as T;
  }
  if (path.endsWith("/vision-authorization") && method === "PUT") {
    const value = (body as { vision_enabled: boolean }).vision_enabled;
    const currentWorkspace = workspaceData(routeWorkspaceId);
    currentWorkspace.vision_enabled = value;
    const updated = { ...settingsFor(routeWorkspaceId), vision_enabled: value };
    setSettingsFor(routeWorkspaceId, updated);
    return { workspace_id: routeWorkspaceId, vision_enabled: value } as T;
  }
  if (/\/documents\/[^/]+\/vision\/preflight$/.test(path) && method === "POST") {
    const request = body as { page_number: number };
    const currentSettings = settingsFor(routeWorkspaceId);
    const canSend = Boolean(currentSettings.enabled && currentSettings.vision_enabled && currentSettings.capabilities?.vision);
    return {
      workspace_id: routeWorkspaceId,
      document_id: path.split("/").at(-3) ?? "",
      page_number: request.page_number,
      model: currentSettings.model,
      mode: canSend ? "vision" : "ocr_fallback",
      image_size_bytes: 184_320,
      width: 1200,
      height: 1600,
      max_edge: 1600,
      pricing_configured: false,
      cost_estimate_status: "unknown",
      requires_confirmation: canSend,
      warning: canSend ? "" : "当前模型未通过视觉能力测试，本次只使用本地 OCR 文本。",
    } as T;
  }
  if (/\/documents\/[^/]+\/vision\/analyze$/.test(path) && method === "POST") {
    const request = body as { page_number: number; confirm_image_send: boolean };
    const currentSettings = settingsFor(routeWorkspaceId);
    const canSend = Boolean(currentSettings.enabled && currentSettings.vision_enabled && currentSettings.capabilities?.vision);
    return {
      workspace_id: routeWorkspaceId,
      document_id: path.split("/").at(-3) ?? "",
      page_number: request.page_number,
      model: currentSettings.model,
      mode: canSend && request.confirm_image_send ? "vision" : "ocr_fallback",
      image_size_bytes: 184_320,
      width: 1200,
      height: 1600,
      max_edge: 1600,
      pricing_configured: false,
      cost_estimate_status: "unknown",
      requires_confirmation: canSend,
      warning: canSend ? "" : "已使用当前页 OCR 文本。",
      answer: canSend ? "页面包含一张研究流程图，并标出了证据核验节点。" : "当前页 OCR 文本：研究流程与证据核验。",
      usage: { calls: canSend ? 1 : 0, input_tokens: 0, output_tokens: 0, total_tokens: 0, duration_ms: 80, estimated_cost: null },
      cost_known: false,
    } as T;
  }
  if (path.endsWith("/tasks") && method === "GET") return summaries(routeWorkspaceId) as T;
  if (path.endsWith("/tasks") && method === "POST") {
    const request = body as { title: string; goal: string; template_id?: TaskTemplateId };
    return createTask(request.title, request.goal, request.template_id, routeWorkspaceId) as T;
  }
  const exportMatch = path.match(/\/tasks\/([^/]+)\/exports$/);
  if (exportMatch && method === "POST") {
    const task = tasks.find((item) =>
      item.task_id === exportMatch[1] && item.workspace_id === routeWorkspaceId
    );
    if (!task) throw new Error("任务不存在");
    const exportOptions = body as ResearchPackExportRequest;
    const includeSources = exportOptions.include_sources;
    const included = includeSources
      ? task.items.filter((item) => item.review_state === "confirmed" && item.freshness_status === "current").length
      : 0;
    const artifactId = crypto.randomUUID().replaceAll("-", "");
    exportArtifacts.set(artifactId, mockResearchPackBlob(task, exportOptions));
    return backgroundJob(
      "task_export",
      {
        artifact_id: artifactId,
        workspace_id: routeWorkspaceId,
        file_name: `${task.title}.zip`,
        size_bytes: 2048,
        sha256: "mock-sha256",
        created_at: new Date().toISOString(),
        expires_at: new Date(Date.now() + 86_400_000).toISOString(),
        included_source_count: included,
        skipped_source_count: task.items.length - included,
        warnings: task.items.some((item) => item.review_state !== "confirmed")
          ? ["部分来源尚未人工核验，原件未复制。"]
          : [],
        progress: { phase: "completed", completed: task.items.length, total: task.items.length },
      },
      { phase: "verifying", completed: 0, total: task.items.length },
      routeWorkspaceId,
    ) as T;
  }
  if (path.endsWith("/changes") && method === "GET") {
    return structuredClone(
      routeWorkspaceId === workspace.workspace_id ? workspaceChanges : [],
    ) as T;
  }
  const taskMatch = path.match(/\/tasks\/([^/]+)(?:\/(markdown|archive|revalidate))?$/);
  if (taskMatch) {
    const taskId = taskMatch[1] ?? "";
    const action = taskMatch[2];
    const index = tasks.findIndex((item) =>
      item.task_id === taskId && item.workspace_id === routeWorkspaceId
    );
    const task = tasks[index];
    if (!task) throw new Error("任务不存在");
    if (action === "markdown") {
      return `# ${task.title}\n\n${task.goal}\n\n${task.items.map((item) => `- **${item.name}**${item.page_number ? ` · 第 ${item.page_number} 页` : ""}\n  - ${item.excerpt}`).join("\n")}` as T;
    }
    if (action === "archive") {
      const archived = { ...task, lifecycle: "archived" as const, revision: task.revision + 1 };
      tasks[index] = archived;
      return archived as T;
    }
    if (action === "revalidate") {
      const refreshed = {
        ...task,
        revision: task.revision + 1,
        updated_at: new Date().toISOString(),
        items: task.items.map((item) => ({
          ...item,
          source_status: "resolved" as const,
          freshness_status: "current" as const,
          confirmed_content_hash: item.content_hash,
        })),
      };
      tasks[index] = refreshed;
      return refreshed as T;
    }
    if (method === "PUT") {
      const request = body as { expected_revision: number; task: WorkspaceTask };
      if (request.expected_revision !== task.revision) {
        const error = new Error("任务已在其他窗口更新");
        Object.assign(error, { status: 409 });
        throw error;
      }
      const saved = { ...request.task, revision: task.revision + 1, lifecycle: "saved" as const, updated_at: new Date().toISOString() };
      tasks[index] = saved;
      return saved as T;
    }
    return structuredClone(task) as T;
  }
  if (path.startsWith("/v2/jobs?")) {
    const requestedWorkspace = new URLSearchParams(path.split("?", 2)[1]).get("workspace_id");
    return serviceJobs.filter((item) => !requestedWorkspace || item.repository_id === requestedWorkspace) as T;
  }
  if (path.startsWith("/v2/jobs/")) {
    const jobUrl = new URL(path, "https://mock.octopus.local");
    const cancel = jobUrl.pathname.endsWith("/cancel");
    const jobId = jobUrl.pathname.slice("/v2/jobs/".length).replace(/\/cancel$/, "");
    const requestedWorkspace = jobUrl.searchParams.get("workspace_id");
    const existing = serviceJobs.find((item) => item.job_id === jobId);
    if (!requestedWorkspace || existing?.repository_id !== requestedWorkspace) {
      throw new Error("后台任务不存在");
    }
    if (cancel && method === "POST") {
      existing.status = "canceled";
      existing.finished_at = new Date().toISOString();
      return existing as T;
    }
    return existing as T;
  }
  throw new Error(`Mock endpoint is not implemented: ${method} ${path}`);
}
