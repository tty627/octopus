import type {
  AISettings,
  AISettingsInput,
  Repository,
  RepositoryEstimate,
  SearchReport,
  SearchResult,
  ServiceJob,
  TaskPack,
  TaskPackSummary,
  ValidationReport,
} from "./types";

const now = new Date().toISOString();

const repository: Repository = {
  repository_id: "demo-repository",
  name: "新能源项目资料",
  raw_repository_path: "C:\\Users\\Demo\\Documents\\新能源项目",
  index_repository_path: "C:\\Users\\Demo\\Documents\\新能源项目-Octopus-Index",
  available: true,
  enabled: true,
  last_successful_update_at: new Date(Date.now() - 8 * 60_000).toISOString(),
  states: { indexed: 184, pending_stable: 2 },
  scan: { scan_generation: 18, last_scan_at: now },
};

let aiSettings: AISettings = {
  repository_id: repository.repository_id,
  enabled: false,
  provider: "deepseek",
  base_url: "https://api.deepseek.com",
  model: "deepseek-v4-flash",
  credential_configured: false,
  credential_source: "none",
  credential_error: "",
};

const result = (
  rank: number,
  name: string,
  type: SearchResult["index_type"],
  path: string,
  summary: string,
  reason: string,
  overrides: Partial<SearchResult> = {},
): SearchResult => ({
  node_id: `demo-node-${rank}`,
  index_type: type,
  index_path: `C:\\Demo\\Index\\${path}.md`,
  raw_relative_path: path,
  name,
  summary,
  description: summary,
  status: "indexed",
  source_uri: `file:///C:/Users/Demo/Documents/${encodeURIComponent(path)}`,
  content_id: `sha256:demo-${rank}`,
  modified_at: new Date(Date.now() - rank * 86_400_000).toISOString(),
  size_bytes: 180_000 * rank,
  evidence: [
    {
      locator: rank === 2 ? "Sheet:预算 B12:F28" : `第 ${rank * 3 + 1} 页`,
      kind: rank === 2 ? "worksheet_range" : "page",
      text_excerpt: reason,
      extraction_method: "native",
      confidence: 0.96,
    },
  ],
  quality_flags: [],
  risk_flags: [],
  rank,
  score: 10 - rank,
  match_reasons: [reason],
  match_evidence: [
    { field: "body", locator: "正文", excerpt: reason, matched_terms: ["项目"] },
  ],
  explanation: `这份资料直接支持当前任务：${reason}`,
  recommended_open_target: "source",
  open_target_uri: `file:///C:/Users/Demo/Documents/${encodeURIComponent(path)}`,
  ...overrides,
});

const localResults: SearchResult[] = [
  result(
    1,
    "项目季度进展汇报.pdf",
    "leaf",
    "03-交付\\项目季度进展汇报.pdf",
    "汇总本季度里程碑、交付状态和下一阶段计划。",
    "标题与正文同时命中“季度汇报”和“项目进展”",
  ),
  result(
    2,
    "项目预算总表.xlsx",
    "leaf",
    "02-预算\\项目预算总表.xlsx",
    "包含预算分类、审批状态、实际支出与剩余额度。",
    "预算 Sheet 中出现审批状态与季度支出",
  ),
  result(
    3,
    "项目关键决策纪要.docx",
    "leaf",
    "01-会议\\项目关键决策纪要.docx",
    "记录范围调整、供应商选择和延期处置决策。",
    "正文中的“范围变更”和“最终决策”命中任务目标",
  ),
  result(
    4,
    "新能源项目",
    "foldernode",
    "新能源项目",
    "项目完整资料目录，覆盖需求、预算、会议与交付。",
    "该文件夹聚合了 42 项与任务相关的资料",
  ),
  result(
    5,
    "风险清单-扫描件.pdf",
    "leaf",
    "04-风险\\风险清单-扫描件.pdf",
    "扫描件列出工程、采购和验收风险。",
    "OCR 文本命中“季度风险”",
    { quality_flags: ["ocr_low_confidence"], risk_flags: ["extraction_risk"] },
  ),
  result(
    6,
    "验收问题跟踪.md",
    "text",
    "03-交付\\验收问题跟踪.md",
    "跟踪当前未关闭问题与负责人。",
    "文件名和正文命中“验收进展”",
  ),
];

