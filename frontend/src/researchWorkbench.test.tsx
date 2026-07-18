import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, describe, expect, it, vi } from "vitest";
import { api } from "./api";
import * as bridge from "./bridge";
import { EvidenceInspector } from "./components/EvidenceInspector";
import { SearchWorkspace } from "./components/SearchWorkspace";
import { TaskPacksView } from "./components/TaskPacksView";
import { TaskCenter } from "./components/TaskCenter";
import { locatorLabel, sourceKindLabel } from "./components/researchLabels";
import { EMPTY_FILTERS, useAppStore } from "./store";
import type { SearchResultV2, WorkspaceTask } from "./types";

function renderWithClient(ui: React.ReactNode) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  render(<QueryClientProvider client={client}>{ui}</QueryClientProvider>);
  return client;
}

function result(documentId: string, name: string, overrides: Partial<SearchResultV2> = {}): SearchResultV2 {
  return {
    document_id: documentId,
    name,
    relative_path: name,
    extension: name.slice(name.lastIndexOf(".")),
    content_hash: `${documentId}-hash`,
    size_bytes: 100,
    modified_at: "2026-07-16T00:00:00Z",
    page_count: 0,
    readability: "readable",
    readability_score: 1,
    indexing_state: "indexed",
    source_uri: `file:///${name}`,
    source_ref: { kind: "physical", workspace_path: name, virtual_path: name },
    freshness_status: "current",
    overview: "证据摘录",
    best_evidence: { page_number: null, heading: "", excerpt: "证据摘录", reason: "正文命中", quality_score: 1 },
    additional_evidence: [],
    rank: 1,
    ...overrides,
  };
}

function task(): WorkspaceTask {
  return {
    schema_version: "2.1",
    task_id: "task-1",
    workspace_id: "workspace-1",
    revision: 1,
    lifecycle: "saved",
    title: "课程研究",
    goal: "核对研究证据",
    slots: [{ slot_id: "core", name: "核心证据", description: "", position: 0, required: true }],
    items: [],
    created_at: "2026-07-16T00:00:00Z",
    updated_at: "2026-07-16T00:00:00Z",
    migrated_from_v1: false,
    template_id: "course_report",
    citation_style: "gb-t-7714-2015",
  };
}

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  useAppStore.setState({
    workspaceId: "",
    activeTask: null,
    inspector: null,
    inspectorOpen: false,
    query: "",
    submittedQuery: "",
    filters: EMPTY_FILTERS,
    taskDirty: false,
    saveState: "idle",
  });
});

