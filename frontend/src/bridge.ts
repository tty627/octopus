import type { BootstrapPayload, PageId, SavedExportFile } from "./types";

interface NativeBridge {
  bootstrap: () => Promise<BootstrapPayload>;
  choose_directory: () => Promise<string>;
  save_text_file: (
    suggestedName: string,
    content: string,
  ) => Promise<{ saved: boolean; file?: string }>;
  save_export_file: (
    workspaceId: string,
    artifactId: string,
    suggestedName: string,
  ) => Promise<SavedExportFile>;
  reveal_saved_file: (artifactId: string) => Promise<{ opened: boolean; file?: string }>;
  open_uri: (uri: string) => Promise<{ opened: boolean; name: string }>;
  load_ui_state: () => Promise<Record<string, unknown>>;
  save_ui_state: (state: Record<string, unknown>) => Promise<{ saved: boolean }>;
}

declare global {
  interface Window {
    pywebview?: { api?: NativeBridge };
  }
}

export function hasNativeBootstrap(value: unknown): value is NativeBridge {
  return typeof value === "object" && value !== null &&
    typeof (value as Partial<NativeBridge>).bootstrap === "function";
}

function nativeApi(): NativeBridge | undefined {
  const api = window.pywebview?.api;
  return hasNativeBootstrap(api) ? api : undefined;
}

async function waitForNativeApi(timeoutMs = 4_000): Promise<NativeBridge | undefined> {
  const deadline = performance.now() + timeoutMs;
  while (performance.now() < deadline) {
    const api = nativeApi();
    if (api) return api;
    await new Promise((resolve) => window.setTimeout(resolve, 50));
  }
  return nativeApi();
}

export async function bootstrapDesktop(): Promise<BootstrapPayload> {
  if ((import.meta.env.DEV || import.meta.env.MODE === "test") && !window.pywebview) {
    return {
      base_url: "mock://octopus",
      token: "development-memory-token",
      product_version: "2.1.0.dev1",
      platform: "browser-demo",
    };
  }
  const api = await waitForNativeApi();
  if (api) return await api.bootstrap();
  if (import.meta.env.DEV || import.meta.env.MODE === "test") {
    return {
      base_url: "mock://octopus",
      token: "development-memory-token",
      product_version: "2.1.0.dev1",
      platform: "browser-demo",
    };
  }
  throw new Error("无法连接 Octopus 本地服务。原始文件未受到影响，请重新启动应用。 ");
}

export async function chooseDirectory(): Promise<string> {
  const api = nativeApi();
  return api ? await api.choose_directory() : "C:\\Users\\Demo\\Documents\\Research";
}

export async function saveTextFile(name: string, content: string): Promise<boolean> {
  const api = nativeApi();
  if (api) return (await api.save_text_file(name, content)).saved;
  const url = URL.createObjectURL(new Blob([content], { type: "text/markdown;charset=utf-8" }));
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = name;
  anchor.click();
  URL.revokeObjectURL(url);
  return true;
}

export function saveBlobFile(name: string, content: Blob): Promise<boolean> {
  const url = URL.createObjectURL(content);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = name;
  anchor.click();
  window.setTimeout(() => URL.revokeObjectURL(url), 0);
  return Promise.resolve(true);
}

export async function saveExportFile(
  workspaceId: string,
  artifactId: string,
  suggestedName: string,
): Promise<SavedExportFile | null> {
  const api = nativeApi();
  if (!api?.save_export_file) return null;
  return api.save_export_file(workspaceId, artifactId, suggestedName);
}

export async function revealSavedFile(artifactId: string): Promise<boolean> {
  const api = nativeApi();
  if (!api?.reveal_saved_file) return false;
  return (await api.reveal_saved_file(artifactId)).opened;
}

export async function openLocalUri(uri: string): Promise<void> {
  const api = nativeApi();
  if (api) {
    await api.open_uri(uri);
    return;
  }
  window.open(uri, "_blank", "noopener,noreferrer");
}

export async function loadWindowState(): Promise<{
  page?: PageId;
  workspace_id?: string;
  task_id?: string;
  repository_id?: string;
  task_pack_id?: string;
}> {
  const api = nativeApi();
  return api ? await api.load_ui_state() : {};
}

export function saveWindowState(state: Record<string, unknown>): void {
  const api = nativeApi();
  if (api) void api.save_ui_state(state);
}
