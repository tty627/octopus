import { bootstrapDesktop } from "./bridge";
import { mockRequest } from "./mockApi";
import type {
  AIConnectionResult,
  AISettings,
  AISettingsInput,
  BootstrapPayload,
  Repository,
  RepositoryEstimate,
  SearchFilters,
  SearchReport,
  ServiceJob,
  TaskPack,
  TaskPackSummary,
  ValidationReport,
} from "./types";

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
    readonly technicalDetail = "",
  ) {
    super(message);
  }
}

let bootstrapPromise: Promise<BootstrapPayload> | undefined;

export function runtimeBootstrap(): Promise<BootstrapPayload> {
  bootstrapPromise ??= bootstrapDesktop();
  return bootstrapPromise;
}

async function request<T>(
  path: string,
  options: { method?: string; body?: unknown; signal?: AbortSignal } = {},
): Promise<T> {
  const runtime = await runtimeBootstrap();
  const method = options.method ?? "GET";
  if (runtime.base_url.startsWith("mock:")) {
    try {
      return await mockRequest<T>(path, method, options.body, options.signal);
    } catch (error) {
      if (error instanceof ApiError) throw error;
      if (error instanceof Error && "status" in error) {
        const status = Number(error.status);
        const message = status === 409
          ? "任务包已经发生变化，请重新载入或保留本地草稿。"
          : error.message;
        throw new ApiError(message, status, error.message);
      }
      throw new ApiError(error instanceof Error ? error.message : "本地演示服务不可用", 500);
    }
  }
  let response: Response;
  try {
    response = await fetch(`${runtime.base_url}${path}`, {
      method,
      headers: {
        Authorization: `Bearer ${runtime.token}`,
        ...(options.body === undefined ? {} : { "Content-Type": "application/json" }),
      },
      body: options.body === undefined ? undefined : JSON.stringify(options.body),
      signal: options.signal,
    });
  } catch (error) {
    if (error instanceof DOMException && error.name === "AbortError") throw error;
    throw new ApiError("本地服务暂时不可用，正在保留当前工作。", 0, String(error));
  }
  if (!response.ok) {
    const detail = await response.text();
    let message = "操作没有完成，请重试。";
    if (response.status === 409) message = "任务包已经发生变化，请重新载入或保留本地草稿。";
    if (response.status === 404) message = "所选资料或任务包已经不可访问。";
    if (response.status === 422) message = "当前范围不满足操作条件，请检查选择内容。";
    throw new ApiError(message, response.status, detail);
  }
  if (response.headers.get("content-type")?.includes("text/plain")) {
    return (await response.text()) as T;
  }
  return (await response.json()) as T;
}

export const api = {
  repositories: () => request<Repository[]>("/v1/repositories"),
  preflight: (raw_path: string, index_path: string) =>
    request<RepositoryEstimate>("/v1/repositories/preflight", {
      method: "POST",
      body: { raw_path, index_path, ai_enabled: false },
    }),
  createRepository: (raw_path: string, index_path: string, name: string) =>
    request<{ repository: Repository; job: ServiceJob }>("/v1/repositories", {
      method: "POST",
      body: { raw_path, index_path, name, build: true },
    }),
  createSample: () =>
    request<{ repository: Repository; job: ServiceJob }>("/v1/repositories/sample", {
      method: "POST",
      body: { name: "Octopus 示例资料" },
    }),
  aiSettings: (repositoryId: string) =>
    request<AISettings>(`/v1/repositories/${repositoryId}/ai-settings`),
  saveAISettings: (repositoryId: string, settings: AISettingsInput) =>
    request<AISettings>(`/v1/repositories/${repositoryId}/ai-settings`, {
      method: "PUT",
      body: settings,
    }),
  testAISettings: (repositoryId: string, settings: Omit<AISettingsInput, "enabled">) =>
    request<AIConnectionResult>(`/v1/repositories/${repositoryId}/ai-settings/test`, {
      method: "POST",
      body: settings,
    }),
  search: (
    repositoryId: string,
    query: string,
    mode: "local" | "auto",
    filters: SearchFilters,
    signal?: AbortSignal,
  ) =>
    request<SearchReport>(`/v1/repositories/${repositoryId}/search`, {
      method: "POST",
      body: { query, mode, limit: 50, filters },
      signal,
    }),
  taskPacks: (repositoryId: string) =>
    request<TaskPackSummary[]>(`/v1/repositories/${repositoryId}/task-packs`),
  createTaskPack: (repositoryId: string, title: string, goal: string) =>
    request<TaskPack>(`/v1/repositories/${repositoryId}/task-packs`, {
      method: "POST",
      body: { title, goal },
    }),
  taskPack: (repositoryId: string, taskPackId: string) =>
    request<TaskPack>(`/v1/repositories/${repositoryId}/task-packs/${taskPackId}`),
  saveTaskPack: (pack: TaskPack) =>
    request<TaskPack>(
      `/v1/repositories/${pack.repository_id}/task-packs/${pack.task_pack_id}`,
      { method: "PUT", body: { expected_revision: pack.revision, task_pack: pack } },
    ),
  archiveTaskPack: (pack: TaskPack) =>
    request<TaskPack>(
      `/v1/repositories/${pack.repository_id}/task-packs/${pack.task_pack_id}/archive`,
      { method: "POST", body: { expected_revision: pack.revision } },
    ),
  taskPackMarkdown: (pack: TaskPack) =>
    request<string>(
      `/v1/repositories/${pack.repository_id}/task-packs/${pack.task_pack_id}/markdown`,
    ),
  packageTaskPack: (pack: TaskPack, output_path: string, confirmed_item_ids: string[]) =>
    request<ServiceJob>(
      `/v1/repositories/${pack.repository_id}/task-packs/${pack.task_pack_id}/package`,
      { method: "POST", body: { output_path, confirmed_item_ids } },
    ),
  updateRepository: (repositoryId: string, retryOnly = false) =>
    request<ServiceJob>(`/v1/repositories/${repositoryId}/updates`, {
      method: "POST",
      body: retryOnly ? { retry_only: true } : {},
    }),
  rebuildSearch: (repositoryId: string) =>
    request<ServiceJob>(`/v1/repositories/${repositoryId}/rebuild-search`, { method: "POST" }),
  validate: (repositoryId: string) =>
    request<ValidationReport>(`/v1/repositories/${repositoryId}/validate`, { method: "POST" }),
};
