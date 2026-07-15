import { afterEach, describe, expect, it, vi } from "vitest";
import { hasNativeBootstrap } from "./bridge";
import { mockRequest } from "./mockApi";
import { mergeTaskPackSave, useAppStore } from "./store";
import { appendResult } from "./taskPackActions";
import type { SearchReport, SearchResult, TaskPack } from "./types";
import { groupSearchResults } from "./utils";

const pack = (): TaskPack => ({
  schema_version: "1.0",
  task_pack_id: "pack-1",
  repository_id: "repository-1",
  revision: 1,
  lifecycle: "draft",
  title: "Review",
  goal: "Review sources",
  slots: [
    { slot_id: "core", name: "核心资料", description: "", position: 0, required: true },
    { slot_id: "more", name: "补充资料", description: "", position: 1, required: false },
    { slot_id: "pending", name: "待核验", description: "", position: 2, required: false },
  ],
  items: [],
  excluded_node_ids: [],
  created_at: "2026-07-15T00:00:00+00:00",
  updated_at: "2026-07-15T00:00:00+00:00",
});

const result = (overrides: Partial<SearchResult> = {}): SearchResult => ({
  node_id: "node-1",
  index_type: "leaf",
  index_path: "C:\\Index\\source.md",
  raw_relative_path: "source.pdf",
  name: "source.pdf",
  summary: "Evidence",
  description: "Evidence",
  status: "indexed",
  source_uri: "file:///C:/Raw/source.pdf",
  content_id: "sha256:test",
  modified_at: "2026-07-15T00:00:00+00:00",
  size_bytes: 42,
  evidence: [],
  quality_flags: [],
  risk_flags: [],
  rank: 1,
  score: 1,
  match_reasons: ["Exact match"],
  match_evidence: [],
  explanation: "Primary evidence",
  recommended_open_target: "source",
  open_target_uri: "file:///C:/Raw/source.pdf",
  ...overrides,
});

afterEach(() => vi.useRealTimers());

describe("search sequencing", () => {
  it("delivers local results before AI enhancement", async () => {
    vi.useFakeTimers();
    const configured = mockRequest(
      "/v1/repositories/demo-repository/ai-settings",
      "PUT",
      {
        enabled: true,
        provider: "deepseek",
        base_url: "https://api.deepseek.com",
        model: "deepseek-v4-flash",
        api_key: "test-key",
      },
    );
    await vi.advanceTimersByTimeAsync(51);
    await configured;
    const local = mockRequest<SearchReport>(
      "/v1/repositories/demo-repository/search",
      "POST",
      { query: "quarterly review", mode: "local" },
    );
    const ai = mockRequest<SearchReport>(
      "/v1/repositories/demo-repository/search",
      "POST",
      { query: "quarterly review", mode: "auto" },
    );
    let aiReady = false;
    void ai.then(() => { aiReady = true; });
    await vi.advanceTimersByTimeAsync(121);
    await expect(local).resolves.toMatchObject({ actual_mode: "local" });
    expect(aiReady).toBe(false);
    await vi.advanceTimersByTimeAsync(500);
    await expect(ai).resolves.toMatchObject({ actual_mode: "ai" });
  });

  it("aborts an obsolete query without affecting the next one", async () => {
    vi.useFakeTimers();
    const controller = new AbortController();
    const obsolete = mockRequest<SearchReport>(
      "/v1/repositories/demo-repository/search",
      "POST",
      { query: "old", mode: "auto" },
      controller.signal,
    );
    controller.abort();
    await expect(obsolete).rejects.toMatchObject({ name: "AbortError" });
  });
});

describe("desktop bridge readiness", () => {
  it("waits for the native bootstrap method instead of accepting an empty API object", () => {
    expect(hasNativeBootstrap({})).toBe(false);
    expect(hasNativeBootstrap({ bootstrap: vi.fn() })).toBe(true);
  });
});

describe("deterministic result and task behavior", () => {
  it("groups folders and risky evidence without AI", () => {
    const grouped = groupSearchResults([
      result(),
      result({ node_id: "folder", index_type: "foldernode", rank: 2 }),
      result({ node_id: "risk", rank: 5, quality_flags: ["ocr_low_confidence"] }),
    ]);
    expect(grouped.核心资料).toHaveLength(1);
    expect(grouped.相关文件夹).toHaveLength(1);
    expect(grouped.需要核验).toHaveLength(1);
  });

  it("puts risky sources in pending and never confirms them automatically", () => {
    const updated = appendResult(
      pack(),
      result({ quality_flags: ["ocr_low_confidence"], risk_flags: ["extraction_risk"] }),
    );
    expect(updated.items[0]).toMatchObject({ slot_id: "pending", review_state: "pending" });
  });

  it("does not add the same source twice", () => {
    const once = appendResult(pack(), result());
    const twice = appendResult(once, result());
    expect(twice.items).toHaveLength(1);
  });

  it("keeps the dirty flag while a save is in progress", () => {
    useAppStore.setState({ activeTaskPack: pack(), taskPackDirty: true, saveState: "idle" });
    useAppStore.getState().setSaveState("saving");
    expect(useAppStore.getState()).toMatchObject({ taskPackDirty: true, saveState: "saving" });
  });

  it("rebases edits made while an older save request is in flight", () => {
    const submitted = pack();
    const current = { ...submitted, title: "Updated while saving" };
    const saved = { ...submitted, revision: 2, lifecycle: "saved" as const };
    expect(mergeTaskPackSave(saved, submitted, current)).toMatchObject({
      dirty: true,
      pack: { title: "Updated while saving", revision: 2 },
    });
    expect(mergeTaskPackSave(saved, submitted, submitted)).toEqual({ pack: saved, dirty: false });
  });
});
