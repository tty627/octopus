import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, describe, expect, it, vi } from "vitest";
import { api, apiErrorMessage } from "./api";
import * as bridge from "./bridge";
import { Onboarding } from "./components/Onboarding";
import { RepositoriesView } from "./components/RepositoriesView";
import { useAppStore } from "./store";
import type { ServiceJob, Workspace, WorkspaceDocument } from "./types";
import {
  isActiveWorkspaceJob,
  latestWorkspaceJob,
  workspaceJobProgressText,
  workspaceOptionLabels,
  workspaceSyncStatusText,
} from "./workspaceUi";

function workspace(
  workspaceId = "workspace-1",
  name = "AAA",
  rawPath = "C:\\Users\\TTY\\Downloads\\AAA",
): Workspace {
  return {
    workspace_id: workspaceId,
    name,
    raw_path: rawPath,
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
      last_sync_at: "",
    },
  };
}

function serviceJob(
  status: ServiceJob["status"] = "running",
  overrides: Partial<ServiceJob> = {},
): ServiceJob {
  return {
    job_id: "job-1",
    repository_id: "workspace-1",
    kind: "workspace_sync",
    status,
    created_at: "2026-07-16T00:00:00Z",
    result: {},
    error_code: "",
    error_message: "",
    ...overrides,
  };
}

function failedDocument(): WorkspaceDocument {
  return {
    document_id: "document-failed",
    name: "broken.pdf",
    relative_path: "imports/broken.pdf",
    extension: ".pdf",
    content_hash: "failed-hash",
    size_bytes: 42,
    modified_at: "2026-07-16T00:00:00Z",
    title: "broken",
    overview: "",
    page_count: 0,
    readability: "low",
    readability_score: 0,
    indexing_state: "failed",
    error: "C:\\Internal\\workspace.sqlite3 is locked",
    source_uri: "file:///C:/Raw/broken.pdf",
  };
}

function renderWithQueryClient(ui: React.ReactNode): QueryClient {
  const queryClient = new QueryClient({
    defaultOptions: {
      queries: { retry: false },
      mutations: { retry: false },
    },
  });
  render(<QueryClientProvider client={queryClient}>{ui}</QueryClientProvider>);
  return queryClient;
}

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  useAppStore.setState({ workspaceId: "" });
});

describe("workspace switcher labels", () => {
  it("adds only the shortest distinguishing path when names duplicate", () => {
    const labels = workspaceOptionLabels([
      workspace("outer", "AAA", "C:\\Users\\TTY\\Downloads\\AAA"),
      workspace("inner", "AAA", "C:\\Users\\TTY\\Downloads\\AAA\\AAA"),
      workspace("math", "高数", "C:\\Users\\TTY\\Documents\\高数"),
    ]);

    expect(labels.get("outer")).toBe("AAA - Downloads\\AAA");
    expect(labels.get("inner")).toBe("AAA - AAA\\AAA");
    expect(labels.get("math")).toBe("高数");
  });

  it("does not claim a new workspace is already synced", () => {
    expect(workspaceSyncStatusText(workspace())).toBe("尚未同步");
    expect(workspaceSyncStatusText({ ...workspace(), available: false })).toBe("原始资料不可访问");
    expect(workspaceSyncStatusText({
      ...workspace(),
      health: { ...workspace().health, last_sync_at: "2026-07-16T00:00:00Z" },
    })).toMatch(/^已同步 /);
  });

  it("uses workspace ids when legacy duplicates share the same path", () => {
    const labels = workspaceOptionLabels([
      workspace("workspace-outer-1", "AAA", "C:\\Users\\TTY\\Downloads\\AAA"),
      workspace("workspace-outer-2", "AAA", "C:\\Users\\TTY\\Downloads\\AAA"),
    ]);

    expect(labels.get("workspace-outer-1")).not.toBe(labels.get("workspace-outer-2"));
    expect(labels.get("workspace-outer-1")).toContain("-outer-1");
    expect(labels.get("workspace-outer-2")).toContain("-outer-2");
  });
});

