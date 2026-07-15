import { useEffect, useRef } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  AlertTriangle,
  Library,
  ListTodo,
  Octagon,
  Search,
  Settings,
} from "lucide-react";
import { ApiError, api, runtimeBootstrap } from "./api";
import { loadWindowState, saveWindowState } from "./bridge";
import {
  clearLocalDraft,
  draftStorageKey,
  mergeTaskSave,
  saveLocalDraft,
  useAppStore,
} from "./store";
import type { PageId, WorkspaceTask } from "./types";
import { relativeTime } from "./utils";
import { AISettingsView } from "./components/AISettingsView";
import { EvidenceInspector } from "./components/EvidenceInspector";
import { Onboarding } from "./components/Onboarding";
import { RepositoriesView } from "./components/RepositoriesView";
import { SearchWorkspace } from "./components/SearchWorkspace";
import { TaskPacksView } from "./components/TaskPacksView";
import { TaskTray } from "./components/TaskTray";
import { useTaskActions } from "./taskPackActions";

const navItems: Array<{ id: PageId; label: string; icon: typeof Search }> = [
  { id: "search", label: "搜索", icon: Search },
  { id: "tasks", label: "任务", icon: ListTodo },
  { id: "documents", label: "资料", icon: Library },
  { id: "settings", label: "设置", icon: Settings },
];

