import { useEffect, useMemo, useRef } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  AlertTriangle,
  BriefcaseBusiness,
  Database,
  FolderKanban,
  LayoutDashboard,
  Octagon,
  RefreshCw,
  Search,
  Settings,
  WifiOff,
} from "lucide-react";
import { ApiError, api, runtimeBootstrap } from "./api";
import { loadWindowState, saveWindowState } from "./bridge";
import {
  clearLocalDraft,
  draftStorageKey,
  mergeTaskPackSave,
  saveLocalDraft,
  useAppStore,
} from "./store";
import type { PageId, TaskPack } from "./types";
import { relativeTime } from "./utils";
import { EvidenceInspector } from "./components/EvidenceInspector";
import { AISettingsView } from "./components/AISettingsView";
import { Onboarding } from "./components/Onboarding";
import { RepositoriesView } from "./components/RepositoriesView";
import { SearchWorkspace } from "./components/SearchWorkspace";
import { TaskPacksView } from "./components/TaskPacksView";
import { TaskTray } from "./components/TaskTray";
import { Workbench } from "./components/Workbench";
import { useTaskPackActions } from "./taskPackActions";

const navItems: Array<{ id: PageId; label: string; icon: typeof LayoutDashboard }> = [
  { id: "workbench", label: "工作台", icon: LayoutDashboard },
  { id: "search", label: "搜索", icon: Search },
  { id: "task-packs", label: "任务包", icon: FolderKanban },
  { id: "repositories", label: "资料空间", icon: Database },
  { id: "settings", label: "设置", icon: Settings },
];