const summaries = (): TaskPackSummary[] =>
  packs
    .filter((pack) => pack.lifecycle !== "archived")
    .map((pack) => ({
      schema_version: pack.schema_version,
      task_pack_id: pack.task_pack_id,
      repository_id: pack.repository_id,
      revision: pack.revision,
      lifecycle: pack.lifecycle,
      title: pack.title,
      goal: pack.goal,
      item_count: pack.items.length,
      pending_count: pack.items.filter((item) => item.review_state === "pending").length,
      updated_at: pack.updated_at,
      writable: true,
    }));

let packs: TaskPack[] = [];

function createPack(title: string, goal: string): TaskPack {
  const pack: TaskPack = {
    schema_version: "1.0",
    task_pack_id: crypto.randomUUID(),
    repository_id: repository.repository_id,
    revision: 1,
    lifecycle: "draft",
    title,
    goal,
    slots: [
      {
        slot_id: crypto.randomUUID(),
        name: "核心资料",
        description: "直接支持当前任务的主要来源。",
        position: 0,
        required: true,
      },
      {
        slot_id: crypto.randomUUID(),
        name: "补充资料",
        description: "提供背景、上下文或旁证的来源。",
        position: 1,
        required: false,
      },
      {
        slot_id: crypto.randomUUID(),
        name: "待核验",
        description: "存在版本、状态或抽取质量风险的来源。",
        position: 2,
        required: false,
      },
    ],
    items: [],
    excluded_node_ids: [],
    created_at: now,
    updated_at: now,
  };
  packs = [pack, ...packs];
  return pack;
}

function delay(ms: number, signal?: AbortSignal): Promise<void> {
  return new Promise((resolve, reject) => {
    const timer = window.setTimeout(resolve, ms);
    signal?.addEventListener(
      "abort",
      () => {
        window.clearTimeout(timer);
        reject(new DOMException("Aborted", "AbortError"));
      },
      { once: true },
    );
  });
}

function searchReport(query: string, mode: "local" | "auto"): SearchReport {
  const aiRequested = mode === "auto";
  const ai = aiRequested && aiSettings.enabled && aiSettings.credential_configured;
  const degraded = aiRequested && !ai;
  return {
    query,
    requested_mode: mode,
    actual_mode: ai ? "ai" : degraded ? "degraded" : "local",
    degradation_reason: degraded
      ? aiSettings.enabled ? "ai_key_not_configured" : "ai_disabled"
      : "",
    answer: {
      summary: ai
        ? "AI 建议：先阅读季度进展，再用预算总表和决策纪要核对范围与资源。"
        : "已按本地索引找到可核验资料。",
      recommended_node_ids: localResults.slice(0, 3).map((item) => item.node_id),
      warnings: degraded ? ["AI 未参与，本地结果保持完整。"] : [],
      cited_node_ids: localResults.slice(0, 3).map((item) => item.node_id),
    },
    results: ai
      ? localResults.map((item, index) => ({
          ...item,
          rank: index + 1,
          explanation: `AI 建议：${item.explanation}`,
        }))
      : localResults,
    candidate_count: localResults.length,
    duration_ms: ai ? 680 : 86,
    ai_usage: ai ? { calls: 2, total_tokens: 420, models: { [aiSettings.model]: 2 } } : undefined,
  };
}

