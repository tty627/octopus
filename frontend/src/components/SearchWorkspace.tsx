import * as Checkbox from "@radix-ui/react-checkbox";
import { useEffect, useMemo, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import {
  AlertTriangle,
  Check,
  CheckSquare,
  ChevronDown,
  File,
  Folder,
  ListFilter,
  LoaderCircle,
  Plus,
  Search,
  Settings2,
  Sparkles,
  X,
} from "lucide-react";
import { ApiError, api } from "../api";
import { EMPTY_FILTERS, useAppStore } from "../store";
import type { IndexType, SearchReport, SearchResult } from "../types";
import { groupSearchResults, humanStatus } from "../utils";

interface SearchWorkspaceProps {
  addResult: (result: SearchResult, defaultTitle?: string) => Promise<void>;
}

const typeOptions: Array<{ value: IndexType; label: string }> = [
  { value: "leaf", label: "PDF / Office / 图片" },
  { value: "text", label: "文本与代码" },
  { value: "foldernode", label: "文件夹" },
];

const degradationMessages: Record<string, string> = {
  ai_disabled: "当前资料空间尚未启用 AI。",
  ai_key_not_configured: "尚未配置 API Key。",
  ai_auth_failed: "API Key 验证失败。",
  ai_quota_exhausted: "模型账户余额或配额不足。",
  ai_rate_limited: "模型服务请求过于频繁。",
  ai_budget_exhausted: "本次 AI 使用预算已经到达上限。",
  ai_no_valid_evidence: "模型没有返回可核验的引用。",
  ai_invalid_output: "模型返回内容无法验证。",
  ai_unavailable: "暂时无法连接模型服务。",
};

export function SearchWorkspace({ addResult }: SearchWorkspaceProps) {
  const repositoryId = useAppStore((state) => state.repositoryId);
  const query = useAppStore((state) => state.query);
  const setQuery = useAppStore((state) => state.setQuery);
  const filters = useAppStore((state) => state.filters);
  const setFilters = useAppStore((state) => state.setFilters);
  const aiEnabled = useAppStore((state) => state.aiEnabled);
  const setAiEnabled = useAppStore((state) => state.setAiEnabled);
  const setPage = useAppStore((state) => state.setPage);
  const inspect = useAppStore((state) => state.inspect);
  const inspector = useAppStore((state) => state.inspector);
  const [intent, setIntent] = useState<"find" | "task">("find");
  const [report, setReport] = useState<SearchReport | null>(null);
  const [stage, setStage] = useState<"idle" | "local" | "ready" | "ai" | "degraded">("idle");
  const [error, setError] = useState("");
  const [filterOpen, setFilterOpen] = useState(false);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const sequence = useRef(0);
  const controller = useRef<AbortController | null>(null);
  const aiSettings = useQuery({
    queryKey: ["ai-settings", repositoryId],
    queryFn: () => api.aiSettings(repositoryId),
    enabled: Boolean(repositoryId),
  });
  const aiAvailable = Boolean(
    aiSettings.data?.enabled && aiSettings.data.credential_configured,
  );

  const runSearch = async () => {
    const trimmed = query.trim();
    if (!repositoryId || !trimmed) return;
    sequence.current += 1;
    const requestSequence = sequence.current;
    controller.current?.abort();
    const current = new AbortController();
    controller.current = current;
    setStage("local");
    setError("");
    setSelected(new Set());
    try {
      const local = await api.search(repositoryId, trimmed, "local", filters, current.signal);
      if (requestSequence !== sequence.current) return;
      setReport(local);
      setStage("ready");
      inspect(local.results[0] ?? null);
      if (!aiEnabled) return;
      setStage("ai");
      try {
        const enhanced = await api.search(repositoryId, trimmed, "auto", filters, current.signal);
        if (requestSequence !== sequence.current) return;
        setReport(enhanced);
        setStage(enhanced.actual_mode === "degraded" ? "degraded" : "ready");
      } catch (reason) {
        if (reason instanceof DOMException && reason.name === "AbortError") return;
        if (requestSequence === sequence.current) setStage("degraded");
      }
    } catch (reason) {
      if (reason instanceof DOMException && reason.name === "AbortError") return;
      if (requestSequence !== sequence.current) return;
      setStage("idle");
      setError(reason instanceof ApiError ? reason.message : "当前搜索没有完成，请重试。 ");
    }
  };

  useEffect(() => () => controller.current?.abort(), []);
  useEffect(() => {
    if (!aiAvailable && aiEnabled) setAiEnabled(false);
  }, [aiAvailable, aiEnabled, setAiEnabled]);
  useEffect(() => {
    const shortcut = (event: KeyboardEvent) => {
      if (event.ctrlKey && event.key === "Enter" && inspector) {
        event.preventDefault();
        void addResult(inspector, query || "资料整理任务");
      }
    };
    window.addEventListener("keydown", shortcut);
    return () => window.removeEventListener("keydown", shortcut);
  }, [addResult, inspector, query]);

  const grouped = useMemo(() => groupSearchResults(report?.results ?? []), [report]);
  const hasFilters = Object.values(filters).some((value) => Array.isArray(value) ? value.length > 0 : Boolean(value));

  const toggleType = (value: IndexType) => {
    setFilters({
      ...filters,
      index_types: filters.index_types.includes(value)
        ? filters.index_types.filter((item) => item !== value)
        : [...filters.index_types, value],
    });
  };

  const bulkAdd = async () => {
    const values = report?.results.filter((item) => selected.has(item.node_id)) ?? [];
    for (const value of values) await addResult(value, query || "资料整理任务");
    setSelected(new Set());
  };

  return (
    <div className="searchWorkspace">
      <div className="searchHeader">
        <div className="segmented" aria-label="搜索目的">
          <button className={intent === "find" ? "segmentActive" : ""} onClick={() => setIntent("find")}>找资料</button>
          <button className={intent === "task" ? "segmentActive" : ""} onClick={() => setIntent("task")}>做任务</button>
        </div>
        {aiAvailable ? (
          <label className="aiToggle">
            <Checkbox.Root checked={aiEnabled} onCheckedChange={(value) => setAiEnabled(value === true)} aria-label="启用 AI 任务辅助">
              <Checkbox.Indicator><Check size={13} /></Checkbox.Indicator>
            </Checkbox.Root>
            <Sparkles size={15} />AI 任务辅助
          </label>
        ) : (
          <button className="aiSetupButton" onClick={() => setPage("settings")}><Settings2 size={15} />配置 AI</button>
        )}
      </div>
      <form className="searchForm" onSubmit={(event) => { event.preventDefault(); void runSearch(); }}>
        <Search size={20} />
        <input
          id="workspace-search"
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          placeholder={intent === "find" ? "查找文件、内容、项目或时间..." : "描述你要完成的任务，例如：准备新能源项目季度汇报"}
          aria-label="查找资料或描述任务"
        />
        {query && <button type="button" className="iconButton" aria-label="清空查询" title="清空" onClick={() => setQuery("")}><X size={17} /></button>}
        <button className="primaryButton" type="submit" disabled={!query.trim() || stage === "local"}>{stage === "local" ? <LoaderCircle className="spin" size={17} /> : <Search size={17} />}搜索</button>
      </form>
      <div className="filterBar">
        <button className={`filterButton ${hasFilters ? "filterActive" : ""}`} onClick={() => setFilterOpen((value) => !value)}><ListFilter size={16} />筛选{hasFilters ? " · 已应用" : ""}<ChevronDown size={15} /></button>
        {hasFilters && <button className="textButton smallButton" onClick={() => setFilters(EMPTY_FILTERS)}>清除筛选</button>}
        <div className="searchProgress" aria-live="polite">
          {stage === "local" && "正在查找本地索引..."}
          {stage === "ai" && <><Sparkles size={14} /> 本地结果已就绪，AI 正在补充排序</>}
          {stage === "degraded" && <><AlertTriangle size={14} /> AI 未参与，本地结果保持完整</>}
          {stage === "ready" && report && `找到 ${report.results.length} 项资料 · ${report.duration_ms} ms`}
        </div>
      </div>
      {filterOpen && (
        <div className="filterPanel">
          <fieldset><legend>资料类型</legend>{typeOptions.map((option) => <label key={option.value}><input type="checkbox" checked={filters.index_types.includes(option.value)} onChange={() => toggleType(option.value)} />{option.label}</label>)}</fieldset>
          <label>路径范围<input value={filters.path_prefix} onChange={(event) => setFilters({ ...filters, path_prefix: event.target.value })} placeholder="例如：项目A/预算" /></label>
          <label>修改时间从<input type="date" value={filters.modified_after.slice(0, 10)} onChange={(event) => setFilters({ ...filters, modified_after: event.target.value ? `${event.target.value}T00:00:00+00:00` : "" })} /></label>
          <label>到<input type="date" value={filters.modified_before.slice(0, 10)} onChange={(event) => setFilters({ ...filters, modified_before: event.target.value ? `${event.target.value}T23:59:59+00:00` : "" })} /></label>
        </div>
      )}
      {selected.size > 0 && (
        <div className="selectionBar"><CheckSquare size={17} /><span>已选择 {selected.size} 项</span><button className="primaryButton smallButton" onClick={() => void bulkAdd()}><Plus size={16} />加入任务包</button><button className="textButton smallButton" onClick={() => setSelected(new Set())}>取消</button></div>
      )}
      {error && <div className="pageError" role="alert"><AlertTriangle size={20} /><div><strong>搜索没有完成</strong><p>{error}</p></div><button className="secondaryButton" onClick={() => void runSearch()}>重试</button></div>}
      {report?.actual_mode === "ai" && (
        <section className="aiAnswerPanel" aria-label="AI 建议">
          <div className="aiAnswerHeading"><Sparkles size={18} /><div><strong>AI 建议</strong><small>{Object.keys(report.ai_usage?.models ?? {})[0] || aiSettings.data?.model}</small></div></div>
          <p>{report.answer.summary}</p>
          {report.answer.warnings.length > 0 && <div className="aiWarnings">{report.answer.warnings.map((warning) => <span key={warning}><AlertTriangle size={14} />{warning}</span>)}</div>}
        </section>
      )}
      {stage === "degraded" && report && (
        <div className="aiDegradedNotice" role="status"><AlertTriangle size={17} /><span><strong>AI 未参与本次搜索</strong><small>{degradationMessages[report.degradation_reason] || "本地搜索结果保持完整。"}</small></span><button className="textButton smallButton" onClick={() => setPage("settings")}><Settings2 size={15} />检查设置</button></div>
      )}
      {!report && !error && stage === "idle" && (
        <div className="searchEmpty"><Search size={28} /><h2>{intent === "find" ? "从现有资料中找到可核验证据" : "先描述任务，再逐项确认资料"}</h2><p>可以输入文件名、项目、人名、时间，或直接说明你要完成的工作。</p><div className="suggestionList">{["最终版报价和审批记录", "季度汇报需要的进展、预算与风险", "最近一次范围变更决策"].map((value) => <button key={value} onClick={() => { setQuery(value); }}>{value}</button>)}</div></div>
      )}
      {report && report.results.length === 0 && (
        <div className="searchEmpty"><Search size={28} /><h2>当前范围没有找到匹配资料</h2><p>可以放宽路径或时间条件，也可以换用文件名片段。</p><button className="secondaryButton" onClick={() => setFilters(EMPTY_FILTERS)}>放宽条件</button></div>
      )}
      {report && report.results.length > 0 && (
        <div className="resultGroups" aria-label="搜索结果">
          {Object.entries(grouped).map(([group, results]) => results.length > 0 && (
            <section className="resultGroup" key={group}>
              <div className="groupHeading"><h2>{group}</h2><span>{results.length} 项</span></div>
              <div className="resultList">
                {results.map((item) => (
                  <ResultRow
                    key={item.node_id}
                    result={item}
                    focused={inspector?.node_id === item.node_id}
                    selected={selected.has(item.node_id)}
                    inTaskPack={Boolean(useAppStore.getState().activeTaskPack?.items.some((value) => value.node_id === item.node_id))}
                    onInspect={() => inspect(item)}
                    onSelect={(checked) => setSelected((current) => { const next = new Set(current); if (checked) next.add(item.node_id); else next.delete(item.node_id); return next; })}
                    onAdd={() => void addResult(item, query || "资料整理任务")}
                  />
                ))}
              </div>
            </section>
          ))}
        </div>
      )}
    </div>
  );
}

interface ResultRowProps {
  result: SearchResult;
  focused: boolean;
  selected: boolean;
  inTaskPack: boolean;
  onInspect: () => void;
  onSelect: (value: boolean) => void;
  onAdd: () => void;
}

function ResultRow({ result, focused, selected, inTaskPack, onInspect, onSelect, onAdd }: ResultRowProps) {
  const Icon = result.index_type === "foldernode" ? Folder : File;
  return (
    <article className={`resultRow ${focused ? "resultFocused" : ""}`} onClick={onInspect}>
      <label className="resultSelect" onClick={(event) => event.stopPropagation()}>
        <input type="checkbox" checked={selected} onChange={(event) => onSelect(event.target.checked)} aria-label={`选择 ${result.name}`} />
      </label>
      <Icon size={19} className="resultIcon" />
      <button className="resultBody" onClick={onInspect} onFocus={onInspect}>
        <span className="resultTitle"><strong>{result.name}</strong><small>{result.raw_relative_path}</small></span>
        <span className="resultSummary">{result.summary || result.description}</span>
        <span className="resultReason">{result.match_reasons[0] || result.explanation}</span>
      </button>
      <div className="resultMeta"><span>{humanStatus(result.status)}</span>{result.evidence[0] && <span>{result.evidence[0].locator}</span>}</div>
      <button className="iconButton resultAdd" disabled={inTaskPack} aria-label={inTaskPack ? `${result.name} 已在任务包` : `将 ${result.name} 加入任务包`} title={inTaskPack ? "已在任务包" : "加入任务包"} onClick={(event) => { event.stopPropagation(); onAdd(); }}>{inTaskPack ? <Check size={17} /> : <Plus size={17} />}</button>
    </article>
  );
}