describe("research source presentation", () => {
  it("formats Office and archive-member locators without inventing pages", () => {
    expect(locatorLabel({ kind: "sheet", sheet_name: "结果", cell_range: "A1:F18" })).toBe("结果 · A1:F18");
    expect(locatorLabel({ kind: "slide", slide_number: 12 })).toBe("第 12 张幻灯片");
    expect(sourceKindLabel({ source_ref: { kind: "archive_member", workspace_path: "课程.zip", virtual_path: "课程.zip!/论文.pdf" } })).toBe("ZIP 内文件");
  });

  it("opens the authenticated target instead of the legacy source URI", async () => {
    const archived = result("archive-member", "论文.pdf", {
      source_uri: "file:///legacy-container.zip",
      source_ref: { kind: "archive_member", workspace_path: "课程.zip", virtual_path: "课程.zip!/论文.pdf" },
      best_evidence: { page_number: 2, locator: { kind: "page", page_number: 2 }, heading: "", excerpt: "证据摘录", reason: "正文命中", quality_score: 1 },
      page_count: 5,
    });
    useAppStore.setState({ workspaceId: "workspace-1", inspector: archived, inspectorOpen: true });
    vi.spyOn(api, "previewUrl").mockResolvedValue("data:image/png;base64,preview");
    vi.spyOn(api, "openTarget").mockResolvedValue({ uri: "file:///temporary/论文.pdf", temporary: true, expires_at: "", display_name: "论文.pdf" });
    const opened = vi.spyOn(bridge, "openLocalUri").mockResolvedValue(undefined);
    const client = renderWithClient(<EvidenceInspector onAdd={vi.fn()} adding={false} actionError="" />);

    await userEvent.click(screen.getByRole("button", { name: "打开来源" }));
    await waitFor(() => expect(opened).toHaveBeenCalledWith("file:///temporary/论文.pdf"));
    expect(screen.getAllByText("ZIP 内文件").length).toBeGreaterThan(0);
    client.clear();
  });

  it("shows single-page vision details before explicit transmission", async () => {
    const image = result("image-1", "访谈编码.png", {
      page_count: 1,
      best_evidence: { page_number: null, heading: "", excerpt: "访谈编码", reason: "OCR 命中", quality_score: 0.8 },
    });
    useAppStore.setState({ workspaceId: "workspace-1", inspector: image, inspectorOpen: true });
    vi.spyOn(api, "contentUrl").mockResolvedValue("data:image/png;base64,preview");
    vi.spyOn(api, "visionPreflight").mockResolvedValue({
      workspace_id: "workspace-1",
      document_id: "image-1",
      page_number: 1,
      model: "glm-vision",
      mode: "vision",
      image_size_bytes: 1024,
      width: 1200,
      height: 1600,
      max_edge: 1600,
      pricing_configured: false,
      cost_estimate_status: "unknown",
      requires_confirmation: true,
      warning: "",
    });
    const analyze = vi.spyOn(api, "analyzeVisionPage").mockResolvedValue({
      workspace_id: "workspace-1",
      document_id: "image-1",
      page_number: 1,
      model: "glm-vision",
      mode: "vision",
      image_size_bytes: 1024,
      width: 1200,
      height: 1600,
      max_edge: 1600,
      pricing_configured: false,
      cost_estimate_status: "unknown",
      requires_confirmation: true,
      warning: "",
      answer: "页面展示了访谈编码表。",
      usage: { calls: 1, input_tokens: 10, output_tokens: 8, total_tokens: 18, duration_ms: 40, estimated_cost: null },
      cost_known: false,
    });
    const client = renderWithClient(<EvidenceInspector onAdd={vi.fn()} adding={false} actionError="" />);

    await userEvent.click(screen.getByRole("button", { name: "准备分析" }));
    expect(await screen.findByText("glm-vision")).toBeVisible();
    expect(screen.getByLabelText("当前页视觉分析")).toHaveTextContent("1200 × 1600 · 1.0 KiB");
    expect(analyze).not.toHaveBeenCalled();

    await userEvent.click(screen.getByRole("button", { name: "确认发送并分析" }));
    await waitFor(() => expect(analyze).toHaveBeenCalledWith(
      "workspace-1",
      "image-1",
      1,
      expect.any(String),
      true,
    ));
    expect(await screen.findByText("页面展示了访谈编码表。")).toBeVisible();
    client.clear();
  });
});

