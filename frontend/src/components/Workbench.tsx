import { useQuery } from "@tanstack/react-query";
import { AlertTriangle, ArrowRight, CheckCircle2, Clock3, FileSearch, FolderKanban, Plus, Search } from "lucide-react";
import { api } from "../api";
import { useAppStore } from "../store";
import type { Repository } from "../types";
import { relativeTime } from "../utils";

export function Workbench({ repository }: { repository: Repository }) {
  const setPage = useAppStore((state) => state.setPage);
  const query = useAppStore((state) => state.query);
  const setQuery = useAppStore((state) => state.setQuery);
  const taskPacks = useQuery({ queryKey: ["task-packs", repository.repository_id], queryFn: () => api.taskPacks(repository.repository_id) });
  const needsAttention = Object.entries(repository.states ?? {}).filter(([state, count]) => !["clean", "indexed"].includes(state) && count > 0);

  const startSearch = () => {
    if (!query.trim()) return;
    setPage("search");
  };

  return (
    <div className="workbench">
      <section className="workbenchLead">
        <div><p className="eyebrow">{repository.name}</p><h1>今天要从资料里完成什么？</h1></div>
        <form className="workbenchSearch" onSubmit={(event) => { event.preventDefault(); startSearch(); }}>
          <Search size={21} /><input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="查找文件，或描述一个需要资料支持的任务..." aria-label="工作台搜索" /><button className="primaryButton" disabled={!query.trim()}>开始<ArrowRight size={17} /></button>
        </form>
        <div className="suggestionList horizontalSuggestions">
          {["查找最终版报价", "准备季度项目汇报", "整理关键决策与风险"].map((value) => <button key={value} onClick={() => { setQuery(value); setPage("search"); }}>{value}</button>)}
        </div>
      </section>

      <section className="workbenchSection">
        <div className="sectionHeading"><div><p className="eyebrow">继续工作</p><h2>最近任务包</h2></div><button className="textButton" onClick={() => setPage("task-packs")}>查看全部<ArrowRight size={16} /></button></div>
        {taskPacks.data?.length ? (
          <div className="taskSummaryList">
            {taskPacks.data.slice(0, 4).map((pack) => (
              <button key={pack.task_pack_id} onClick={() => void api.taskPack(repository.repository_id, pack.task_pack_id).then((value) => { useAppStore.getState().setTaskPack(value); setPage("task-packs"); })}>
                <FolderKanban size={19} /><span><strong>{pack.title}</strong><small>{pack.item_count} 项资料 · {pack.pending_count} 项待核验</small></span><span className="taskDate">{relativeTime(pack.updated_at)}</span><ArrowRight size={16} />
              </button>
            ))}
          </div>
        ) : (
          <div className="inlineEmpty"><FileSearch size={22} /><div><strong>还没有任务包</strong><p>从一次搜索确认资料，建立第一个可交付的资料集合。</p></div><button className="secondaryButton" onClick={() => setPage("search")}><Plus size={17} />发起任务</button></div>
        )}
      </section>

      <section className="workbenchSection healthOverview">
        <div className="sectionHeading"><div><p className="eyebrow">资料状态</p><h2>{needsAttention.length ? "有少量资料需要留意" : "资料空间运行正常"}</h2></div><button className="textButton" onClick={() => setPage("repositories")}>打开健康中心<ArrowRight size={16} /></button></div>
        <div className="healthRows">
          <div><CheckCircle2 size={19} /><span><strong>上次同步</strong><small>{relativeTime(repository.last_successful_update_at)}</small></span></div>
          <div><Clock3 size={19} /><span><strong>已建立索引</strong><small>{repository.states?.indexed ?? 0} 项资料可搜索</small></span></div>
          {needsAttention.map(([state, count]) => <div className="attentionRow" key={state}><AlertTriangle size={19} /><span><strong>{count} 项等待系统处理</strong><small>不影响已完成索引的资料</small></span></div>)}
        </div>
      </section>
    </div>
  );
}
