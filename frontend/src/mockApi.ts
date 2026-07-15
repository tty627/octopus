import type {
  AISettingsInputV2,
  AISettingsV2,
  SearchReportV2,
  SearchResultV2,
  ServiceJob,
  Workspace,
  WorkspaceDocument,
  WorkspaceTask,
  WorkspaceTaskSummary,
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
    document_count: 12,
    readable_count: 9,
    partial_count: 2,
    low_quality_count: 1,
    metadata_only_count: 0,
    failed_count: 0,
    last_sync_at: now,
  },
};

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
  source_uri: `file:///C:/Users/Demo/Documents/${encodeURIComponent(relativePath)}`,
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
  indexing_state: "indexed",
  error: "",
  source_uri: item.source_uri,
}));

let aiSettings: AISettingsV2 = {
  workspace_id: workspace.workspace_id,
  enabled: false,
  provider: "deepseek",
  base_url: "https://api.deepseek.com",
  model: "deepseek-v4-flash",
  credential_configured: false,
  credential_source: "none",
  credential_error: "",
  vision_enabled: false,
};

let tasks: WorkspaceTask[] = [];

function createTask(title: string, goal: string): WorkspaceTask {
  const task: WorkspaceTask = {
    schema_version: "2.0",
    task_id: crypto.randomUUID(),
    workspace_id: workspace.workspace_id,
    revision: 1,
    lifecycle: "draft",
    title,
    goal,
    slots: [
      { slot_id: crypto.randomUUID(), name: "核心证据", description: "", position: 0, required: true },
      { slot_id: crypto.randomUUID(), name: "补充证据", description: "", position: 1, required: false },
      { slot_id: crypto.randomUUID(), name: "待核验", description: "", position: 2, required: false },
    ],
    items: [],
    created_at: now,
    updated_at: now,
    migrated_from_v1: false,
  };
  tasks = [task, ...tasks];
  return task;
}

function summaries(): WorkspaceTaskSummary[] {
  return tasks.filter((task) => task.lifecycle !== "archived").map((task) => ({
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
  }));
}

function delay(ms: number, signal?: AbortSignal): Promise<void> {
  return new Promise((resolve, reject) => {
    const timer = window.setTimeout(resolve, ms);
    signal?.addEventListener("abort", () => {
      window.clearTimeout(timer);
      reject(new DOMException("Aborted", "AbortError"));
    }, { once: true });
  });
}

function searchReport(query: string, mode: "local" | "assisted"): SearchReportV2 {
  const filtered = query.includes("级数")
    ? allResults.filter((item) => item.name.includes("级数"))
    : query.includes("微分方程")
      ? allResults.filter((item) => item.name.includes("微分方程"))
      : allResults;
  const assisted = mode === "assisted" && aiSettings.enabled && aiSettings.credential_configured;
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

function job(): ServiceJob {
  return {
    job_id: crypto.randomUUID(),
    repository_id: workspace.workspace_id,
    kind: "workspace_sync",
    status: "succeeded",
    result: {},
    error_code: "",
    error_message: "",
  };
}

export function mockPreviewUrl(documentId: string, page: number): string {
  const document = documents.find((item) => item.document_id === documentId);
  const title = document?.name ?? "页面证据";
  const svg = `<svg xmlns="http://www.w3.org/2000/svg" width="760" height="1020"><rect width="100%" height="100%" fill="#fff"/><text x="70" y="90" font-family="sans-serif" font-size="25" fill="#17201e">${title}</text><text x="70" y="140" font-family="sans-serif" font-size="18" fill="#64706c">第 ${page} 页</text><line x1="70" y1="175" x2="690" y2="175" stroke="#ccd5d1"/><text x="70" y="235" font-family="sans-serif" font-size="20" fill="#283431">级数与微分方程的页面证据预览</text><rect x="65" y="270" width="630" height="54" fill="#fff1a8" opacity=".75"/><text x="78" y="305" font-family="sans-serif" font-size="18" fill="#283431">命中内容位于当前页面，原文件保持只读。</text></svg>`;
  return `data:image/svg+xml;charset=utf-8,${encodeURIComponent(svg)}`;
}

export async function mockRequest<T>(
  path: string,
  method: string,
  body?: unknown,
  signal?: AbortSignal,
): Promise<T> {
  await delay(path.endsWith("/search") ? 100 : 35, signal);
  if (path === "/v2/workspaces" && method === "GET") return [workspace] as T;
  if (path === "/v2/workspaces" && method === "POST") return { workspace, job: job() } as T;
  if (path === `/v2/workspaces/${workspace.workspace_id}` && method === "GET") return workspace as T;
  if (path.endsWith("/sync") && method === "POST") return job() as T;
  if (path.endsWith("/documents") && method === "GET") return documents as T;
  if (path.endsWith("/reprocess") && method === "POST") return job() as T;
  if (path.endsWith("/search") && method === "POST") {
    const request = body as { query: string; mode: "local" | "assisted" };
    return searchReport(request.query, request.mode) as T;
  }
  if (path.endsWith("/ai-settings/test") && method === "POST") {
    const request = body as AISettingsInputV2;
    const configured = Boolean(request.api_key || aiSettings.credential_configured);
    return { ok: configured, code: configured ? "connected" : "key_not_configured", message: configured ? `已连接 ${request.model}。` : "请先填写 API Key。" } as T;
  }
  if (path.endsWith("/ai-settings") && method === "GET") return structuredClone(aiSettings) as T;
  if (path.endsWith("/ai-settings") && method === "PUT") {
    const request = body as AISettingsInputV2;
    const configured = request.clear_api_key ? false : Boolean(request.api_key || aiSettings.credential_configured);
    if (request.enabled && !configured) {
      const error = new Error("启用 AI 前需要 API Key");
      Object.assign(error, { status: 422 });
      throw error;
    }
    aiSettings = { ...aiSettings, ...request, credential_configured: configured, credential_source: configured ? "windows_credential" : "none" };
    return structuredClone(aiSettings) as T;
  }
  if (path.endsWith("/vision-authorization") && method === "PUT") {
    const value = (body as { vision_enabled: boolean }).vision_enabled;
    workspace.vision_enabled = value;
    aiSettings.vision_enabled = value;
    return { workspace_id: workspace.workspace_id, vision_enabled: value } as T;
  }
  if (path.endsWith("/tasks") && method === "GET") return summaries() as T;
  if (path.endsWith("/tasks") && method === "POST") {
    const request = body as { title: string; goal: string };
    return createTask(request.title, request.goal) as T;
  }
  const taskMatch = path.match(/\/tasks\/([^/]+)(?:\/(markdown|archive))?$/);
  if (taskMatch) {
    const taskId = taskMatch[1] ?? "";
    const action = taskMatch[2];
    const index = tasks.findIndex((item) => item.task_id === taskId);
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
  if (path.startsWith("/v2/jobs/")) return job() as T;
  throw new Error(`Mock endpoint is not implemented: ${method} ${path}`);
}