export async function mockRequest<T>(
  path: string,
  method: string,
  body?: unknown,
  signal?: AbortSignal,
): Promise<T> {
  await delay(path.endsWith("/search") ? (body as { mode?: string }).mode === "auto" ? 520 : 120 : 50, signal);
  if (path === "/v1/repositories" && method === "GET") return [repository] as T;
  if (path === "/v1/repositories/preflight" && method === "POST") {
    const request = body as { raw_path: string; index_path: string };
    return {
      raw_path: request.raw_path,
      index_path: request.index_path,
      file_count: 186,
      directory_count: 23,
      supported_file_count: 178,
      unsupported_file_count: 8,
      format_counts: { ".pdf": 48, ".docx": 31, ".xlsx": 22, ".md": 61, ".png": 24 },
      total_source_bytes: 2_860_000_000,
      estimated_index_bytes: 238_000_000,
      required_free_bytes: 476_000_000,
      available_free_bytes: 82_000_000_000,
      estimated_seconds_p50: 94,
      estimated_seconds_p95: 220,
      blockers: [],
      warnings: [],
    } satisfies RepositoryEstimate as T;
  }
  if ((path === "/v1/repositories" || path === "/v1/repositories/sample") && method === "POST") {
    return {
      repository,
      job: { job_id: "demo-build", repository_id: repository.repository_id, kind: "update", status: "running" },
    } as T;
  }
  if (path.endsWith("/ai-settings/test") && method === "POST") {
    const request = body as Omit<AISettingsInput, "enabled">;
    const configured = Boolean(request.api_key || aiSettings.credential_configured);
    return {
      ok: configured,
      code: configured ? "connected" : "key_not_configured",
      message: configured ? `已连接 ${request.model}。` : "请先填写 API Key。",
    } as T;
  }
  if (path.endsWith("/ai-settings") && method === "GET") {
    return structuredClone(aiSettings) as T;
  }
  if (path.endsWith("/ai-settings") && method === "PUT") {
    const request = body as AISettingsInput;
    const credentialConfigured = request.clear_api_key
      ? false
      : Boolean(request.api_key || aiSettings.credential_configured);
    if (request.enabled && !credentialConfigured) {
      const error = new Error("启用 AI 前需要 API Key");
      Object.assign(error, { status: 422 });
      throw error;
    }
    aiSettings = {
      ...aiSettings,
      enabled: request.enabled,
      provider: request.provider,
      base_url: request.base_url,
      model: request.model,
      credential_configured: credentialConfigured,
      credential_source: credentialConfigured ? "windows_credential" : "none",
    };
    return structuredClone(aiSettings) as T;
  }
  if (path.endsWith("/search") && method === "POST") {
    const request = body as { query: string; mode: "local" | "auto" };
    return searchReport(request.query, request.mode) as T;
  }
  if (path.endsWith("/task-packs") && method === "GET") return summaries() as T;
  if (path.endsWith("/task-packs") && method === "POST") {
    const request = body as { title: string; goal: string };
    return createPack(request.title, request.goal) as T;
  }
  const packMatch = path.match(/\/task-packs\/([^/]+)(?:\/(markdown|archive|package))?$/);
  if (packMatch) {
    const id = packMatch[1] ?? "";
    const action = packMatch[2];
    const index = packs.findIndex((pack) => pack.task_pack_id === id);
    const pack = packs[index];
    if (!pack) throw new Error("任务包不存在");
    if (action === "markdown" && method === "GET") {
      return `# ${pack.title}\n\n${pack.goal}\n\n${pack.slots
        .map((slot) => `## ${slot.name}\n\n${pack.items.filter((item) => item.slot_id === slot.slot_id).map((item) => `- **${item.name}** · ${item.review_state === "confirmed" ? "已确认" : "待核验"}`).join("\n") || "- 暂无资料"}`)
        .join("\n\n")}\n` as T;
    }
    if (action === "archive" && method === "POST") {
      const archived = { ...pack, lifecycle: "archived" as const, revision: pack.revision + 1 };
      packs[index] = archived;
      return archived as T;
    }
    if (action === "package" && method === "POST") {
      return {
        job_id: crypto.randomUUID(),
        repository_id: repository.repository_id,
        kind: "package",
        status: "succeeded",
        result: { exported: true },
        error_code: "",
        error_message: "",
      } satisfies ServiceJob as T;
    }
    if (method === "PUT") {
      const request = body as { expected_revision: number; task_pack: TaskPack };
      if (request.expected_revision !== pack.revision) {
        const error = new Error("任务包已在其他窗口更新");
        Object.assign(error, { status: 409 });
        throw error;
      }
      const saved = {
        ...request.task_pack,
        revision: pack.revision + 1,
        lifecycle: "saved" as const,
        updated_at: new Date().toISOString(),
      };
      packs[index] = saved;
      return saved as T;
    }
    return structuredClone(pack) as T;
  }
  if (path.endsWith("/validate") && method === "POST") {
    return { error_count: 0, warning_count: 0, issues: [] } satisfies ValidationReport as T;
  }
  if (path.includes("/updates") || path.includes("/rebuild-search")) {
    return {
      job_id: crypto.randomUUID(),
      repository_id: repository.repository_id,
      kind: path.includes("rebuild") ? "rebuild_search" : "update",
      status: "running",
      result: {},
      error_code: "",
      error_message: "",
    } satisfies ServiceJob as T;
  }
  throw new Error(`Mock endpoint is not implemented: ${method} ${path}`);
}