describe("research pack workflows", () => {
  it("retries failed research from the task center with its original local filters", async () => {
    const filters = { ...EMPTY_FILTERS, path_prefix: "课程" };
    vi.spyOn(api, "jobs").mockResolvedValue([{
      job_id: "research-failed",
      repository_id: "workspace-1",
      kind: "workspace_research",
      status: "failed",
      created_at: "2026-07-16T00:00:00Z",
      result: {
        progress: {
          phase: "retrieving",
          retry_payload: { kind: "workspace_research", question: "课程证据", limit: 50, filters },
        },
      },
      error_code: "ProviderError",
      error_message: "模型暂时不可用",
    }]);
    const retry = vi.spyOn(api, "startResearch").mockResolvedValue({
      job_id: "research-retry",
      repository_id: "workspace-1",
      kind: "workspace_research",
      status: "queued",
      result: {},
      error_code: "",
      error_message: "",
    });
    const client = renderWithClient(<TaskCenter workspaceId="workspace-1" />);

    await userEvent.click(await screen.findByRole("button", { name: "任务中心" }));
    await userEvent.click(screen.getByRole("button", { name: "重试研究问题" }));
    await waitFor(() => expect(retry).toHaveBeenCalledWith("workspace-1", "课程证据", filters));
    client.clear();
  });

  it("adds multiple selected search results in one action", async () => {
    const results = [result("docx", "研究方法.docx"), result("image", "编码表.png")];
    useAppStore.setState({ workspaceId: "workspace-1", query: "研究" });
    vi.spyOn(api, "aiSettings").mockResolvedValue({ workspace_id: "workspace-1", enabled: false, provider: "deepseek", base_url: "https://api.deepseek.com", model: "model", credential_configured: false, credential_source: "none", credential_error: "", vision_enabled: false });
    vi.spyOn(api, "search").mockResolvedValue({ query: "研究", requested_mode: "local", actual_mode: "local", degradation_reason: "", answer: "", results, candidate_count: 2, duration_ms: 2 });
    const addResult = vi.fn().mockResolvedValue(undefined);
    const client = renderWithClient(<SearchWorkspace addResult={addResult} adding={false} actionError="" clearActionError={vi.fn()} />);

    await userEvent.click(screen.getByRole("button", { name: /^搜索$/ }));
    await userEvent.click(await screen.findByRole("checkbox", { name: "选择 研究方法.docx" }));
    await userEvent.click(screen.getByRole("checkbox", { name: "选择 编码表.png" }));
    await userEvent.click(screen.getByRole("button", { name: "加入资料包 (2)" }));
    await waitFor(() => expect(addResult).toHaveBeenCalledTimes(2));
    client.clear();
  });

  it("exports the research ZIP without originals by default", async () => {
    const activeTask = task();
    useAppStore.setState({ workspaceId: "workspace-1", activeTask, saveState: "saved" });
    vi.spyOn(api, "tasks").mockResolvedValue([]);
    const exportJob = {
      job_id: "export-job",
      repository_id: "workspace-1",
      kind: "task_export" as const,
      status: "succeeded" as const,
      result: {
        artifact_id: "0123456789abcdef0123456789abcdef",
        workspace_id: "workspace-1",
        file_name: "课程研究.zip",
        size_bytes: 4,
        sha256: "hash",
        created_at: "2026-07-16T00:00:00Z",
        expires_at: "2026-07-17T00:00:00Z",
        included_source_count: 0,
        skipped_source_count: 0,
        warnings: [],
      },
      error_code: "",
      error_message: "",
    };
    const startExport = vi.spyOn(api, "startTaskExport").mockResolvedValue(exportJob);
    vi.spyOn(api, "exportArtifact").mockResolvedValue(new Blob(["pack"], { type: "application/zip" }));
    const saveBlob = vi.spyOn(bridge, "saveBlobFile").mockResolvedValue(true);
    const client = renderWithClient(<TaskPacksView />);

    await userEvent.click(screen.getByRole("button", { name: "导出研究包" }));
    await waitFor(() => expect(startExport).toHaveBeenCalledWith(activeTask, { citation_style: "gb-t-7714-2015", include_sources: false }));
    expect(saveBlob).toHaveBeenCalledWith("课程研究.zip", expect.any(Blob));
    client.clear();
  });

  it("offers source revalidation for changed and missing evidence", () => {
    const activeTask = task();
    activeTask.items = [
      {
        item_id: "changed-item",
        document_id: "document-1",
        content_hash: "old-hash",
        name: "Changed.pdf",
        relative_path: "Changed.pdf",
        page_number: null,
        excerpt: "old evidence",
        rationale: "",
        slot_id: "core",
        review_state: "confirmed",
        source_status: "resolved",
        freshness_status: "changed",
        position: 0,
        added_at: "2026-07-16T00:00:00Z",
      },
      {
        item_id: "missing-item",
        document_id: "document-2",
        content_hash: "missing-hash",
        name: "Missing.pdf",
        relative_path: "Missing.pdf",
        page_number: null,
        excerpt: "missing evidence",
        rationale: "",
        slot_id: "core",
        review_state: "confirmed",
        source_status: "resolved",
        freshness_status: "missing",
        position: 1,
        added_at: "2026-07-16T00:00:00Z",
      },
    ];
    useAppStore.setState({ workspaceId: "workspace-1", activeTask, saveState: "saved" });
    vi.spyOn(api, "tasks").mockResolvedValue([]);
    const client = renderWithClient(<TaskPacksView />);

    expect(screen.getByRole("button", { name: "重新核验来源" })).toBeVisible();
    expect(screen.getByText("2 条来源待复核")).toBeVisible();
    expect(screen.getByText("来源已变化")).toBeVisible();
    expect(screen.getByText("来源不可访问")).toBeVisible();
    client.clear();
  });
});
