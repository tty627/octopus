import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, describe, expect, it, vi } from "vitest";
import App from "./App";
import { ApiError, api } from "./api";
import * as bridge from "./bridge";
import { AISettingsView } from "./components/AISettingsView";
import { EvidenceInspector } from "./components/EvidenceInspector";
import { RepositoriesView } from "./components/RepositoriesView";
import { SearchWorkspace } from "./components/SearchWorkspace";
import { TaskPacksView } from "./components/TaskPacksView";
import { loadLocalDraft, saveLocalDraft, useAppStore } from "./store";
import { persistActiveTask } from "./taskPersistence";
import { appendEvidence, useTaskActions } from "./taskPackActions";
import type {
  AISettingsV2,
  SearchReportV2,
  SearchResultV2,
  Workspace,
  WorkspaceDocument,
  WorkspaceTask,
  WorkspaceTaskSummary,
} from "./types";

function queryClient(): QueryClient {
  return new QueryClient({
    defaultOptions: {
      queries: { retry: false },
      mutations: { retry: false },
    },
  });
}

function renderWithClient(ui: React.ReactNode): QueryClient {
  const client = queryClient();
  render(<QueryClientProvider client={client}>{ui}</QueryClientProvider>);
  return client;
}

function workspace(workspaceId = "workspace-1", name = "资料一"): Workspace {
  return {
    workspace_id: workspaceId,
    name,
    raw_path: `C:\\资料\\${name}`,
    available: true,
    enabled: true,
    vision_enabled: false,
    legacy_index_present: false,
    health: {
      document_count: 1,
      readable_count: 1,
      partial_count: 0,
      low_quality_count: 0,
      metadata_only_count: 0,
      failed_count: 0,
      last_sync_at: "2026-07-16T00:00:00Z",
    },
  };
}

function task(overrides: Partial<WorkspaceTask> = {}): WorkspaceTask {
  return {
    schema_version: "2.0",
    task_id: "task-1",
    workspace_id: "workspace-1",
    revision: 1,
    lifecycle: "draft",
    title: "核对任务",
    goal: "",
    slots: [
      { slot_id: "core", name: "核心证据", description: "", position: 0, required: true },
      { slot_id: "pending", name: "待核验", description: "", position: 1, required: false },
    ],
    items: [],
    created_at: "2026-07-16T00:00:00Z",
    updated_at: "2026-07-16T00:00:00Z",
    migrated_from_v1: false,
    ...overrides,
  };
}

function taskSummary(value: WorkspaceTask): WorkspaceTaskSummary {
  return {
    schema_version: value.schema_version,
    task_id: value.task_id,
    workspace_id: value.workspace_id,
    revision: value.revision,
    lifecycle: value.lifecycle,
    title: value.title,
    goal: value.goal,
    item_count: value.items.length,
    pending_count: value.items.filter((item) => item.review_state === "pending").length,
    unresolved_count: value.items.filter((item) => item.source_status === "source_unconfirmed").length,
    updated_at: value.updated_at,
    writable: true,
  };
}

function result(overrides: Partial<SearchResultV2> = {}): SearchResultV2 {
  return {
    document_id: "document-1",
    name: "证据.pdf",
    relative_path: "章节/证据.pdf",
    extension: ".pdf",
    content_hash: "hash-1",
    size_bytes: 42,
    modified_at: "2026-07-16T00:00:00Z",
    page_count: 8,
    readability: "readable",
    readability_score: 0.9,
    indexing_state: "indexed",
    source_uri: "file:///C:/资料/证据.pdf",
    overview: "",
    best_evidence: {
      page_number: 3,
      heading: "第三章",
      excerpt: "最佳证据内容",
      reason: "正文包含查询内容",
      quality_score: 0.9,
    },
    additional_evidence: [{
      page_number: 5,
      heading: "第五章",
      excerpt: "补充证据内容",
      reason: "另一处正文命中",
      quality_score: 0.88,
    }],
    rank: 1,
    ...overrides,
  };
}

const aiSettings = (overrides: Partial<AISettingsV2> = {}): AISettingsV2 => ({
  workspace_id: "workspace-1",
  enabled: false,
  provider: "deepseek",
  base_url: "https://api.deepseek.com",
  model: "deepseek-chat",
  credential_configured: false,
  credential_source: "none",
  credential_error: "",
  vision_enabled: false,
  ...overrides,
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  window.localStorage.clear();
  useAppStore.setState({
    page: "search",
    workspaceId: "",
    inspector: null,
    inspectorOpen: false,
    activeTask: null,
    taskDirty: false,
    saveState: "idle",
    query: "",
    submittedQuery: "",
    filters: { path_prefix: "", extensions: [] },
    assistedEnabled: false,
  });
});

