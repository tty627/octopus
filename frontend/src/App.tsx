import { useEffect, useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  AlertTriangle,
  Library,
  ListTodo,
  Octagon,
  Search,
  Settings,
} from "lucide-react";
import { api, runtimeBootstrap } from "./api";
import { loadWindowState, saveWindowState } from "./bridge";
import {
  loadLocalDraft,
  saveLocalDraft,
  useAppStore,
} from "./store";
import { persistActiveTask } from "./taskPersistence";
import type { PageId, ServiceJob, Workspace } from "./types";
import { workspaceOptionLabels, workspaceSyncStatusText } from "./workspaceUi";
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
  const restoreSequence = useRef(0);
  const [windowStateReady, setWindowStateReady] = useState(false);
  const queryClient = useQueryClient();
  const runtime = useQuery({ queryKey: ["runtime"], queryFn: runtimeBootstrap, retry: 1 });
  const workspaces = useQuery({
    queryKey: ["workspaces"],
    queryFn: api.workspaces,
    enabled: runtime.isSuccess,
    retry: 2,
    refetchInterval: 20_000,
  });
  const { addResult, adding, actionError, clearActionError } = useTaskActions();
  const currentWorkspace = workspaces.data?.find((item) => item.workspace_id === workspaceId);
  const workspaceLabels = workspaceOptionLabels(workspaces.data ?? []);

  const selectWorkspace = (selectedWorkspaceId: string) => {
    restoreSequence.current += 1;
    setWorkspaceId(selectedWorkspaceId);
  };

  const openCreatedWorkspace = (created: Workspace, job: ServiceJob) => {
    restored.current = true;
    restoreSequence.current += 1;
    setWindowStateReady(true);
    queryClient.setQueryData<Workspace[]>(["workspaces"], (current = []) => [
      created,
      ...current.filter((item) => item.workspace_id !== created.workspace_id),
    ]);
    queryClient.setQueryData<ServiceJob[]>(["jobs", created.workspace_id], [job]);
    setWorkspaceId(created.workspace_id);
    setPage("documents");
    void queryClient.invalidateQueries({ queryKey: ["workspaces"] });
  };

  useEffect(() => {
    if (restored.current || !workspaces.data?.length) return;
    restored.current = true;
    const restoreId = ++restoreSequence.current;
    const canApplyTaskRestore = (selectedWorkspaceId: string) =>
      restoreSequence.current === restoreId &&
      useAppStore.getState().workspaceId === selectedWorkspaceId;
    void (async () => {
      let selected = workspaces.data[0]?.workspace_id ?? "";
      try {
        const state = await loadWindowState();
        const requestedWorkspace = state.workspace_id ?? state.repository_id;
        selected = workspaces.data.find((item) => item.workspace_id === requestedWorkspace)?.workspace_id ?? selected;
        setWorkspaceId(selected);
        if (state.page && navItems.some((item) => item.id === state.page)) setPage(state.page);
        const requestedTask = state.task_id ?? state.task_pack_id;
        if (requestedTask && selected && canApplyTaskRestore(selected)) {
          try {
            const restoredTask = await api.task(selected, requestedTask);
            if (canApplyTaskRestore(selected)) setTask(restoredTask);
          } catch {
            if (canApplyTaskRestore(selected)) setTask(null);
          }
        }
      } catch {
        setWorkspaceId(selected);
      } finally {
        if (!useAppStore.getState().workspaceId) setWorkspaceId(selected);
        setWindowStateReady(true);
      }
    })();
  }, [setPage, setTask, setWorkspaceId, workspaces.data]);

  useEffect(() => {
    if (!windowStateReady || !workspaceId) return;
    saveWindowState({ page, workspace_id: workspaceId, task_id: activeTask?.task_id });
  }, [activeTask?.task_id, page, windowStateReady, workspaceId]);

  useEffect(() => {
    if (!activeTask || taskDirty) return;
    const draft = loadLocalDraft(activeTask);
    if (!draft) return;
    if (draft.revision === activeTask.revision) setTask(draft, true);
    else setSaveState("conflict");
  }, [activeTask, setSaveState, setTask, taskDirty]);

  useEffect(() => {
    if (!activeTask || !taskDirty) return;
    const { workspace_id: taskWorkspaceId, task_id: taskId } = activeTask;
    const timer = window.setTimeout(() => {
      void persistActiveTask(queryClient, taskWorkspaceId, taskId).catch(() => undefined);
    }, 700);
    return () => window.clearTimeout(timer);
  }, [activeTask, queryClient, taskDirty]);

  useEffect(() => {
    const preserveDraft = () => {
      const state = useAppStore.getState();
      if (state.activeTask && state.taskDirty) saveLocalDraft(state.activeTask);
    };
    window.addEventListener("beforeunload", preserveDraft);
    return () => window.removeEventListener("beforeunload", preserveDraft);
  }, []);

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
    return <div className="firstRun"><div className="firstRunBrand"><span className="brandMark"><Octagon size={25} /></span><strong>Octopus</strong><small>{runtime.data?.product_version}</small></div><Onboarding onCreated={openCreatedWorkspace} /></div>;
  }
  if (!currentWorkspace) return null;

  const inspectorVisible = page === "search";
  return (
    <div className={`appShell page-${page} ${inspectorVisible ? "withInspector" : "withoutInspector"}`}>
      <header className="topBar">
        <div className="brand"><span className="brandMark"><Octagon size={18} /></span><strong>Octopus</strong><span className="versionLabel">V2</span></div>
        <label className="workspaceSwitcher">
          <Library size={16} />
          <select value={workspaceId} onChange={(event) => selectWorkspace(event.target.value)} aria-label="当前资料空间">
            {workspaces.data.map((item) => <option key={item.workspace_id} value={item.workspace_id}>{workspaceLabels.get(item.workspace_id) ?? item.name}</option>)}
          </select>
        </label>
        <div className="syncStatus"><span className={currentWorkspace.available ? "onlineDot" : "offlineDot"} />{workspaceSyncStatusText(currentWorkspace)}</div>
      </header>
      <nav className="sideNav" aria-label="主导航">
        <div className="navItems">
          {navItems.map(({ id, label, icon: Icon }) => <button key={id} className={page === id ? "navActive" : ""} onClick={() => setPage(id)} aria-label={label} title={label}><Icon size={19} /><span>{label}</span></button>)}
        </div>
        <div className="navFooter"><span className="localBadge">仅本机</span><small>原文件只读</small></div>
      </nav>
      <main className="mainWorkspace">
        {page === "search" && <SearchWorkspace addResult={addResult} adding={adding} actionError={actionError} clearActionError={clearActionError} />}
        {page === "tasks" && <TaskPacksView key={workspaceId} />}
        {page === "documents" && <RepositoriesView key={workspaceId} workspace={currentWorkspace} />}
        {page === "settings" && <AISettingsView key={workspaceId} />}
      </main>
      {inspectorVisible && <EvidenceInspector adding={adding} actionError={actionError} onAdd={(result, evidence) => addResult(result, evidence, useAppStore.getState().submittedQuery || "资料核对任务", workspaceId)} />}
      <TaskTray />
    </div>
  );
}
