import { bootstrapDesktop } from "./bridge";
import {
  mockContentUrl,
  mockExportArtifactBlob,
  mockPreviewUrl,
  mockRequest,
  mockResearchPackBlob,
} from "./mockApi";
import type {
  AIConnectionResult,
  AISettingsInputV2,
  AISettingsV2,
  AIIndexStatus,
  AIProviderPreset,
  BootstrapPayload,
  SearchFiltersV2,
  SearchReportV2,
  ServiceJob,
  OpenTargetResponse,
  ResearchPackExportRequest,
  Workspace,
  WorkspaceChange,
  WorkspaceDocument,
  WorkspaceTask,
  WorkspaceTaskSummary,
  TaskTemplateId,
  ResearchTaskProposal,
  WorkspaceResearchResult,
  VisionAnalysis,
  VisionPreflight,
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

export function apiErrorMessage(status: number, detail: string): string {
  if (status === 409) return "任务或同步状态已经变化，请重新载入。";
  if (status === 404) return "所选资料或任务已经不可访问。";
  if (status === 422) {
    if (/overlaps existing workspace/i.test(detail)) {
      return "所选文件夹与已有资料空间范围重叠，请选择不重叠的文件夹。";
    }
    if (/api.?key|credential|凭据/i.test(detail)) {
      return "请填写有效的 API Key 后重试。";
    }
    if (/base.?url/i.test(detail)) return "Base URL 无效，请检查后重试。";
    if (/model/i.test(detail)) return "模型名称无效，请检查后重试。";
    return "输入内容无法处理，请检查标记的字段后重试。";
  }
  return "操作没有完成，请重试。";
}

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
        throw new ApiError(error.message, status, error.message);
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
    throw new ApiError("本地服务暂时不可用，当前任务草稿已保留。", 0, String(error));
  }
  if (!response.ok) {
    const detail = await response.text();
    throw new ApiError(apiErrorMessage(response.status, detail), response.status, detail);
  }
  if (response.headers.get("content-type")?.includes("text/plain")) {
    return (await response.text()) as T;
  }
  return (await response.json()) as T;
}

async function previewUrl(
  workspaceId: string,
  documentId: string,
  page: number,
  highlight = "",
  variant: "base" | "highlighted" = highlight ? "highlighted" : "base",
): Promise<string> {
  const runtime = await runtimeBootstrap();
  if (runtime.base_url.startsWith("mock:")) return mockPreviewUrl(documentId, page);
  const parameters = new URLSearchParams({ variant });
  if (highlight) parameters.set("highlight", highlight);
  const response = await fetch(
    `${runtime.base_url}/v2/workspaces/${workspaceId}/documents/${documentId}/pages/${page}/preview?${parameters}`,
    { headers: { Authorization: `Bearer ${runtime.token}` } },
  );
  if (!response.ok) throw new ApiError("页面预览暂时不可用。", response.status);
  return URL.createObjectURL(await response.blob());
}

async function contentUrl(workspaceId: string, documentId: string): Promise<string> {
  const runtime = await runtimeBootstrap();
  if (runtime.base_url.startsWith("mock:")) return mockContentUrl(documentId);
  const response = await fetch(
    `${runtime.base_url}/v2/workspaces/${workspaceId}/documents/${documentId}/content`,
    { headers: { Authorization: `Bearer ${runtime.token}` } },
  );
  if (!response.ok) throw new ApiError("内容预览暂时不可用。", response.status);
  return URL.createObjectURL(await response.blob());
}

async function taskExport(
  task: WorkspaceTask,
  options: ResearchPackExportRequest,
): Promise<Blob> {
  const runtime = await runtimeBootstrap();
  if (runtime.base_url.startsWith("mock:")) {
    return mockResearchPackBlob(task, options);
  }
  const response = await fetch(
    `${runtime.base_url}/v2/workspaces/${task.workspace_id}/tasks/${task.task_id}/export`,
    {
      method: "POST",
      headers: {
        Authorization: `Bearer ${runtime.token}`,
        "Content-Type": "application/json",
      },
      body: JSON.stringify(options),
    },
  );
  if (!response.ok) {
    const detail = await response.text();
    throw new ApiError(apiErrorMessage(response.status, detail), response.status, detail);
  }
  return response.blob();
}