describe("workspace background jobs", () => {
  it("selects the latest sync job and renders useful progress", () => {
    const older = serviceJob("failed", {
      job_id: "older",
      created_at: "2026-07-15T00:00:00Z",
    });
    const running = serviceJob("running", {
      job_id: "running",
      created_at: "2026-07-16T00:00:00Z",
      result: {
        progress: {
          phase: "processing",
          discovered: 16,
          processed: 3,
          current_file: "notes.pdf",
        },
      },
    });

    expect(latestWorkspaceJob([older, running])).toBe(running);
    expect(isActiveWorkspaceJob(running)).toBe(true);
    expect(workspaceJobProgressText(running)).toContain("3/16");
    expect(workspaceJobProgressText(running)).toContain("notes.pdf");
  });

  it("renders page-level OCR progress while keeping legacy jobs compatible", () => {
    const ocr = serviceJob("running", {
      result: {
        progress: {
          phase: "processing",
          current_file: "scan.pdf",
          current_page: 12,
          page_count: 163,
          pages_completed: 11,
          ocr_pages_completed: 4,
          extraction_stage: "ocr",
        },
      },
    });

    expect(workspaceJobProgressText(ocr)).toBe("正在 OCR 第 12/163 页：scan.pdf");
    expect(workspaceJobProgressText(serviceJob("running", { result: { progress: {} } }))).toBe(
      "正在后台处理资料",
    );
  });

  it("does not let an older failed job override a newer success", () => {
    const failed = serviceJob("failed", {
      job_id: "failed",
      created_at: "2026-07-15T00:00:00Z",
    });
    const succeeded = serviceJob("succeeded", {
      job_id: "succeeded",
      created_at: "2026-07-16T00:00:00Z",
    });

    expect(latestWorkspaceJob([failed, succeeded])?.status).toBe("succeeded");
  });

  it("starts manual sync without waiting for the job to finish", async () => {
    useAppStore.setState({ workspaceId: "workspace-1" });
    const running = serviceJob("running");
    let started = false;
    vi.spyOn(api, "documents").mockResolvedValue([]);
    vi.spyOn(api, "jobs").mockImplementation(() => Promise.resolve(started ? [running] : []));
    vi.spyOn(api, "syncWorkspace").mockImplementation(() => {
      started = true;
      return Promise.resolve(running);
    });
    const jobLookup = vi.spyOn(api, "job");
    const queryClient = renderWithQueryClient(<RepositoriesView workspace={workspace()} />);

    await userEvent.click(await screen.findByRole("button", { name: "同步" }));

    await waitFor(() => expect(screen.getByRole("button", { name: "处理中" })).toBeDisabled());
    expect(jobLookup).not.toHaveBeenCalled();
    queryClient.clear();
  });

  it("shows an actual failed job without exposing its internal error detail", async () => {
    useAppStore.setState({ workspaceId: "workspace-1" });
    vi.spyOn(api, "documents").mockResolvedValue([]);
    vi.spyOn(api, "jobs").mockResolvedValue([
      serviceJob("failed", { error_message: "C:\\Internal\\workspace.sqlite3 is locked" }),
    ]);
    const queryClient = renderWithQueryClient(<RepositoriesView workspace={workspace()} />);

    expect(await screen.findByText("后台处理失败。请检查原始资料是否可访问，然后重试。")).toBeVisible();
    expect(screen.queryByText(/workspace\.sqlite3/)).not.toBeInTheDocument();
    queryClient.clear();
  });

  it("warns when a completed sync contains file-level failures", async () => {
    useAppStore.setState({ workspaceId: "workspace-1" });
    vi.spyOn(api, "documents").mockResolvedValue([]);
    vi.spyOn(api, "jobs").mockResolvedValue([
      serviceJob("succeeded", {
        result: { progress: { phase: "completed", discovered: 16, processed: 16, failed: 2 } },
      }),
    ]);
    const queryClient = renderWithQueryClient(<RepositoriesView workspace={workspace()} />);

    expect(await screen.findByText("后台处理已完成，其中 2 个文件处理失败。可在下方重新处理。")).toBeVisible();
    queryClient.clear();
  });

  it("keeps persisted document failures visible when job history is empty", async () => {
    useAppStore.setState({ workspaceId: "workspace-1" });
    const persisted = workspace();
    persisted.health.failed_count = 1;
    vi.spyOn(api, "documents").mockResolvedValue([failedDocument()]);
    vi.spyOn(api, "jobs").mockResolvedValue([]);
    const queryClient = renderWithQueryClient(<RepositoriesView workspace={persisted} />);

    expect(await screen.findByText("当前有 1 个文件处理失败。可在下方重新处理。")).toBeVisible();
    const table = screen.getByRole("region", { name: "文档处理状态" });
    expect(await within(table).findByText("处理失败")).toBeVisible();
    expect(screen.queryByText(/workspace\.sqlite3/)).not.toBeInTheDocument();
    queryClient.clear();
  });
});

describe("workspace creation", () => {
  it("explains overlapping source folders in human language", () => {
    expect(apiErrorMessage(422, '{"detail":"Path overlaps existing workspace AAA"}')).toBe(
      "所选文件夹与已有资料空间范围重叠，请选择不重叠的文件夹。",
    );
  });

  it("opens the returned workspace while its initial job is still running", async () => {
    const created = workspace();
    const running = serviceJob("running");
    vi.spyOn(bridge, "chooseDirectory").mockResolvedValue(created.raw_path);
    vi.spyOn(api, "createWorkspace").mockResolvedValue({ workspace: created, job: running });
    const workspaceLookup = vi.spyOn(api, "workspace");
    const onCreated = vi.fn();
    const queryClient = renderWithQueryClient(<Onboarding onCreated={onCreated} />);

    await userEvent.click(screen.getByRole("button", { name: "选择" }));
    await userEvent.click(screen.getByRole("button", { name: "建立资料空间" }));

    await waitFor(() => expect(onCreated).toHaveBeenCalledWith(created, running));
    expect(workspaceLookup).not.toHaveBeenCalled();
    queryClient.clear();
  });
});
