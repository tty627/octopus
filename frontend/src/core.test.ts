import { afterEach, describe, expect, it, vi } from "vitest";
import { hasNativeBootstrap } from "./bridge";
import { mockRequest } from "./mockApi";
import { mergeTaskSave, useAppStore } from "./store";
import { appendEvidence } from "./taskPackActions";
import type {
  AISettingsV2,
  SearchReportV2,
  SearchResultV2,
  ServiceJob,
  Workspace,
  WorkspaceDocument,
  WorkspaceTask,
} from "./types";

const task = (): WorkspaceTask => ({
  schema_version: "2.0",
  task_id: "task-1",
  workspace_id: "workspace-1",
  revision: 1,
  lifecycle: "draft",
  title: "Review",
  goal: "Review evidence",
  template_id: "free_research",
  slots: [
    { slot_id: "core", name: "核心证据", description: "", position: 0, required: true },
    { slot_id: "more", name: "补充证据", description: "", position: 1, required: false },
    { slot_id: "pending", name: "待核验", description: "", position: 2, required: false },
  ],
  items: [],
  created_at: "2026-07-15T00:00:00+00:00",
  updated_at: "2026-07-15T00:00:00+00:00",
  migrated_from_v1: false,
});

const result = (overrides: Partial<SearchResultV2> = {}): SearchResultV2 => ({
  document_id: "document-1",
  name: "source.pdf",
  relative_path: "chapter/source.pdf",
  extension: ".pdf",
  content_hash: "hash-1",
  size_bytes: 42,
  modified_at: "2026-07-15T00:00:00+00:00",
  page_count: 12,
  readability: "readable",
  readability_score: 0.9,
  indexing_state: "indexed",
  source_uri: "file:///C:/Raw/source.pdf",
  overview: "Evidence",
  best_evidence: {
    page_number: 3,
    heading: "第三章",
    excerpt: "Primary evidence",
    reason: "正文包含查询内容",
    quality_score: 0.9,
  },
  additional_evidence: [],
  rank: 1,
  ...overrides,
});

afterEach(() => vi.useRealTimers());

describe("V2 local search", () => {
  it("returns one result per document with a human-readable evidence reason", async () => {
    vi.useFakeTimers();
    const pending = mockRequest<SearchReportV2>(
      "/v2/workspaces/demo-workspace/search",
      "POST",
      { query: "微分方程", mode: "local" },
    );
    await vi.advanceTimersByTimeAsync(101);
    const report = await pending;
    expect(report.results.map((item) => item.document_id)).toEqual([
      "document-1",
      "document-3",
    ]);
    expect(report.results[0]?.best_evidence.reason).toContain("正文");
    expect(JSON.stringify(report.results)).not.toMatch(/exact_name|folder_child|summary_layer|index_path/);
  });

  it("aborts an obsolete query", async () => {
    vi.useFakeTimers();
    const controller = new AbortController();
    const obsolete = mockRequest<SearchReportV2>(
      "/v2/workspaces/demo-workspace/search",
      "POST",
      { query: "old", mode: "local" },
      controller.signal,
    );
    controller.abort();
    await expect(obsolete).rejects.toMatchObject({ name: "AbortError" });
  });

  it("keeps a newly created demo workspace distinct and empty", async () => {
    vi.useFakeTimers();
    const creation = mockRequest<{ workspace: Workspace; job: ServiceJob }>(
      "/v2/workspaces",
      "POST",
      { raw_path: "C:\\Users\\Demo\\Documents\\新资料", name: "新资料" },
    );
    await vi.advanceTimersByTimeAsync(36);
    const created = await creation;

    expect(created.workspace).toMatchObject({ name: "新资料", raw_path: "C:\\Users\\Demo\\Documents\\新资料" });
    expect(created.workspace.workspace_id).not.toBe("demo-workspace");
    expect(created.job.repository_id).toBe(created.workspace.workspace_id);

    await vi.advanceTimersByTimeAsync(81);
    const listed = mockRequest<Workspace[]>("/v2/workspaces", "GET");
    await vi.advanceTimersByTimeAsync(36);
    const workspaceList = await listed;
    expect(workspaceList.map((item) => item.workspace_id)).toContain(created.workspace.workspace_id);
    expect(workspaceList.find((item) => item.workspace_id === created.workspace.workspace_id)?.health.last_sync_at)
      .not.toBe("");

    const emptyDocuments = mockRequest<WorkspaceDocument[]>(
      `/v2/workspaces/${created.workspace.workspace_id}/documents`,
      "GET",
    );
    await vi.advanceTimersByTimeAsync(36);
    expect(await emptyDocuments).toEqual([]);

    const emptySearch = mockRequest<SearchReportV2>(
      `/v2/workspaces/${created.workspace.workspace_id}/search`,
      "POST",
      { query: "微分方程", mode: "local" },
    );
    await vi.advanceTimersByTimeAsync(101);
    expect((await emptySearch).results).toEqual([]);

    const emptySettings = mockRequest<AISettingsV2>(
      `/v2/workspaces/${created.workspace.workspace_id}/ai-settings`,
      "GET",
    );
    await vi.advanceTimersByTimeAsync(36);
    expect(await emptySettings).toMatchObject({
      workspace_id: created.workspace.workspace_id,
      enabled: false,
      credential_configured: false,
    });

    const scopedJob = mockRequest<ServiceJob>(
      `/v2/jobs/${created.job.job_id}?workspace_id=${encodeURIComponent(created.workspace.workspace_id)}`,
      "GET",
    );
    await vi.advanceTimersByTimeAsync(36);
    expect((await scopedJob).repository_id).toBe(created.workspace.workspace_id);

    const crossWorkspaceJob = mockRequest<ServiceJob>(
      `/v2/jobs/${created.job.job_id}?workspace_id=demo-workspace`,
      "GET",
    );
    const rejectedCrossWorkspaceJob = expect(crossWorkspaceJob).rejects.toThrow(
      "后台任务不存在",
    );
    await vi.advanceTimersByTimeAsync(36);
    await rejectedCrossWorkspaceJob;
  });
});

