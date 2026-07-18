import { useEffect, useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import {
  AlertTriangle,
  Archive,
  Check,
  CheckSquare2,
  FileText,
  Filter,
  LoaderCircle,
  Plus,
  Search,
  Square,
  SlidersHorizontal,
  Sparkles,
  X,
} from "lucide-react";
import { ApiError, api } from "../api";
import { recentActivity } from "../activity";
import { waitForJob } from "../jobs";
import { EMPTY_FILTERS, useAppStore } from "../store";
import type {
  SearchReportV2,
  SearchResultV2,
  ServiceJob,
  SourceKind,
  WorkspaceEvidence,
  WorkspaceResearchResult,
} from "../types";
import { documentQualityLabel, formatBytes, searchEvidenceText } from "../utils";
import { locatorLabel, sourceKindLabel } from "./researchLabels";

interface SearchWorkspaceProps {
  addResult: (
    result: SearchResultV2,
    evidence: WorkspaceEvidence,
    defaultTitle?: string,
    sourceWorkspaceId?: string,
  ) => Promise<void>;
  adding: boolean;
  actionError: string;
  clearActionError: () => void;
}

const extensionOptions = [
  { label: "PDF", values: [".pdf"] },
  { label: "Office", values: [".docx", ".xlsx", ".xlsm", ".pptx"] },
  { label: "图片", values: [".png", ".jpg", ".jpeg", ".tif", ".tiff", ".webp", ".bmp"] },
  { label: "ZIP", values: [".zip"] },
  { label: "文本", values: [".txt", ".md", ".rst"] },
];

const sourceKindOptions: Array<{ label: string; value: SourceKind }> = [
  { label: "普通文件", value: "physical" },
  { label: "压缩包", value: "archive" },
  { label: "ZIP 内文件", value: "archive_member" },
];

export function SearchWorkspace({
  addResult,
  adding,
  actionError,
  clearActionError,
}: SearchWorkspaceProps) {
  const workspaceId = useAppStore((state) => state.workspaceId);
  const query = useAppStore((state) => state.query);
  const setQuery = useAppStore((state) => state.setQuery);
  const setSubmittedQuery = useAppStore((state) => state.setSubmittedQuery);
  const searchRequested = useAppStore((state) => state.searchRequested);
  const consumeSearchRequest = useAppStore((state) => state.consumeSearchRequest);
  const filters = useAppStore((state) => state.filters);
  const setFilters = useAppStore((state) => state.setFilters);
  const inspector = useAppStore((state) => state.inspector);
  const inspect = useAppStore((state) => state.inspect);
  const activeTask = useAppStore((state) => state.activeTask);
  const [report, setReport] = useState<SearchReportV2 | null>(null);
  const [searchMode, setSearchMode] = useState<"lookup" | "research">("lookup");
  const [researchResult, setResearchResult] = useState<WorkspaceResearchResult | null>(null);
  const [researchPhase, setResearchPhase] = useState("");
  const [activeResearchJob, setActiveResearchJob] = useState<ServiceJob | null>(null);
  const [reportWorkspaceId, setReportWorkspaceId] = useState("");
  const [stage, setStage] = useState<"idle" | "searching" | "ready" | "degraded">("idle");
  const [filterOpen, setFilterOpen] = useState(false);
  const [error, setError] = useState("");
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set());
  const sequence = useRef(0);
  const controller = useRef<AbortController | null>(null);
  const queryClient = useQueryClient();

  useEffect(() => () => controller.current?.abort(), []);
  useEffect(() => {
    sequence.current += 1;
    controller.current?.abort();
    controller.current = null;
    setReport(null);
    setResearchResult(null);
    setResearchPhase("");
    setActiveResearchJob(null);
    setReportWorkspaceId("");
    setStage("idle");
    setError("");
    setFilterOpen(false);
    setSelectedIds(new Set());
    setSubmittedQuery("");
    setFilters(EMPTY_FILTERS);
    clearActionError();
    inspect(null);
  }, [clearActionError, inspect, setFilters, setSubmittedQuery, workspaceId]);
  const runSearch = async () => {
    const value = query.trim();
    if (!workspaceId || !value) return;
    const requestedWorkspaceId = workspaceId;
    sequence.current += 1;
    const currentSequence = sequence.current;
    controller.current?.abort();
    const current = new AbortController();
    controller.current = current;
    setStage("searching");
    setError("");
    setResearchResult(null);
    try {
      let result: SearchReportV2;
      if (searchMode === "research") {
        const started = await api.startResearch(requestedWorkspaceId, value, filters);
        setActiveResearchJob(started);
        queryClient.setQueryData<ServiceJob[]>(["jobs", requestedWorkspaceId], (jobs = []) => [
          started,
          ...jobs.filter((item) => item.job_id !== started.job_id),
        ]);
        const completed = await waitForJob(started, {
          signal: current.signal,
          onUpdate: (job) => {
            setActiveResearchJob(job);
            setResearchPhase(researchPhaseLabel(job.result.progress?.phase));
            queryClient.setQueryData<ServiceJob[]>(
              ["jobs", requestedWorkspaceId],
              (jobs = []) => [job, ...jobs.filter((item) => item.job_id !== job.job_id)],
            );
          },
        });
        const research = api.researchResult(completed);
        setResearchResult(research);
        result = {
          query: research.query,
          requested_mode: "assisted",
          actual_mode: research.actual_mode === "degraded" ? "degraded" : "assisted",
          degradation_reason: research.degradation_reason,
          answer: research.answer,
          results: research.results,
          candidate_count: research.results.length,
          duration_ms: research.duration_ms ?? 0,
        };
      } else {
        result = await api.search(
          requestedWorkspaceId,
          value,
          "local",
          filters,
          current.signal,
        );
      }
      if (
        currentSequence !== sequence.current ||
        useAppStore.getState().workspaceId !== requestedWorkspaceId
      ) return;
      setReport(result);
      setSelectedIds(new Set());
      setReportWorkspaceId(requestedWorkspaceId);
      setSubmittedQuery(result.query);
      recentActivity.recordSearch(result.query);
      setStage(result.actual_mode === "degraded" ? "degraded" : "ready");
      inspect(result.results[0] ?? null);
    } catch (reason) {
      if (reason instanceof Error && reason.name === "AbortError") return;
      if (currentSequence !== sequence.current) return;
      setStage("idle");
      setError(reason instanceof ApiError ? reason.message : "搜索没有完成，请重试。 ");
    } finally {
      if (currentSequence === sequence.current) setActiveResearchJob(null);
    }
  };
  const runSearchRef = useRef(runSearch);
  runSearchRef.current = runSearch;
  useEffect(() => {
    if (!searchRequested) return;
    consumeSearchRequest();
    void runSearchRef.current();
  }, [consumeSearchRequest, searchRequested]);

  const cancelResearch = async () => {
    if (!activeResearchJob) return;
    controller.current?.abort();
    try {
      await api.cancelJob(activeResearchJob.repository_id, activeResearchJob.job_id);
      setStage("idle");
      setResearchPhase("");
      setError("研究任务已取消，本地资料和已有结果均未改变。");
    } catch (reason) {
      setError(reason instanceof ApiError ? reason.message : "研究任务暂时无法取消。");
    }
  };

  const selectCitation = (citationId: string) => {
    const citation = researchResult?.citations.find((item) => item.citation_id === citationId);
    const result = report?.results.find((item) => item.document_id === citation?.document_id);
    if (result) inspect(result);
  };

  const toggleExtensionGroup = (values: string[]) => {
    const active = values.some((value) => filters.extensions.includes(value));
    setFilters({
      ...filters,
      extensions: active
        ? filters.extensions.filter((value) => !values.includes(value))
        : [...new Set([...filters.extensions, ...values])],
    });
  };

  const toggleSourceKind = (value: SourceKind) => {
    const currentKinds = filters.source_kinds ?? [];
    setFilters({
      ...filters,
      source_kinds: currentKinds.includes(value)
        ? currentKinds.filter((item) => item !== value)
        : [...currentKinds, value],
    });
  };

  const visibleResults = reportWorkspaceId === workspaceId ? report?.results ?? [] : [];
  const selectedResults = visibleResults.filter((result) => selectedIds.has(result.document_id));
  const toggleResult = (documentId: string) => setSelectedIds((current) => {
    const next = new Set(current);
    if (next.has(documentId)) next.delete(documentId);
    else next.add(documentId);
    return next;
  });
  const toggleAll = () => setSelectedIds(
    selectedResults.length === visibleResults.length && visibleResults.length > 0
      ? new Set()
      : new Set(visibleResults.map((result) => result.document_id)),
  );
  const addSelected = async () => {
    for (const result of selectedResults) {
      await addResult(result, result.best_evidence, report?.query || "研究资料包", workspaceId);
    }
    setSelectedIds(new Set());
  };

  return (
    <div className="searchPage">
      <header className="searchLead">
        <div>
          <h1>{searchMode === "lookup" ? "查找原始资料" : "研究本地问题"}</h1>
          <p>{searchMode === "lookup" ? "按文件名、章节和正文定位可核验的页面证据。" : "拆解问题并仅使用当前资料空间生成带引用的回答。"}</p>
        </div>
        <div className="searchModeSwitch" role="group" aria-label="搜索模式">
          <button className={searchMode === "lookup" ? "modeActive" : ""} onClick={() => setSearchMode("lookup")}><Search size={15} />查资料</button>
          <button className={searchMode === "research" ? "modeActive" : ""} onClick={() => setSearchMode("research")}><Sparkles size={15} />研究问题</button>
        </div>
      </header>

      <form className="primarySearch" onSubmit={(event) => { event.preventDefault(); void runSearch(); }}>
        <Search size={21} />
        <input
          id="workspace-search"
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          placeholder={searchMode === "lookup" ? "输入文件名、章节或正文，例如：微分方程" : "输入需要用本地资料回答的问题"}
          aria-label="搜索原始资料"
          autoFocus
        />
        {query && (
          <button type="button" className="iconButton" onClick={() => setQuery("")} aria-label="清空搜索" title="清空">
            <X size={17} />
          </button>
        )}
        <button className="primaryButton" type="submit" disabled={!query.trim() || stage === "searching"}>
          {stage === "searching" ? <LoaderCircle className="spin" size={17} /> : searchMode === "research" ? <Sparkles size={17} /> : <Search size={17} />}
          {searchMode === "research" ? "开始研究" : "搜索"}
        </button>
      </form>

      <div className="searchControls">
        <button className={`filterButton ${filterOpen ? "filterActive" : ""}`} onClick={() => setFilterOpen((value) => !value)}>
          <SlidersHorizontal size={16} />筛选
        </button>
        {(filters.path_prefix || filters.extensions.length > 0 || (filters.source_kinds?.length ?? 0) > 0) && (
          <button className="textButton smallButton" onClick={() => setFilters(EMPTY_FILTERS)}>清除</button>
        )}
        {visibleResults.length > 0 && (
          <div className="bulkSearchActions">
            <button className="textButton smallButton" onClick={toggleAll}>
              {selectedResults.length === visibleResults.length ? <CheckSquare2 size={15} /> : <Square size={15} />}
              {selectedResults.length === visibleResults.length ? "取消全选" : "全选"}
            </button>
            <button className="secondaryButton smallButton" disabled={selectedResults.length === 0 || adding} onClick={() => void addSelected()}>
              {adding ? <LoaderCircle className="spin" size={15} /> : <Plus size={15} />}
              加入资料包{selectedResults.length > 0 ? ` (${selectedResults.length})` : ""}
            </button>
          </div>
        )}
        <span className="searchStatus" aria-live="polite">
          {stage === "searching" && "正在检索本地资料…"}
          {stage === "ready" && report && `找到 ${report.results.length} 份资料 · ${report.duration_ms} ms`}
          {stage === "degraded" && report && `找到 ${report.results.length} 份资料 · 本次使用本地检索`}
        </span>
        {stage === "searching" && searchMode === "research" && (
          <button className="textButton smallButton" onClick={() => void cancelResearch()}>取消</button>
        )}
      </div>

      {filterOpen && (
        <div className="filterPanel">
          <label>
            <span><Filter size={15} />文件类型</span>
            <span className="filterChoices">
              {extensionOptions.map((option) => {
                const active = option.values.some((value) => filters.extensions.includes(value));
                return <button key={option.label} className={active ? "choiceActive" : ""} onClick={() => toggleExtensionGroup(option.values)}>{active && <Check size={14} />}{option.label}</button>;
              })}
            </span>
          </label>
          <label>
            <span>文件夹范围</span>
            <input value={filters.path_prefix} onChange={(event) => setFilters({ ...filters, path_prefix: event.target.value })} placeholder="例如：第六章/" />
          </label>
          <label className="wideFilter">
            <span><Archive size={15} />来源位置</span>
            <span className="filterChoices">
              {sourceKindOptions.map((option) => {
                const active = filters.source_kinds?.includes(option.value) ?? false;
                return <button key={option.value} className={active ? "choiceActive" : ""} onClick={() => toggleSourceKind(option.value)}>{active && <Check size={14} />}{option.label}</button>;
              })}
            </span>
          </label>
        </div>
      )}

      {error && (
        <div className="pageError" role="alert">
          <AlertTriangle size={19} />
          <span>{error}</span>
          <button className="secondaryButton smallButton" onClick={() => void runSearch()}>重试</button>
        </div>
      )}

      {actionError && (
        <div className="pageError" role="alert">
          <AlertTriangle size={19} />
          <span>{actionError}</span>
          <button className="iconButton" onClick={clearActionError} aria-label="关闭加入任务错误" title="关闭"><X size={17} /></button>
        </div>
      )}

      {report?.answer && reportWorkspaceId === workspaceId && (
        <section className="researchAnswer" aria-label="研究回答">
          <div className="researchAnswerTitle"><Sparkles size={17} /><strong>{searchMode === "research" ? "研究结论" : "检索摘要"}</strong>{researchPhase && stage === "searching" && <span>{researchPhase}</span>}</div>
          <p><CitationAnswer answer={report.answer} onCitation={selectCitation} /></p>
          {researchResult?.warnings.map((warning) => <div className="warningBox" role="status" key={warning}>{warning}</div>)}
          {stage === "degraded" && <small>辅助模型不可用，本次回答保留本地检索结果。{report.degradation_reason ? ` 原因：${report.degradation_reason}` : ""}</small>}
        </section>
      )}

      {!report && !error && stage === "idle" && (
        <div className="searchStart">
          <FileText size={27} />
          <h2>从文件名开始，也可以直接搜正文</h2>
          <div className="suggestionList">
            {["微分方程", "级数", "一阶线性方程"].map((value) => (
              <button key={value} onClick={() => setQuery(value)}>{value}</button>
            ))}
          </div>
        </div>
      )}

      {report && reportWorkspaceId === workspaceId && report.results.length === 0 && (
        <div className="searchStart">
          <Search size={27} />
          <h2>没有找到匹配资料</h2>
          <p>尝试使用文件名片段，或清除文件夹和类型筛选。</p>
        </div>
      )}

      {report && reportWorkspaceId === workspaceId && report.results.length > 0 && (
        <section className="resultsSection" aria-label="搜索结果">
          <div className="resultList">
            {report.results.map((result) => (
              <ResultRow
                key={result.document_id}
                result={result}
                focused={inspector?.document_id === result.document_id}
                checked={selectedIds.has(result.document_id)}
                inTask={Boolean(activeTask?.items.some((item) =>
                  item.document_id === result.document_id &&
                  item.page_number === result.best_evidence.page_number &&
                  item.excerpt === result.best_evidence.excerpt
                ))}
                onInspect={() => inspect(result)}
                onToggle={() => toggleResult(result.document_id)}
                adding={adding}
                onAdd={() => void addResult(result, result.best_evidence, report.query || "资料核对任务", workspaceId)}
              />
            ))}
          </div>
        </section>
      )}
    </div>
  );
}

