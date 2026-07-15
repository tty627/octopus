import { afterEach, describe, expect, it, vi } from "vitest";
import { hasNativeBootstrap } from "./bridge";
import { mockRequest } from "./mockApi";
import { mergeTaskSave, useAppStore } from "./store";
import { appendEvidence } from "./taskPackActions";
import type { SearchReportV2, SearchResultV2, WorkspaceTask } from "./types";

const task = (): WorkspaceTask => ({
  schema_version: "2.0",
  task_id: "task-1",
  workspace_id: "workspace-1",
  revision: 1,
  lifecycle: "draft",
  title: "Review",
  goal: "Review evidence",
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
});

describe("desktop bridge readiness", () => {
  it("waits for the native bootstrap method", () => {
    expect(hasNativeBootstrap({})).toBe(false);
    expect(hasNativeBootstrap({ bootstrap: vi.fn() })).toBe(true);
  });
});

describe("V2 task evidence behavior", () => {
  it("puts low-quality evidence in pending", () => {
    const updated = appendEvidence(
      task(),
      result({ readability: "low", readability_score: 0.3 }),
    );
    expect(updated.items[0]).toMatchObject({ slot_id: "pending", review_state: "pending" });
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