describe("desktop bridge readiness", () => {
  it("waits for the native bootstrap method", () => {
    expect(hasNativeBootstrap({})).toBe(false);
    expect(hasNativeBootstrap({ bootstrap: vi.fn() })).toBe(true);
  });
});

describe("V2 task evidence behavior", () => {
  it("puts manually collected free-research evidence in pending", () => {
    const updated = appendEvidence(
      task(),
      result({ readability: "low", readability_score: 0.3 }),
    );
    expect(updated.items[0]).toMatchObject({ slot_id: "pending", review_state: "pending" });
  });

  it("puts literature-review evidence in core literature instead of research gaps", () => {
    const literatureTask: WorkspaceTask = {
      ...task(),
      template_id: "literature_review",
      slots: [
        { slot_id: "background", name: "背景", description: "", position: 0, required: true },
        { slot_id: "literature", name: "核心文献", description: "", position: 1, required: true },
        { slot_id: "gaps", name: "研究缺口", description: "", position: 2, required: false },
      ],
    };

    expect(appendEvidence(literatureTask, result()).items[0]?.slot_id).toBe("literature");
  });

  it("puts course-report evidence in supporting material instead of the final slot", () => {
    const courseTask: WorkspaceTask = {
      ...task(),
      template_id: "course_report",
      slots: [
        { slot_id: "requirements", name: "题目与要求", description: "", position: 0, required: true },
        { slot_id: "material", name: "论据与材料", description: "", position: 1, required: true },
        { slot_id: "references", name: "参考资料", description: "", position: 2, required: false },
      ],
    };

    expect(appendEvidence(courseTask, result()).items[0]?.slot_id).toBe("material");
  });

  it("falls back to a general evidence slot and skips semantic sink slots", () => {
    const customTask: WorkspaceTask = {
      ...task(),
      template_id: undefined,
      slots: [
        { slot_id: "notes", name: "参考材料", description: "", position: 0, required: false },
        { slot_id: "pending", name: "待核验", description: "", position: 1, required: false },
        { slot_id: "gaps", name: "研究缺口", description: "", position: 2, required: false },
      ],
    };

    expect(appendEvidence(customTask, result()).items[0]?.slot_id).toBe("notes");
  });

  it("creates a neutral collection slot instead of misclassifying literature as a research gap", () => {
    const gapOnlyTask: WorkspaceTask = {
      ...task(),
      template_id: "literature_review",
      slots: [
        { slot_id: "gaps", name: "研究缺口", description: "", position: 0, required: false },
      ],
    };

    const updated = appendEvidence(gapOnlyTask, result());
    expect(updated.slots).toContainEqual(expect.objectContaining({ name: "收集证据" }));
    expect(updated.items[0]?.slot_id).not.toBe("gaps");
  });

  it("does not add the same page evidence twice", () => {
    const once = appendEvidence(task(), result());
    const twice = appendEvidence(once, result());
    expect(twice.items).toHaveLength(1);
  });

  it("keeps the dirty flag while a save is in progress", () => {
    useAppStore.setState({ activeTask: task(), taskDirty: true, saveState: "idle" });
    useAppStore.getState().setSaveState("saving");
    expect(useAppStore.getState()).toMatchObject({ taskDirty: true, saveState: "saving" });
  });

  it("rebases edits made while an older save is in flight", () => {
    const submitted = task();
    const current = { ...submitted, title: "Updated while saving" };
    const saved = { ...submitted, revision: 2, lifecycle: "saved" as const };
    expect(mergeTaskSave(saved, submitted, current)).toMatchObject({
      dirty: true,
      task: { title: "Updated while saving", revision: 2 },
    });
    expect(mergeTaskSave(saved, submitted, submitted)).toEqual({ task: saved, dirty: false });
  });
});