function researchPhaseLabel(phase?: string): string {
  if (phase === "understanding") return "理解问题";
  if (phase === "retrieving") return "检索证据";
  if (phase === "composing") return "组织回答";
  if (phase === "completed") return "完成";
  return "准备研究";
}

function CitationAnswer({
  answer,
  onCitation,
}: {
  answer: string;
  onCitation: (citationId: string) => void;
}) {
  return <>{answer.split(/(\[R\d+\])/g).map((part, index) => {
    const match = part.match(/^\[(R\d+)\]$/);
    return match
      ? <button className="citationLink" key={`${part}-${index}`} onClick={() => onCitation(match[1] ?? "")}>{part}</button>
      : part;
  })}</>;
}

function ResultRow({
  result,
  focused,
  checked,
  inTask,
  adding,
  onInspect,
  onToggle,
  onAdd,
}: {
  result: SearchResultV2;
  focused: boolean;
  checked: boolean;
  inTask: boolean;
  adding: boolean;
  onInspect: () => void;
  onToggle: () => void;
  onAdd: () => void;
}) {
  const evidence = result.best_evidence;
  const qualityState = result.indexing_state === "failed"
    ? "failed"
    : result.indexing_state === "metadata_only"
      ? "metadata"
      : result.readability;
  const lowQualityExcerpt = result.indexing_state === "indexed" && result.readability === "low";
  return (
    <article className={`resultRow ${focused ? "resultFocused" : ""}`} onClick={onInspect}>
      <label className="resultSelect" onClick={(event) => event.stopPropagation()}>
        <input type="checkbox" checked={checked} onChange={onToggle} aria-label={`选择 ${result.name}`} />
      </label>
      <button className="resultBody" onClick={onInspect} onFocus={onInspect} aria-label={result.name}>
        <span className="resultTitle">
          <strong>{result.name}</strong>
          <small>{result.relative_path}</small>
        </span>
        <span className="resultReason">
          {evidence.reason}
          {locatorLabel(evidence.locator ?? result.locator, evidence.page_number) ? ` · ${locatorLabel(evidence.locator ?? result.locator, evidence.page_number)}` : ""}
          {evidence.heading ? ` · ${evidence.heading}` : ""}
        </span>
        <span className={lowQualityExcerpt ? "resultExcerpt lowExcerpt" : "resultExcerpt"}>
          {searchEvidenceText(result.indexing_state, result.readability, evidence.excerpt)}
        </span>
        {result.additional_evidence.length > 0 && (
          <span className="moreEvidence">另有 {result.additional_evidence.length} 处命中</span>
        )}
      </button>
      <div className="resultMeta">
        <span className="sourceBadge">{sourceKindLabel(result)}</span>
        <span className="formatBadge">{formatLabel(result.extension)}</span>
        <span className={`qualityBadge quality-${qualityState}`}>
          {documentQualityLabel(result.indexing_state, result.readability)}
        </span>
        <span>{formatBytes(result.size_bytes)}</span>
      </div>
      <button className="iconButton resultAdd" disabled={inTask || adding} onClick={(event) => { event.stopPropagation(); onAdd(); }} aria-label={inTask ? `${result.name} 已加入资料包` : `将 ${result.name} 加入资料包`} title={inTask ? "已加入" : adding ? "正在加入" : "加入资料包"}>
        {inTask ? <Check size={17} /> : adding ? <LoaderCircle className="spin" size={17} /> : <Plus size={17} />}
      </button>
    </article>
  );
}

function formatLabel(extension: string): string {
  if ([".docx", ".xlsx", ".xlsm", ".pptx"].includes(extension)) return "Office 正文";
  if ([".png", ".jpg", ".jpeg", ".tif", ".tiff", ".webp", ".bmp"].includes(extension)) return "图片 OCR";
  if (extension === ".zip") return "ZIP";
  return extension.replace(".", "").toUpperCase() || "文件";
}