export default function App() {
  const page = useAppStore((state) => state.page);
  const setPage = useAppStore((state) => state.setPage);
  const workspaceId = useAppStore((state) => state.workspaceId);
  const setWorkspaceId = useAppStore((state) => state.setWorkspaceId);
  const activeTask = useAppStore((state) => state.activeTask);
  const taskDirty = useAppStore((state) => state.taskDirty);
  const setTask = useAppStore((state) => state.setTask);
  const setSaveState = useAppStore((state) => state.setSaveState);
  const restored = useRef(false);
  const queryClient = useQueryClient();
  const runtime = useQuery({ queryKey: ["runtime"], queryFn: runtimeBootstrap, retry: 1 });
  const workspaces = useQuery({
    queryKey: ["workspaces"],
    queryFn: api.workspaces,
    enabled: runtime.isSuccess,
    retry: 2,
    refetchInterval: 20_000,
  });
  const { addResult } = useTaskActions();
  const currentWorkspace = workspaces.data?.find((item) => item.workspace_id === workspaceId);

  useEffect(() => {
    if (restored.current || !workspaces.data) return;
    restored.current = true;
    void loadWindowState().then((state) => {
      const requestedWorkspace = state.workspace_id ?? state.repository_id;
      const selected = workspaces.data.find((item) => item.workspace_id === requestedWorkspace)?.workspace_id ?? workspaces.data[0]?.workspace_id ?? "";
      setWorkspaceId(selected);
      if (state.page && navItems.some((item) => item.id === state.page)) setPage(state.page);
      const requestedTask = state.task_id ?? state.task_pack_id;
      if (requestedTask && selected) void api.task(selected, requestedTask).then((task) => setTask(task)).catch(() => undefined);
    });
  }, [setPage, setTask, setWorkspaceId, workspaces.data]);

  useEffect(() => {
    saveWindowState({ page, workspace_id: workspaceId, task_id: activeTask?.task_id });
  }, [activeTask?.task_id, page, workspaceId]);

  useEffect(() => {
    if (!activeTask || taskDirty) return;
    const stored = localStorage.getItem(draftStorageKey(activeTask.workspace_id, activeTask.task_id));
    if (!stored) return;
    try {
      const draft = JSON.parse(stored) as WorkspaceTask;
      if (draft.revision === activeTask.revision) setTask(draft, true);
      else setSaveState("conflict");
    } catch {
      localStorage.removeItem(draftStorageKey(activeTask.workspace_id, activeTask.task_id));
    }
  }, [activeTask, setSaveState, setTask, taskDirty]);

  useEffect(() => {
    if (!activeTask || !taskDirty) return;
    const submitted = activeTask;
    const timer = window.setTimeout(async () => {
      setSaveState("saving");
      try {
        const saved = await api.saveTask(submitted);
        clearLocalDraft(submitted);
        const merged = mergeTaskSave(saved, submitted, useAppStore.getState().activeTask);
        if (merged) {
          setTask(merged.task, merged.dirty);
          if (merged.dirty) saveLocalDraft(merged.task);
        }
        await queryClient.invalidateQueries({ queryKey: ["tasks", submitted.workspace_id] });
      } catch (error) {
        saveLocalDraft(submitted);
        setSaveState(error instanceof ApiError && error.status === 409 ? "conflict" : "offline");
      }
    }, 700);
    return () => window.clearTimeout(timer);
  }, [activeTask, queryClient, setSaveState, setTask, taskDirty]);

  useEffect(() => {
    const shortcut = (event: KeyboardEvent) => {
      if (event.ctrlKey && event.key.toLowerCase() === "f") {
        event.preventDefault();
        setPage("search");
        window.setTimeout(() => document.querySelector<HTMLInputElement>("#workspace-search")?.focus(), 0);
      }
    };
    window.addEventListener("keydown", shortcut);
    return () => window.removeEventListener("keydown", shortcut);
  }, [setPage]);

  if (runtime.isLoading || workspaces.isLoading) {
    return <div className="startupScreen"><span className="brandMark"><Octagon size={27} /></span><h1>Octopus</h1><p>正在连接本地证据工作台…</p></div>;
  }
  if (runtime.isError || workspaces.isError) {
    return <div className="recoveryScreen"><AlertTriangle size={35} /><h1>本地服务不可用</h1><p>请重新启动 Octopus。原始资料没有被修改。</p></div>;
  }
  if (!workspaces.data?.length) {
    return <div className="firstRun"><div className="firstRunBrand"><span className="brandMark"><Octagon size={25} /></span><strong>Octopus</strong><small>{runtime.data?.product_version}</small></div><Onboarding onCreated={(created) => { setWorkspaceId(created.workspace_id); void queryClient.invalidateQueries({ queryKey: ["workspaces"] }); }} /></div>;
  }
  if (!currentWorkspace) return null;

  const inspectorVisible = page === "search";
  return (
    <div className={`appShell page-${page} ${inspectorVisible ? "withInspector" : "withoutInspector"}`}>
      <header className="topBar">
        <div className="brand"><span className="brandMark"><Octagon size={18} /></span><strong>Octopus</strong><span className="versionLabel">V2</span></div>
        <label className="workspaceSwitcher">
          <Library size={16} />
          <select value={workspaceId} onChange={(event) => setWorkspaceId(event.target.value)} aria-label="当前资料空间">
            {workspaces.data.map((item) => <option key={item.workspace_id} value={item.workspace_id}>{item.name}</option>)}
          </select>
        </label>
        <div className="syncStatus"><span className={currentWorkspace.available ? "onlineDot" : "offlineDot"} />{currentWorkspace.available ? `已同步 ${relativeTime(currentWorkspace.health.last_sync_at)}` : "原始资料不可访问"}</div>
      </header>
      <nav className="sideNav" aria-label="主导航">
        <div className="navItems">
          {navItems.map(({ id, label, icon: Icon }) => <button key={id} className={page === id ? "navActive" : ""} onClick={() => setPage(id)} aria-label={label} title={label}><Icon size={19} /><span>{label}</span></button>)}
        </div>
        <div className="navFooter"><span className="localBadge">仅本机</span><small>原文件只读</small></div>
      </nav>
      <main className="mainWorkspace">
        {page === "search" && <SearchWorkspace addResult={addResult} />}
        {page === "tasks" && <TaskPacksView />}
        {page === "documents" && <RepositoriesView workspace={currentWorkspace} />}
        {page === "settings" && <AISettingsView />}
      </main>
      {inspectorVisible && <EvidenceInspector onAdd={(result) => addResult(result, useAppStore.getState().query || "资料核对任务")} />}
      <TaskTray />
    </div>
  );
}