async function exportArtifact(
  workspaceId: string,
  artifactId: string,
): Promise<Blob> {
  const runtime = await runtimeBootstrap();
  if (runtime.base_url.startsWith("mock:")) {
    return mockExportArtifactBlob(artifactId);
  }
  const response = await fetch(
    `${runtime.base_url}/v2/workspaces/${workspaceId}/exports/${artifactId}`,
    { headers: { Authorization: `Bearer ${runtime.token}` } },
  );
  if (!response.ok) {
    const detail = await response.text();
    throw new ApiError(apiErrorMessage(response.status, detail), response.status, detail);
  }
  return response.blob();
}

export const api = {
  workspaces: () => request<Workspace[]>("/v2/workspaces"),
  workspace: (workspaceId: string) => request<Workspace>(`/v2/workspaces/${workspaceId}`),
  createWorkspace: (raw_path: string, name: string) =>
    request<{ workspace: Workspace; job: ServiceJob }>("/v2/workspaces", {
      method: "POST",
      body: { raw_path, name },
    }),
  syncWorkspace: (workspaceId: string) =>
    request<ServiceJob>(`/v2/workspaces/${workspaceId}/sync`, { method: "POST" }),
  documents: (workspaceId: string) =>
    request<WorkspaceDocument[]>(`/v2/workspaces/${workspaceId}/documents`),
  documentMembers: (workspaceId: string, documentId: string) =>
    request<WorkspaceDocument[]>(
      `/v2/workspaces/${workspaceId}/documents/${documentId}/members`,
    ),
  reprocessDocument: (workspaceId: string, documentId: string) =>
    request<ServiceJob>(
      `/v2/workspaces/${workspaceId}/documents/${documentId}/reprocess`,
      { method: "POST" },
    ),
  search: (
    workspaceId: string,
    query: string,
    mode: "local" | "assisted",
    filters: SearchFiltersV2,
    signal?: AbortSignal,
  ) =>
    request<SearchReportV2>(`/v2/workspaces/${workspaceId}/search`, {
      method: "POST",
      body: { query, mode, limit: 50, ...filters },
      signal,
    }),
  previewUrl,
  contentUrl,
  openTarget: (workspaceId: string, documentId: string) =>
    request<OpenTargetResponse>(
      `/v2/workspaces/${workspaceId}/documents/${documentId}/open-target`,
      { method: "POST" },
    ),
  aiSettings: (workspaceId: string) =>
    request<AISettingsV2>(`/v2/workspaces/${workspaceId}/ai-settings`),
  aiIndexStatus: (workspaceId: string) =>
    request<AIIndexStatus>(`/v2/workspaces/${workspaceId}/ai-index`),
  aiProviderPresets: () => request<AIProviderPreset[]>("/v2/ai-provider-presets"),
  startAIIndex: (
    workspaceId: string,
    options: number | {
      scope?: "all" | "documents" | "folders";
      max_calls?: number;
      retry_failed?: boolean;
    } = {},
  ) =>
    request<ServiceJob>(`/v2/workspaces/${workspaceId}/ai-index`, {
      method: "POST",
      body: typeof options === "number" ? { limit: options } : options,
    }),
  saveAISettings: (workspaceId: string, settings: AISettingsInputV2) =>
    request<AISettingsV2>(`/v2/workspaces/${workspaceId}/ai-settings`, {
      method: "PUT",
      body: settings,
    }),
  testAISettings: (workspaceId: string, settings: AISettingsInputV2) =>
    request<AIConnectionResult>(`/v2/workspaces/${workspaceId}/ai-settings/test`, {
      method: "POST",
      body: settings,
    }),
  setVisionAuthorization: (workspaceId: string, vision_enabled: boolean) =>
    request<{ workspace_id: string; vision_enabled: boolean }>(
      `/v2/workspaces/${workspaceId}/vision-authorization`,
      { method: "PUT", body: { vision_enabled } },
    ),
  visionPreflight: (workspaceId: string, documentId: string, page_number: number) =>
    request<VisionPreflight>(
      `/v2/workspaces/${workspaceId}/documents/${documentId}/vision/preflight`,
      { method: "POST", body: { page_number } },
    ),
  analyzeVisionPage: (
    workspaceId: string,
    documentId: string,
    page_number: number,
    prompt: string,
    confirm_image_send: boolean,
  ) =>
    request<VisionAnalysis>(
      `/v2/workspaces/${workspaceId}/documents/${documentId}/vision/analyze`,
      { method: "POST", body: { page_number, prompt, confirm_image_send } },
    ),
  tasks: (workspaceId: string) =>
    request<WorkspaceTaskSummary[]>(`/v2/workspaces/${workspaceId}/tasks`),
  createTask: (
    workspaceId: string,
    title: string,
    goal: string,
    template_id?: TaskTemplateId,
  ) =>
    request<WorkspaceTask>(`/v2/workspaces/${workspaceId}/tasks`, {
      method: "POST",
      body: { title, goal, ...(template_id ? { template_id } : {}) },
    }),
  createResearchProposal: (workspaceId: string, goal: string, title = "", template_id = "free_research") =>
    request<ResearchTaskProposal>(`/v2/workspaces/${workspaceId}/task-proposals`, {
      method: "POST",
      body: { goal, title, template_id },
    }),
  startResearchProposal: (workspaceId: string, goal: string, title = "", template_id = "free_research") =>
    request<ServiceJob>(`/v2/workspaces/${workspaceId}/task-proposals/jobs`, {
      method: "POST",
      body: { goal, title, template_id },
    }),
  startResearch: (workspaceId: string, question: string, filters: SearchFiltersV2) =>
    request<ServiceJob>(`/v2/workspaces/${workspaceId}/research`, {
      method: "POST",
      body: { question, limit: 50, ...filters },
    }),
  confirmResearchProposal: (workspaceId: string, proposal: ResearchTaskProposal) =>
    request<WorkspaceTask>(`/v2/workspaces/${workspaceId}/task-proposals/confirm`, {
      method: "POST",
      body: { proposal },
    }),
  task: (workspaceId: string, taskId: string) =>
    request<WorkspaceTask>(`/v2/workspaces/${workspaceId}/tasks/${taskId}`),
  saveTask: (task: WorkspaceTask) =>
    request<WorkspaceTask>(
      `/v2/workspaces/${task.workspace_id}/tasks/${task.task_id}`,
      { method: "PUT", body: { expected_revision: task.revision, task } },
    ),
  archiveTask: (task: WorkspaceTask) =>
    request<WorkspaceTask>(
      `/v2/workspaces/${task.workspace_id}/tasks/${task.task_id}/archive`,
      { method: "POST", body: { expected_revision: task.revision } },
    ),
  taskMarkdown: (task: WorkspaceTask) =>
    request<string>(
      `/v2/workspaces/${task.workspace_id}/tasks/${task.task_id}/markdown`,
    ),
  taskExport,
  startTaskExport: (task: WorkspaceTask, options: ResearchPackExportRequest) =>
    request<ServiceJob>(
      `/v2/workspaces/${task.workspace_id}/tasks/${task.task_id}/exports`,
      { method: "POST", body: options },
    ),
  exportArtifact,
  revalidateTask: (task: WorkspaceTask) =>
    request<WorkspaceTask>(
      `/v2/workspaces/${task.workspace_id}/tasks/${task.task_id}/revalidate`,
      { method: "POST", body: { expected_revision: task.revision } },
    ),
  changes: (workspaceId: string) =>
    request<WorkspaceChange[]>(`/v2/workspaces/${workspaceId}/changes`),
  jobs: (workspaceId: string, signal?: AbortSignal) =>
    request<ServiceJob[]>(`/v2/jobs?workspace_id=${encodeURIComponent(workspaceId)}`, { signal }),
  job: (jobId: string) => request<ServiceJob>(`/v2/jobs/${jobId}`),
  researchResult: (job: ServiceJob) => job.result as unknown as WorkspaceResearchResult,
  cancelJob: (jobId: string) =>
    request<ServiceJob>(`/v2/jobs/${jobId}/cancel`, { method: "POST" }),
};
