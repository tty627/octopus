import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import {
  AlertTriangle,
  ArrowRight,
  Clock3,
  FileClock,
  FolderOpen,
  History,
  Search,
} from "lucide-react";
import { api } from "../api";
import { openLocalUri } from "../bridge";
import { recentActivity } from "../activity";
import { useAppStore } from "../store";
import type { Workspace } from "../types";
import { relativeTime, taskSummaryIssueCount } from "../utils";

export function HomeView({ workspace }: { workspace: Workspace }) {
  const setPage = useAppStore((state) => state.setPage);
  const requestSearch = useAppStore((state) => state.requestSearch);
  const setTask = useAppStore((state) => state.setTask);
  const activeTask = useAppStore((state) => state.activeTask);
  const [researchInput, setResearchInput] = useState("");
  const [openError, setOpenError] = useState("");
  const summaries = useQuery({
    queryKey: ["tasks", workspace.workspace_id],
    queryFn: () => api.tasks(workspace.workspace_id),
  });
  const changes = useQuery({
    queryKey: ["changes", workspace.workspace_id],
    queryFn: () => api.changes(workspace.workspace_id),
    retry: false,
  });
  const recentSearches = recentActivity.searches();
  const recentOpens = recentActivity.opens();

  const startSearch = (value = researchInput) => {
    const next = value.trim();
    if (!next) return;
    requestSearch(next);
    setPage("search");
  };

  const openTask = async (taskId: string) => {
    try {
      const loaded = await api.task(workspace.workspace_id, taskId);
      if (useAppStore.getState().workspaceId !== workspace.workspace_id) return;
      setTask(loaded);
      setPage("tasks");
    } catch {
      setOpenError("资料包暂时无法载入，请重试。");
    }
  };

  const openRecent = async (documentId: string) => {
    setOpenError("");
    try {
      const target = await api.openTarget(workspace.workspace_id, documentId);
      await openLocalUri(target.uri);
    } catch {
      setOpenError("这份来源当前不可访问，请同步资料空间后重试。");
    }
  };

  const continuePacks = summaries.data?.slice(0, 4) ?? [];
  const pendingChanges = changes.data?.filter((item) => !item.acknowledged).slice(0, 5) ?? [];

  return (
    <div className="homePage">
      <header className="homeLead">
        <div>
          <p className="eyebrow">{workspace.name}</p>
          <h1>研究工作台</h1>
          <p>从原始资料中定位证据，并整理成可复核、可引用的资料包。</p>
        </div>
        <div className="homeHealth" aria-label="资料空间概况">
          <strong>{workspace.health.document_count}</strong>
          <span>份资料</span>
          <small>{workspace.health.metadata_only_count + workspace.health.failed_count} 份需要处理</small>
        </div>
      </header>

      <form className="researchInput" onSubmit={(event) => { event.preventDefault(); startSearch(); }}>
        <Search size={21} />
        <input
          value={researchInput}
          onChange={(event) => setResearchInput(event.target.value)}
          aria-label="输入研究问题"
          placeholder="输入研究问题、主题或需要核对的概念"
        />
        <button className="primaryButton" disabled={!researchInput.trim()}>
          开始查找<ArrowRight size={17} />
        </button>
      </form>

      {openError && <div className="pageError" role="alert"><AlertTriangle size={18} />{openError}</div>}

      <div className="homeColumns">
        <section className="homeSection" aria-labelledby="continue-packs">
          <div className="sectionHeading">
            <div><FolderOpen size={18} /><h2 id="continue-packs">继续资料包</h2></div>
            <button className="textButton smallButton" onClick={() => setPage("tasks")}>查看全部</button>
          </div>
          {activeTask && (
            <button className="activityRow activityPrimary" onClick={() => setPage("tasks")}>
              <span><strong>{activeTask.title}</strong><small>{activeTask.items.length} 条证据 · 正在编辑</small></span>
              <ArrowRight size={16} />
            </button>
          )}
          {continuePacks.filter((item) => item.task_id !== activeTask?.task_id).map((item) => {
            const reviewCount = taskSummaryIssueCount(item);
            return (
              <button className="activityRow" key={item.task_id} onClick={() => void openTask(item.task_id)}>
                <span><strong>{item.title}</strong><small>{item.item_count} 条证据 · {relativeTime(item.updated_at)}</small></span>
                {reviewCount > 0 && <em>{reviewCount} 条待复核</em>}
                <ArrowRight size={16} />
              </button>
            );
          })}
          {!activeTask && continuePacks.length === 0 && (
            <div className="homeEmpty"><FolderOpen size={20} /><span>还没有资料包。从上方输入一个研究问题开始。</span></div>
          )}
        </section>

        <section className="homeSection" aria-labelledby="source-changes">
          <div className="sectionHeading"><div><FileClock size={18} /><h2 id="source-changes">来源变化</h2></div></div>
          {pendingChanges.map((item) => (
            <div className="changeRow" key={item.change_id}>
              <span className={`changeKind change-${item.kind}`}>{changeLabel(item.kind)}</span>
              <span><strong>{item.name}</strong><small>{item.message || item.relative_path}</small></span>
              <time>{relativeTime(item.occurred_at)}</time>
            </div>
          ))}
          {!changes.isLoading && pendingChanges.length === 0 && (
            <div className="homeEmpty"><FileClock size={20} /><span>没有需要处理的来源变化。</span></div>
          )}
        </section>
      </div>

      <div className="homeColumns homeHistory">
        <section className="homeSection" aria-labelledby="recent-searches">
          <div className="sectionHeading"><div><History size={18} /><h2 id="recent-searches">最近搜索</h2></div></div>
          <div className="compactActivity">
            {recentSearches.map((item) => <button key={item.value} onClick={() => startSearch(item.value)}><Search size={14} /><span>{item.label}</span><time>{relativeTime(item.at)}</time></button>)}
            {recentSearches.length === 0 && <span className="mutedLine">搜索记录只保存在本机。</span>}
          </div>
        </section>
        <section className="homeSection" aria-labelledby="recent-opens">
          <div className="sectionHeading"><div><Clock3 size={18} /><h2 id="recent-opens">最近打开</h2></div></div>
          <div className="compactActivity">
            {recentOpens.map((item) => <button key={item.value} onClick={() => void openRecent(item.value)}><FolderOpen size={14} /><span>{item.label}<small>{item.detail}</small></span><time>{relativeTime(item.at)}</time></button>)}
            {recentOpens.length === 0 && <span className="mutedLine">核对来源后会显示在这里。</span>}
          </div>
        </section>
      </div>
    </div>
  );
}

function changeLabel(kind: string): string {
  if (kind === "added") return "新增";
  if (kind === "modified") return "已修改";
  if (kind === "moved") return "已移动";
  if (kind === "deleted") return "已删除";
  return "解析提醒";
}