export default function App() {
  const page = useAppStore((state) => state.page);
  const setPage = useAppStore((state) => state.setPage);
  const repositoryId = useAppStore((state) => state.repositoryId);
  const setRepositoryId = useAppStore((state) => state.setRepositoryId);
  const activeTaskPack = useAppStore((state) => state.activeTaskPack);
  const taskPackDirty = useAppStore((state) => state.taskPackDirty);
  const setTaskPack = useAppStore((state) => state.setTaskPack);
  const setSaveState = useAppStore((state) => state.setSaveState);
  const closeInspector = useAppStore((state) => state.closeInspector);
  const restored = useRef(false);
  const queryClient = useQueryClient();
  const runtime = useQuery({ queryKey: ["runtime"], queryFn: runtimeBootstrap, retry: 1 });
  const repositories = useQuery({
    queryKey: ["repositories"],
    queryFn: api.repositories,
    enabled: runtime.isSuccess,
    retry: 2,
    refetchInterval: 30_000,
  });
  const { addResult } = useTaskPackActions();

  useEffect(() => {
    if (restored.current || !repositories.data) return;
    restored.current = true;
    void loadWindowState().then((state) => {
      const selected = repositories.data.find((item) => item.repository_id === state.repository_id)?.repository_id ?? repositories.data[0]?.repository_id ?? "";
      setRepositoryId(selected);
      if (state.page && navItems.some((item) => item.id === state.page)) setPage(state.page);
      if (state.task_pack_id && selected) void api.taskPack(selected, state.task_pack_id).then((pack) => setTaskPack(pack)).catch(() => undefined);
    });
  }, [repositories.data, setPage, setRepositoryId, setTaskPack]);

  useEffect(() => {
    saveWindowState({ page, repository_id: repositoryId, task_pack_id: activeTaskPack?.task_pack_id });
  }, [activeTaskPack?.task_pack_id, page, repositoryId]);

  useEffect(() => {
    if (!activeTaskPack || taskPackDirty) return;
    const stored = localStorage.getItem(draftStorageKey(activeTaskPack.repository_id, activeTaskPack.task_pack_id));
    if (!stored) return;
    try {
      const draft = JSON.parse(stored) as TaskPack;
      if (draft.revision === activeTaskPack.revision) setTaskPack(draft, true);
      else setSaveState("conflict");
    } catch {
      localStorage.removeItem(draftStorageKey(activeTaskPack.repository_id, activeTaskPack.task_pack_id));
    }
  }, [activeTaskPack, setSaveState, setTaskPack, taskPackDirty]);

  useEffect(() => {
    if (!activeTaskPack || !taskPackDirty) return;
    const draft = activeTaskPack;
    const timer = window.setTimeout(async () => {
      setSaveState("saving");
      try {
        const saved = await api.saveTaskPack(draft);
        clearLocalDraft(draft);
        const merged = mergeTaskPackSave(saved, draft, useAppStore.getState().activeTaskPack);
        if (merged) {
          setTaskPack(merged.pack, merged.dirty);
          if (merged.dirty) saveLocalDraft(merged.pack);
        }
        void queryClient.invalidateQueries({ queryKey: ["task-packs", draft.repository_id] });
      } catch (error) {
        saveLocalDraft(draft);
        setSaveState(error instanceof ApiError && error.status === 409 ? "conflict" : "offline");
      }
    }, 800);
    return () => window.clearTimeout(timer);
  }, [activeTaskPack, queryClient, setSaveState, setTaskPack, taskPackDirty]);

  useEffect(() => {
    const shortcuts = (event: KeyboardEvent) => {
      if (event.ctrlKey && event.key.toLowerCase() === "f") {
        event.preventDefault();
        setPage("search");
        window.setTimeout(() => document.querySelector<HTMLInputElement>("#workspace-search")?.focus(), 0);
      }
      if (event.ctrlKey && event.key.toLowerCase() === "n") {
        event.preventDefault();
        setPage("task-packs");
      }
      if (event.key === "Escape") closeInspector();
    };
    window.addEventListener("keydown", shortcuts);
    return () => window.removeEventListener("keydown", shortcuts);
  }, [closeInspector, setPage]);

  const currentRepository = useMemo(
    () => repositories.data?.find((item) => item.repository_id === repositoryId) ?? repositories.data?.[0],
    [repositories.data, repositoryId],
  );

  if (runtime.isLoading || repositories.isLoading) {
    return <div className="startupScreen"><div className="brandMark"><Octagon size={28} /></div><h1>Octopus</h1><p>正在连接本地资料服务...</p></div>;
  }
  if (runtime.isError || repositories.isError) {
    const detail = runtime.error instanceof Error ? runtime.error.message : repositories.error instanceof Error ? repositories.error.message : "本地服务暂时不可用。";
    return <div className="recoveryScreen"><WifiOff size={34} /><h1>暂时无法连接本地服务</h1><p>当前草稿与原始文件没有受到影响。Octopus 会在重新连接后校验任务包版本。</p><button className="primaryButton" onClick={() => { void runtime.refetch(); void repositories.refetch(); }}><RefreshCw size={17} />重新连接</button><details><summary>技术详情</summary><pre>{detail}</pre></details></div>;
  }
  if (!repositories.data?.length) {
    return <Onboarding onCreated={(repository) => { setRepositoryId(repository.repository_id); void queryClient.invalidateQueries({ queryKey: ["repositories"] }); }} />;
  }
  if (!currentRepository) return null;

  return (
    <div className={`appShell page-${page}`}>
      <header className="topBar">
        <div className="brand"><span className="brandMark"><Octagon size={21} /></span><strong>Octopus</strong><span className="versionLabel">{runtime.data?.product_version}</span></div>
        <label className="workspaceSwitcher"><span className="srOnly">当前资料空间</span><BriefcaseBusiness size={17} /><select value={currentRepository.repository_id} onChange={(event) => setRepositoryId(event.target.value)}>{repositories.data.map((repository) => <option key={repository.repository_id} value={repository.repository_id}>{repository.name}</option>)}</select></label>
        <div className={`syncStatus ${currentRepository.available ? "" : "syncError"}`}>{currentRepository.available ? <><span className="onlineDot" /> 已同步 {relativeTime(currentRepository.last_successful_update_at)}</> : <><AlertTriangle size={16} /> 来源不可访问</>}</div>
      </header>
      <nav className="sideNav" aria-label="主导航">
        <div className="navItems">{navItems.map(({ id, label, icon: Icon }) => <button aria-label={label} title={label} className={page === id ? "navActive" : ""} key={id} onClick={() => setPage(id)}><Icon size={19} /><span>{label}</span></button>)}</div>
        <div className="navFooter"><span className="localBadge">仅本机</span><small>Raw 保持只读</small></div>
      </nav>
      <main className="mainWorkspace">
        {page === "workbench" && <Workbench repository={currentRepository} />}
        {page === "search" && <SearchWorkspace addResult={addResult} />}
        {page === "task-packs" && <TaskPacksView />}
        {page === "repositories" && <RepositoriesView repositories={repositories.data} />}
        {page === "settings" && <AISettingsView repository={currentRepository} />}
      </main>
      {page !== "settings" && <EvidenceInspector onAdd={(result) => addResult(result, useAppStore.getState().query || "资料整理任务")} />}
      <TaskTray />
    </div>
  );
}