describe("task resilience", () => {
  it("writes every dirty edit to a local draft before navigation", () => {
    useAppStore.setState({ workspaceId: "workspace-1", activeTask: task(), taskDirty: false });
    useAppStore.getState().updateTask((current) => ({ ...current, title: "刚刚编辑的标题" }));

    const stored = window.localStorage.getItem("octopus:v2-task-draft:workspace-1:task-1");
    expect(JSON.parse(stored ?? "{}") as WorkspaceTask).toMatchObject({ title: "刚刚编辑的标题" });

    useAppStore.getState().setWorkspaceId("workspace-2");
    expect(window.localStorage.getItem("octopus:v2-task-draft:workspace-1:task-1")).toBe(stored);
  });

  it("serializes rapid evidence additions and creates only one task", async () => {
    useAppStore.setState({ workspaceId: "workspace-1", query: "测试" });
    let resolveCreate: ((value: WorkspaceTask) => void) | undefined;
    vi.spyOn(api, "createTask").mockImplementation(() => new Promise((resolve) => { resolveCreate = resolve; }));

    function Harness() {
      const actions = useTaskActions();
      const first = result();
      const second = result({ document_id: "document-2", name: "补充.txt", extension: ".txt", content_hash: "hash-2" });
      return <><button onClick={() => void actions.addResult(first)}>加一</button><button onClick={() => void actions.addResult(second)}>加二</button>{actions.actionError && <div role="alert">{actions.actionError}</div>}</>;
    }

    const client = renderWithClient(<Harness />);
    fireEvent.click(screen.getByRole("button", { name: "加一" }));
    fireEvent.click(screen.getByRole("button", { name: "加二" }));
    await waitFor(() => expect(api.createTask).toHaveBeenCalledTimes(1));
    resolveCreate?.(task());

    await waitFor(() => expect(useAppStore.getState().activeTask?.items).toHaveLength(2));
    expect(api.createTask).toHaveBeenCalledTimes(1);
    client.clear();
  });

  it("creates a task from the submitted query instead of the edited draft", async () => {
    useAppStore.setState({
      workspaceId: "workspace-1",
      query: "尚未提交的新查询",
      submittedQuery: "已提交查询",
    });
    vi.spyOn(api, "createTask").mockResolvedValue(task({ title: "已提交查询", goal: "已提交查询" }));

    function Harness() {
      const actions = useTaskActions();
      return <button onClick={() => void actions.addResult(result())}>加入</button>;
    }

    const client = renderWithClient(<Harness />);
    await userEvent.click(screen.getByRole("button", { name: "加入" }));

    await waitFor(() => expect(api.createTask).toHaveBeenCalledWith(
      "workspace-1",
      "已提交查询",
      "已提交查询",
    ));
    client.clear();
  });

  it("does not clear unrelated dirty edits when evidence is already present", async () => {
    const existingResult = result();
    useAppStore.setState({
      workspaceId: "workspace-1",
      activeTask: appendEvidence(task({ title: "尚未保存的标题" }), existingResult),
      taskDirty: true,
      saveState: "idle",
    });

    function Harness() {
      const actions = useTaskActions();
      return <button onClick={() => void actions.addResult(existingResult)}>重复加入</button>;
    }

    const client = renderWithClient(<Harness />);
    await userEvent.click(screen.getByRole("button", { name: "重复加入" }));
    await act(async () => { await Promise.resolve(); });
    expect(useAppStore.getState()).toMatchObject({
      taskDirty: true,
      activeTask: { title: "尚未保存的标题" },
    });
    client.clear();
  });

  it("rebases a newer local draft when an in-flight save finishes after switching", async () => {
    const submitted = task({ title: "已提交版本" });
    useAppStore.setState({
      workspaceId: "workspace-1",
      activeTask: submitted,
      taskDirty: true,
      saveState: "idle",
    });
    saveLocalDraft(submitted);
    let resolveSave: ((value: WorkspaceTask) => void) | undefined;
    vi.spyOn(api, "saveTask").mockImplementation(() => new Promise((resolve) => { resolveSave = resolve; }));
    const client = queryClient();
    const saving = persistActiveTask(client, "workspace-1", "task-1");
    await waitFor(() => expect(api.saveTask).toHaveBeenCalledTimes(1));

    useAppStore.getState().updateTask((current) => ({ ...current, title: "切换前的新编辑" }));
    useAppStore.getState().setWorkspaceId("workspace-2");
    resolveSave?.(task({ revision: 2, lifecycle: "saved", title: "已提交版本" }));
    await saving;

    expect(loadLocalDraft(submitted)).toMatchObject({
      revision: 2,
      title: "切换前的新编辑",
    });
    client.clear();
  });

  it("offers explicit recovery and discard actions for a conflicting draft", async () => {
    const authoritative = task({ revision: 2, lifecycle: "saved", title: "服务器标题" });
    saveLocalDraft(task({ title: "本地标题" }));
    useAppStore.setState({
      workspaceId: "workspace-1",
      activeTask: authoritative,
      taskDirty: true,
      saveState: "conflict",
    });
    vi.spyOn(api, "tasks").mockResolvedValue([]);
    vi.spyOn(api, "task").mockResolvedValue(authoritative);
    const client = renderWithClient(<TaskPacksView />);

    expect(screen.getByRole("button", { name: "恢复本地草稿" })).toBeVisible();
    expect(screen.getByRole("button", { name: "放弃本地草稿" })).toBeVisible();
    await userEvent.click(screen.getByRole("button", { name: "恢复本地草稿" }));

    await waitFor(() => expect(useAppStore.getState()).toMatchObject({
      taskDirty: true,
      saveState: "idle",
      activeTask: { title: "本地标题", revision: 2 },
    }));
    client.clear();
  });

  it("prioritizes an unconfirmed source over a stale confirmed review state", () => {
    useAppStore.setState({
      workspaceId: "workspace-1",
      activeTask: task({
        items: [{
          item_id: "item-1",
          document_id: "document-1",
          content_hash: "hash-1",
          name: "待确认来源.pdf",
          relative_path: "待确认来源.pdf",
          page_number: 3,
          excerpt: "原证据内容",
          rationale: "",
          slot_id: "core",
          review_state: "confirmed",
          source_status: "source_unconfirmed",
          position: 0,
          added_at: "2026-07-16T00:00:00Z",
        }],
      }),
    });
    vi.spyOn(api, "tasks").mockResolvedValue([]);

    const client = renderWithClient(<TaskPacksView />);

    expect(screen.getByRole("status")).toHaveTextContent("来源待重新确认");
    expect(screen.getByText("1 条待核验")).toBeVisible();
    expect(screen.queryByText("已确认")).not.toBeInTheDocument();
    expect(screen.queryByRole("checkbox")).not.toBeInTheDocument();
    expect(document.querySelector(".confirmedDot")).not.toBeInTheDocument();
    expect(document.querySelector(".pendingDot")).toBeInTheDocument();
    client.clear();
  });

  it("saves an immediate title edit before returning to the task list", async () => {
    const original = task({ title: "旧标题" });
    let saved = original;
    useAppStore.setState({
      workspaceId: "workspace-1",
      activeTask: original,
      taskDirty: false,
      saveState: "idle",
    });
    vi.spyOn(api, "tasks").mockImplementation(() => Promise.resolve([taskSummary(saved)]));
    vi.spyOn(api, "saveTask").mockImplementation((submitted) => {
      saved = { ...submitted, revision: submitted.revision + 1, lifecycle: "saved" };
      return Promise.resolve(saved);
    });

    const client = renderWithClient(<TaskPacksView />);
    const titleInput = screen.getByRole("textbox", { name: "任务名称" });
    await userEvent.clear(titleInput);
    await userEvent.type(titleInput, "刚编辑的新标题");
    await userEvent.click(screen.getByRole("button", { name: "返回任务列表" }));

    await waitFor(() => expect(api.saveTask).toHaveBeenCalledWith(expect.objectContaining({ title: "刚编辑的新标题" })));
    expect(await screen.findByRole("button", { name: /刚编辑的新标题/ })).toBeVisible();
    expect(useAppStore.getState().activeTask).toBeNull();
    client.clear();
  });

  it("keeps the editor open when saving before return fails", async () => {
    useAppStore.setState({
      workspaceId: "workspace-1",
      activeTask: task({ title: "待保存标题" }),
      taskDirty: true,
      saveState: "idle",
    });
    vi.spyOn(api, "tasks").mockResolvedValue([]);
    vi.spyOn(api, "saveTask").mockRejectedValue(new ApiError("暂时无法保存", 503));

    const client = renderWithClient(<TaskPacksView />);
    await userEvent.click(screen.getByRole("button", { name: "返回任务列表" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("暂时无法保存");
    expect(screen.getByRole("textbox", { name: "任务名称" })).toHaveValue("待保存标题");
    expect(useAppStore.getState().activeTask?.task_id).toBe("task-1");
    client.clear();
  });

  it("does not activate a task created for a workspace that has been left", async () => {
    useAppStore.setState({ workspaceId: "workspace-1" });
    vi.spyOn(api, "tasks").mockResolvedValue([]);
    let resolveCreate: ((value: WorkspaceTask) => void) | undefined;
    vi.spyOn(api, "createTask").mockImplementation(() => new Promise((resolve) => { resolveCreate = resolve; }));

    function Harness() {
      return <TaskPacksView />;
    }

    const client = renderWithClient(<Harness />);
    await userEvent.click(await screen.findByRole("button", { name: "新建" }));
    await waitFor(() => expect(api.createTask).toHaveBeenCalledWith("workspace-1", "新的证据任务", ""));
    act(() => useAppStore.getState().setWorkspaceId("workspace-2"));
    resolveCreate?.(task({ workspace_id: "workspace-1", title: "A 空间任务" }));
    await act(async () => { await Promise.resolve(); });

    expect(useAppStore.getState()).toMatchObject({ workspaceId: "workspace-2", activeTask: null });
    client.clear();
  });

  it("does not activate a late task load response in another workspace", async () => {
    const sourceTask = task({ title: "A 待载入" });
    useAppStore.setState({ workspaceId: "workspace-1" });
    vi.spyOn(api, "tasks").mockImplementation((workspaceId) => Promise.resolve(
      workspaceId === "workspace-1" ? [taskSummary(sourceTask)] : [],
    ));
    let resolveLoad: ((value: WorkspaceTask) => void) | undefined;
    vi.spyOn(api, "task").mockImplementation(() => new Promise((resolve) => { resolveLoad = resolve; }));

    function Harness() {
      return <TaskPacksView />;
    }

    const client = renderWithClient(<Harness />);
    await userEvent.click(await screen.findByRole("button", { name: /A 待载入/ }));
    await waitFor(() => expect(api.task).toHaveBeenCalledWith("workspace-1", "task-1"));
    act(() => useAppStore.getState().setWorkspaceId("workspace-2"));
    resolveLoad?.(sourceTask);
    await act(async () => { await Promise.resolve(); });

    expect(useAppStore.getState()).toMatchObject({ workspaceId: "workspace-2", activeTask: null });
    client.clear();
  });

  it("keeps the latest task selection when responses arrive out of order", async () => {
    const first = task({ task_id: "task-a", title: "任务 A" });
    const second = task({ task_id: "task-b", title: "任务 B" });
    useAppStore.setState({ workspaceId: "workspace-1" });
    vi.spyOn(api, "tasks").mockResolvedValue([taskSummary(first), taskSummary(second)]);
    let resolveFirst: ((value: WorkspaceTask) => void) | undefined;
    let resolveSecond: ((value: WorkspaceTask) => void) | undefined;
    vi.spyOn(api, "task").mockImplementation((_workspaceId, taskId) => new Promise((resolve) => {
      if (taskId === "task-a") resolveFirst = resolve;
      else resolveSecond = resolve;
    }));

    const client = renderWithClient(<TaskPacksView />);
    await userEvent.click(await screen.findByRole("button", { name: /任务 A/ }));
    await userEvent.click(screen.getByRole("button", { name: /任务 B/ }));
    resolveSecond?.(second);
    await waitFor(() => expect(useAppStore.getState().activeTask?.task_id).toBe("task-b"));
    resolveFirst?.(first);
    await act(async () => { await Promise.resolve(); });

    expect(useAppStore.getState().activeTask).toMatchObject({ task_id: "task-b", title: "任务 B" });
    client.clear();
  });

  it("does not show an obsolete create failure after a newer task opens", async () => {
    const existing = task({ task_id: "task-existing", title: "现有任务" });
    useAppStore.setState({ workspaceId: "workspace-1" });
    vi.spyOn(api, "tasks").mockResolvedValue([taskSummary(existing)]);
    let rejectCreate: ((reason: unknown) => void) | undefined;
    vi.spyOn(api, "createTask").mockImplementation(() => new Promise((_, reject) => {
      rejectCreate = reject;
    }));
    vi.spyOn(api, "task").mockResolvedValue(existing);
    const client = renderWithClient(<TaskPacksView />);

    await userEvent.click(await screen.findByRole("button", { name: "新建" }));
    await waitFor(() => expect(api.createTask).toHaveBeenCalledTimes(1));
    await userEvent.click(screen.getByRole("button", { name: /现有任务/ }));
    await waitFor(() => expect(useAppStore.getState().activeTask?.task_id).toBe("task-existing"));
    rejectCreate?.(new ApiError("旧创建失败", 500));
    await act(async () => { await Promise.resolve(); });

    expect(useAppStore.getState().activeTask?.title).toBe("现有任务");
    expect(screen.queryByText("旧创建失败")).not.toBeInTheDocument();
    client.clear();
  });

  it("does not show an obsolete load failure after a newer task opens", async () => {
    const oldTask = task({ task_id: "task-old", title: "旧请求" });
    const currentTask = task({ task_id: "task-current", title: "当前任务" });
    useAppStore.setState({ workspaceId: "workspace-1" });
    vi.spyOn(api, "tasks").mockResolvedValue([
      taskSummary(oldTask),
      taskSummary(currentTask),
    ]);
    let rejectOldLoad: ((reason: unknown) => void) | undefined;
    vi.spyOn(api, "task").mockImplementation((_, taskId) => {
      if (taskId === "task-old") {
        return new Promise((_, reject) => { rejectOldLoad = reject; });
      }
      return Promise.resolve(currentTask);
    });
    const client = renderWithClient(<TaskPacksView />);

    await userEvent.click(await screen.findByRole("button", { name: /旧请求/ }));
    await userEvent.click(screen.getByRole("button", { name: /当前任务/ }));
    await waitFor(() => expect(useAppStore.getState().activeTask?.task_id).toBe("task-current"));
    rejectOldLoad?.(new ApiError("旧载入失败", 500));
    await act(async () => { await Promise.resolve(); });

    expect(useAppStore.getState().activeTask?.title).toBe("当前任务");
    expect(screen.queryByText("旧载入失败")).not.toBeInTheDocument();
    client.clear();
  });

  it.each(["恢复本地草稿", "放弃本地草稿"])(
    "does not let a late %s response replace the current workspace task",
    async (actionName) => {
      const sourceTask = task({ revision: 2, lifecycle: "saved", title: "A 冲突任务" });
      const destinationTask = task({
        workspace_id: "workspace-2",
        task_id: "task-b",
        title: "B 当前任务",
      });
      saveLocalDraft(task({ title: "A 本地草稿" }));
      useAppStore.setState({
        workspaceId: "workspace-1",
        activeTask: sourceTask,
        taskDirty: true,
        saveState: "conflict",
      });
      vi.spyOn(api, "tasks").mockResolvedValue([]);
      let resolveLoad: ((value: WorkspaceTask) => void) | undefined;
      vi.spyOn(api, "task").mockImplementation(() => new Promise((resolve) => { resolveLoad = resolve; }));

      function Harness() {
        return <TaskPacksView />;
      }

      const client = renderWithClient(<Harness />);
      await userEvent.click(screen.getByRole("button", { name: actionName }));
      act(() => {
        useAppStore.getState().setWorkspaceId("workspace-2");
        useAppStore.getState().setTask(destinationTask);
      });
      resolveLoad?.(sourceTask);
      await act(async () => { await Promise.resolve(); });

      expect(useAppStore.getState().activeTask).toMatchObject({
        workspace_id: "workspace-2",
        task_id: "task-b",
        title: "B 当前任务",
      });
      client.clear();
    },
  );
});

describe("workspace-bound evidence", () => {
  it("keeps highlights and task defaults bound to the submitted query", async () => {
    const searched = result({
      best_evidence: {
        page_number: 3,
        heading: "第三章",
        excerpt: "级数的收敛判别",
        reason: "正文包含查询内容",
        quality_score: 0.9,
      },
      additional_evidence: [],
    });
    useAppStore.setState({ workspaceId: "workspace-1", query: "级数" });
    vi.spyOn(api, "aiSettings").mockResolvedValue(aiSettings());
    vi.spyOn(api, "search").mockResolvedValue({
      query: "级数",
      requested_mode: "local",
      actual_mode: "local",
      degradation_reason: "",
      answer: "",
      results: [searched],
      candidate_count: 1,
      duration_ms: 4,
    });
    vi.spyOn(api, "previewUrl").mockResolvedValue("data:image/png;base64,preview");
    const addResult = vi.fn().mockResolvedValue(undefined);
    const client = renderWithClient(<>
      <SearchWorkspace addResult={addResult} adding={false} actionError="" clearActionError={vi.fn()} />
      <EvidenceInspector onAdd={vi.fn()} adding={false} actionError="" />
    </>);

    await userEvent.click(screen.getByRole("button", { name: /^搜索$/ }));
    expect(await screen.findByText("级数", { selector: "mark" })).toBeVisible();
    await waitFor(() => expect(api.previewUrl).toHaveBeenCalledWith(
      "workspace-1",
      "document-1",
      3,
      "级数",
    ));
    await userEvent.clear(screen.getByRole("textbox", { name: "搜索原始资料" }));
    await userEvent.type(screen.getByRole("textbox", { name: "搜索原始资料" }), "微分方程");
    expect(screen.getByText("级数", { selector: "mark" })).toBeVisible();

    await userEvent.click(screen.getByRole("button", { name: "将 证据.pdf 加入任务" }));
    expect(addResult).toHaveBeenCalledWith(searched, searched.best_evidence, "级数", "workspace-1");
    client.clear();
  });

  it("labels metadata-only search results without claiming正文 extraction", async () => {
    const metadataResult = result({
      document_id: "office-1",
      name: "课程整理需求.docx",
      relative_path: "课程整理需求.docx",
      extension: ".docx",
      page_count: 0,
      readability: "low",
      readability_score: 0,
      indexing_state: "metadata_only",
      best_evidence: {
        page_number: null,
        heading: "",
        excerpt: "课程整理需求.docx",
        reason: "文件名与查询完全一致",
        quality_score: 0,
      },
      additional_evidence: [],
    });
    useAppStore.setState({ workspaceId: "workspace-1", query: "课程整理需求" });
    vi.spyOn(api, "aiSettings").mockResolvedValue(aiSettings());
    vi.spyOn(api, "search").mockResolvedValue({
      query: "课程整理需求",
      requested_mode: "local",
      actual_mode: "local",
      degradation_reason: "",
      answer: "",
      results: [metadataResult],
      candidate_count: 1,
      duration_ms: 4,
    });
    const client = renderWithClient(<>
      <SearchWorkspace addResult={vi.fn()} adding={false} actionError="" clearActionError={vi.fn()} />
      <EvidenceInspector onAdd={vi.fn()} adding={false} actionError="" />
    </>);

    await userEvent.click(screen.getByRole("button", { name: /^搜索$/ }));
    expect(await screen.findAllByText("仅文件信息")).toHaveLength(2);
    expect(screen.getAllByText("当前仅提供文件名、路径和元数据检索。").length).toBeGreaterThanOrEqual(2);
    expect(screen.queryByText("识别质量低")).not.toBeInTheDocument();
    expect(screen.queryByText("文本证据已从原文件中定位。")).not.toBeInTheDocument();
    client.clear();
  });

  it("drops an obsolete search response after switching workspaces", async () => {
    useAppStore.setState({ workspaceId: "workspace-1", query: "旧查询" });
    vi.spyOn(api, "aiSettings").mockResolvedValue(aiSettings());
    let resolveSearch: ((value: SearchReportV2) => void) | undefined;
    vi.spyOn(api, "search").mockImplementation(() => new Promise((resolve) => { resolveSearch = resolve; }));
    const addResult = vi.fn().mockResolvedValue(undefined);
    const client = renderWithClient(<SearchWorkspace addResult={addResult} adding={false} actionError="" clearActionError={vi.fn()} />);

    await userEvent.click(screen.getByRole("button", { name: /^搜索$/ }));
    await waitFor(() => expect(api.search).toHaveBeenCalledTimes(1));
    act(() => useAppStore.getState().setWorkspaceId("workspace-2"));
    resolveSearch?.({
      query: "旧查询",
      requested_mode: "local",
      actual_mode: "local",
      degradation_reason: "",
      answer: "",
      results: [result()],
      candidate_count: 1,
      duration_ms: 10,
    });

    await waitFor(() => expect(screen.queryByRole("button", { name: "证据.pdf" })).not.toBeInTheDocument());
    expect(useAppStore.getState().inspector).toBeNull();
    expect(addResult).not.toHaveBeenCalled();
    client.clear();
  });

  it("does not label an adjacent page with another page's evidence", async () => {
    const inspected = result();
    useAppStore.setState({
      workspaceId: "workspace-1",
      inspector: inspected,
      inspectorOpen: true,
      query: "证据",
    });
    vi.spyOn(api, "previewUrl").mockResolvedValue("data:image/png;base64,preview");
    const onAdd = vi.fn().mockResolvedValue(undefined);
    const client = renderWithClient(<EvidenceInspector onAdd={onAdd} adding={false} actionError="" />);

    await userEvent.click(screen.getByRole("button", { name: "下一页" }));
    expect(screen.getByText("当前页没有搜索命中")).toBeVisible();
    expect(screen.queryByText("最佳证据内容")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "当前页无命中" })).toBeDisabled();

    await userEvent.click(screen.getByRole("button", { name: "第 5 页" }));
    await userEvent.click(screen.getByRole("button", { name: "加入任务" }));
    expect(onAdd).toHaveBeenCalledWith(inspected, inspected.additional_evidence[0]);
    client.clear();
  });

  it("explains when the original file is no longer available", async () => {
    useAppStore.setState({
      workspaceId: "workspace-1",
      inspector: result(),
      inspectorOpen: true,
      submittedQuery: "证据",
    });
    vi.spyOn(api, "previewUrl").mockResolvedValue("data:image/png;base64,preview");
    vi.spyOn(bridge, "openLocalUri").mockRejectedValue(new Error("missing"));
    const client = renderWithClient(<EvidenceInspector onAdd={vi.fn()} adding={false} actionError="" />);

    await userEvent.click(screen.getByRole("button", { name: "打开原文件" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("原文件已不可访问，请同步后重新定位。");
    client.clear();
  });
});

describe("authoritative settings", () => {
  it("does not expose editable defaults when settings cannot be read", async () => {
    vi.spyOn(api, "aiSettings").mockRejectedValue(new ApiError("读取失败", 500));
    useAppStore.setState({ workspaceId: "workspace-1" });
    const client = renderWithClient(<AISettingsView />);

    expect(await screen.findByText("无法读取当前设置。为避免覆盖已有配置，编辑功能已暂停。")).toBeVisible();
    expect(screen.queryByLabelText("API Key")).not.toBeInTheDocument();
    client.clear();
  });

  it("explains that enabling AI requires an API key", async () => {
    vi.spyOn(api, "aiSettings").mockResolvedValue(aiSettings());
    useAppStore.setState({ workspaceId: "workspace-1" });
    const client = renderWithClient(<AISettingsView />);

    await userEvent.click(await screen.findByRole("checkbox", { name: "启用辅助整理" }));
    expect(screen.getByText("启用辅助整理前，请先填写 API Key。")).toBeVisible();
    expect(screen.getByRole("button", { name: "保存设置" })).toBeDisabled();
    client.clear();
  });

  it("refetches authoritative state when the second save stage fails", async () => {
    const authoritative = aiSettings({ credential_configured: true, credential_source: "windows_credential" });
    vi.spyOn(api, "aiSettings").mockResolvedValue(authoritative);
    vi.spyOn(api, "saveAISettings").mockResolvedValue(authoritative);
    vi.spyOn(api, "setVisionAuthorization").mockRejectedValue(new ApiError("授权保存失败", 500));
    useAppStore.setState({ workspaceId: "workspace-1" });
    const client = renderWithClient(<AISettingsView />);

    const vision = await screen.findByRole("checkbox", { name: "允许发送疑难页面图像" });
    await userEvent.click(vision);
    await userEvent.click(screen.getByRole("button", { name: "保存设置" }));

    expect(await screen.findByText(/设置未完整保存，已重新读取当前状态/)).toBeVisible();
    await waitFor(() => expect(vi.mocked(api.aiSettings).mock.calls.length).toBeGreaterThanOrEqual(2));
    await waitFor(() => expect(vision).not.toBeChecked());
    client.clear();
  });

  it("clears an unsaved API key before saving another workspace", async () => {
    useAppStore.setState({ workspaceId: "workspace-1" });
    vi.spyOn(api, "aiSettings").mockImplementation((workspaceId) => Promise.resolve(aiSettings({ workspace_id: workspaceId })));
    const saveSettings = vi.spyOn(api, "saveAISettings").mockImplementation((workspaceId) => Promise.resolve(aiSettings({ workspace_id: workspaceId })));
    vi.spyOn(api, "setVisionAuthorization").mockImplementation((workspaceId, vision_enabled) => Promise.resolve({ workspace_id: workspaceId, vision_enabled }));

    function Harness() {
      return <AISettingsView />;
    }

    const client = renderWithClient(<Harness />);
    await userEvent.type(await screen.findByLabelText("API Key"), "A-secret");
    act(() => useAppStore.getState().setWorkspaceId("workspace-2"));
    const destinationKey = await screen.findByLabelText("API Key");
    expect(destinationKey).toHaveValue("");
    await userEvent.click(screen.getByRole("button", { name: "保存设置" }));

    await waitFor(() => expect(saveSettings).toHaveBeenCalled());
    const destinationSave = saveSettings.mock.calls.find(([workspaceId]) => workspaceId === "workspace-2");
    expect(destinationSave?.[1]).not.toHaveProperty("api_key");
    client.clear();
  });

  it("ignores a late connection-test result from the previous workspace", async () => {
    useAppStore.setState({ workspaceId: "workspace-1" });
    vi.spyOn(api, "aiSettings").mockImplementation((workspaceId) => Promise.resolve(aiSettings({ workspace_id: workspaceId })));
    let resolveTest: ((value: { ok: boolean; code: string; message: string }) => void) | undefined;
    vi.spyOn(api, "testAISettings").mockImplementation(() => new Promise((resolve) => { resolveTest = resolve; }));

    function Harness() {
      return <AISettingsView />;
    }

    const client = renderWithClient(<Harness />);
    await userEvent.type(await screen.findByLabelText("API Key"), "A-secret");
    await userEvent.click(screen.getByRole("button", { name: "测试连接" }));
    act(() => useAppStore.getState().setWorkspaceId("workspace-2"));
    const destinationKey = await screen.findByLabelText("API Key");
    await userEvent.type(destinationKey, "B-secret");
    resolveTest?.({ ok: true, code: "connected", message: "A 已连接" });
    await act(async () => { await Promise.resolve(); });

    expect(destinationKey).toHaveValue("B-secret");
    expect(screen.queryByText("A 已连接")).not.toBeInTheDocument();
    client.clear();
  });

  it("ignores a late save callback from the previous workspace", async () => {
    useAppStore.setState({ workspaceId: "workspace-1" });
    vi.spyOn(api, "aiSettings").mockImplementation((workspaceId) => Promise.resolve(aiSettings({ workspace_id: workspaceId })));
    let resolveSave: ((value: AISettingsV2) => void) | undefined;
    vi.spyOn(api, "saveAISettings").mockImplementation(() => new Promise((resolve) => { resolveSave = resolve; }));
    vi.spyOn(api, "setVisionAuthorization").mockImplementation((workspaceId, vision_enabled) => Promise.resolve({ workspace_id: workspaceId, vision_enabled }));

    function Harness() {
      return <AISettingsView />;
    }

    const client = renderWithClient(<Harness />);
    await userEvent.type(await screen.findByLabelText("API Key"), "A-secret");
    await userEvent.click(screen.getByRole("button", { name: "保存设置" }));
    act(() => useAppStore.getState().setWorkspaceId("workspace-2"));
    const destinationKey = await screen.findByLabelText("API Key");
    await userEvent.type(destinationKey, "B-secret");
    resolveSave?.(aiSettings({ workspace_id: "workspace-1", credential_configured: true }));
    await act(async () => { await Promise.resolve(); });

    expect(destinationKey).toHaveValue("B-secret");
    expect(screen.queryByText("设置已保存。")).not.toBeInTheDocument();
    client.clear();
  });

  it("does not apply an old authoritative refetch after switching workspaces", async () => {
    useAppStore.setState({ workspaceId: "workspace-1" });
    let resolveAuthoritative: ((value: AISettingsV2) => void) | undefined;
    let sourceReads = 0;
    vi.spyOn(api, "aiSettings").mockImplementation((workspaceId) => {
      if (workspaceId === "workspace-2") {
        return Promise.resolve(aiSettings({ workspace_id: workspaceId, model: "model-b" }));
      }
      sourceReads += 1;
      if (sourceReads === 1) return Promise.resolve(aiSettings({ workspace_id: workspaceId, model: "model-a" }));
      return new Promise((resolve) => { resolveAuthoritative = resolve; });
    });
    vi.spyOn(api, "saveAISettings").mockResolvedValue(aiSettings({ workspace_id: "workspace-1" }));
    vi.spyOn(api, "setVisionAuthorization").mockResolvedValue({ workspace_id: "workspace-1", vision_enabled: false });

    const client = renderWithClient(<AISettingsView />);
    await screen.findByDisplayValue("model-a");
    await userEvent.click(screen.getByRole("button", { name: "保存设置" }));
    await waitFor(() => expect(sourceReads).toBe(2));
    act(() => useAppStore.getState().setWorkspaceId("workspace-2"));
    await screen.findByDisplayValue("model-b");
    await userEvent.type(screen.getByLabelText("API Key"), "B-secret");
    resolveAuthoritative?.(aiSettings({ workspace_id: "workspace-1", model: "late-model-a", vision_enabled: true }));
    await act(async () => { await Promise.resolve(); });

    expect(screen.getByDisplayValue("model-b")).toBeVisible();
    expect(screen.getByLabelText("API Key")).toHaveValue("B-secret");
    expect(screen.queryByDisplayValue("late-model-a")).not.toBeInTheDocument();
    client.clear();
  });
});

describe("document quality labels", () => {
  it("shows Office metadata-only documents as file information", async () => {
    const current = workspace();
    current.health = { ...current.health, readable_count: 0, metadata_only_count: 1 };
    const document: WorkspaceDocument = {
      document_id: "office-1",
      name: "汇总.xlsx",
      relative_path: "汇总.xlsx",
      extension: ".xlsx",
      content_hash: "office-hash",
      size_bytes: 1024,
      modified_at: "2026-07-16T00:00:00Z",
      title: "汇总",
      overview: "",
      page_count: 0,
      readability: "low",
      readability_score: 0,
      indexing_state: "metadata_only",
      error: "",
      source_uri: "file:///C:/资料/汇总.xlsx",
    };
    useAppStore.setState({ workspaceId: "workspace-1" });
    vi.spyOn(api, "documents").mockResolvedValue([document]);
    vi.spyOn(api, "jobs").mockResolvedValue([]);
    const client = renderWithClient(<RepositoriesView workspace={current} />);

    const table = screen.getByRole("region", { name: "文档处理状态" });
    expect(await within(table).findByText("仅文件信息")).toBeVisible();
    expect(within(table).queryByText("识别质量低")).not.toBeInTheDocument();
    client.clear();
  });
});

describe("window state restoration", () => {
  it("restores the requested workspace and task before persisting UI state", async () => {
    const first = workspace("workspace-1", "资料一");
    const second = workspace("workspace-2", "资料二");
    const restoredTask = task({ workspace_id: "workspace-2", task_id: "task-restored" });
    vi.spyOn(api, "workspaces").mockResolvedValue([first, second]);
    vi.spyOn(api, "task").mockResolvedValue(restoredTask);
    vi.spyOn(api, "tasks").mockResolvedValue([]);
    vi.spyOn(bridge, "loadWindowState").mockResolvedValue({ page: "tasks", workspace_id: "workspace-2", task_id: "task-restored" });
    const saveState = vi.spyOn(bridge, "saveWindowState");
    const client = renderWithClient(<App />);

    await waitFor(() => expect(saveState).toHaveBeenCalledWith({ page: "tasks", workspace_id: "workspace-2", task_id: "task-restored" }));
    expect(saveState.mock.calls.some(([state]) => state.workspace_id === "" || state.workspace_id === "workspace-1")).toBe(false);
    client.clear();
  });

  it("ignores a restored task response after the user switches workspace", async () => {
    const first = workspace("workspace-1", "资料一");
    const second = workspace("workspace-2", "资料二");
    const restoredTask = task({ workspace_id: "workspace-2", task_id: "task-restored" });
    let resolveTask: ((value: WorkspaceTask) => void) | undefined;
    vi.spyOn(api, "workspaces").mockResolvedValue([first, second]);
    vi.spyOn(api, "task").mockImplementation(() => new Promise((resolve) => { resolveTask = resolve; }));
    vi.spyOn(api, "tasks").mockResolvedValue([]);
    vi.spyOn(bridge, "loadWindowState").mockResolvedValue({ page: "tasks", workspace_id: "workspace-2", task_id: "task-restored" });
    const saveState = vi.spyOn(bridge, "saveWindowState");
    const client = renderWithClient(<App />);

    const workspaceSwitcher = await waitFor(() => {
      const element = document.querySelector<HTMLSelectElement>(".workspaceSwitcher select");
      expect(element).toHaveValue("workspace-2");
      return element as HTMLSelectElement;
    });
    fireEvent.change(workspaceSwitcher, { target: { value: "workspace-1" } });
    expect(useAppStore.getState().workspaceId).toBe("workspace-1");

    await act(async () => {
      resolveTask?.(restoredTask);
      await Promise.resolve();
    });

    await waitFor(() => expect(saveState).toHaveBeenCalledWith({ page: "tasks", workspace_id: "workspace-1", task_id: undefined }));
    expect(useAppStore.getState().activeTask).toBeNull();
    client.clear();
  });
});
