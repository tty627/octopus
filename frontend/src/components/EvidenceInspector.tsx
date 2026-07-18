import { Fragment, useEffect, useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import {
  AlertTriangle,
  Archive,
  Check,
  ChevronLeft,
  ChevronRight,
  ExternalLink,
  FileImage,
  FileSearch,
  LoaderCircle,
  Plus,
  ScanSearch,
  Send,
  X,
} from "lucide-react";
import { api } from "../api";
import { recentActivity } from "../activity";
import { openLocalUri } from "../bridge";
import { useAppStore } from "../store";
import type { SearchResultV2, VisionAnalysis, VisionPreflight, WorkspaceEvidence } from "../types";
import { documentQualityLabel, formatBytes, searchEvidenceText } from "../utils";
import { locatorLabel, sourceKindLabel } from "./researchLabels";

interface EvidenceInspectorProps {
  onAdd: (result: SearchResultV2, evidence: WorkspaceEvidence) => Promise<void>;
  adding: boolean;
  actionError: string;
}

const imageExtensions = new Set([".png", ".jpg", ".jpeg", ".tif", ".tiff", ".webp", ".bmp"]);

export function EvidenceInspector({ onAdd, adding, actionError }: EvidenceInspectorProps) {
  const workspaceId = useAppStore((state) => state.workspaceId);
  const result = useAppStore((state) => state.inspector);
  const open = useAppStore((state) => state.inspectorOpen);
  const close = useAppStore((state) => state.closeInspector);
  const submittedQuery = useAppStore((state) => state.submittedQuery);
  const activeTask = useAppStore((state) => state.activeTask);
  const [page, setPage] = useState<number | null>(null);
  const [selectedEvidenceIndex, setSelectedEvidenceIndex] = useState<number | null>(null);
  const [preview, setPreview] = useState("");
  const [previewState, setPreviewState] = useState<"idle" | "loading" | "ready" | "error">("idle");
  const [previewError, setPreviewError] = useState("");
  const [previewNonce, setPreviewNonce] = useState(0);
  const [openError, setOpenError] = useState("");
  const [opening, setOpening] = useState(false);
  const [visionPreflight, setVisionPreflight] = useState<VisionPreflight | null>(null);
  const [visionAnalysis, setVisionAnalysis] = useState<VisionAnalysis | null>(null);
  const [visionPrompt, setVisionPrompt] = useState("请描述当前页面的关键信息，并指出需要人工核验的细节。");
  const [visionLoading, setVisionLoading] = useState(false);
  const [visionError, setVisionError] = useState("");
  const evidence = useMemo(
    () => result ? [result.best_evidence, ...result.additional_evidence] : [],
    [result],
  );
  const selectedEvidence = selectedEvidenceIndex === null
    ? null
    : evidence[selectedEvidenceIndex] ?? null;
  const selected = Boolean(
    result && selectedEvidence && activeTask?.items.some((item) =>
      item.document_id === result.document_id &&
      item.page_number === selectedEvidence.page_number &&
      item.excerpt === selectedEvidence.excerpt
    ),
  );
  const qualityState = result?.indexing_state === "failed"
    ? "failed"
    : result?.indexing_state === "metadata_only"
      ? "metadata"
      : result?.readability;
  const selectedLocator = selectedEvidence?.locator ?? result?.locator;
  const isImage = Boolean(result && imageExtensions.has(result.extension));
  const visionPage = isImage ? 1 : result?.extension === ".pdf" ? page : null;
  const members = useQuery({
    queryKey: ["document-members", workspaceId, result?.document_id],
    queryFn: () => api.documentMembers(workspaceId, result?.document_id ?? ""),
    enabled: Boolean(result && result.extension === ".zip"),
    retry: false,
  });

  useEffect(() => {
    setPage(result?.best_evidence.locator?.page_number ?? result?.best_evidence.page_number ?? null);
    setSelectedEvidenceIndex(result ? 0 : null);
    setOpenError("");
    setVisionPreflight(null);
    setVisionAnalysis(null);
    setVisionError("");
  }, [result]);

  useEffect(() => {
    setVisionPreflight(null);
    setVisionAnalysis(null);
    setVisionError("");
  }, [page]);

  useEffect(() => {
    const previewController = new AbortController();
    const loadedUrls: string[] = [];
    const keepUrl = (url: string) => {
      if (!previewController.signal.aborted) {
        loadedUrls.push(url);
        return true;
      }
      if (url.startsWith("blob:")) URL.revokeObjectURL(url);
      return false;
    };
    setPreview("");
    setPreviewError("");
    if (!result || result.indexing_state !== "indexed") {
      setPreviewState("idle");
      return () => undefined;
    }
    const isPdfPage = result.extension === ".pdf" && page !== null;
    const loadImage = imageExtensions.has(result.extension)
      ? api.contentUrl(workspaceId, result.document_id)
      : null;
    if (!isPdfPage && !loadImage) {
      setPreviewState("idle");
      return () => undefined;
    }
    setPreviewState("loading");
    void (async () => {
      try {
        if (isPdfPage) {
          const baseUrl = await api.previewUrl(
            workspaceId,
            result.document_id,
            page,
            "",
            "base",
          );
          if (!keepUrl(baseUrl)) return;
          setPreview(baseUrl);
          setPreviewState("ready");
          if (selectedEvidence && submittedQuery.trim()) {
            try {
              const highlightedUrl = await api.previewUrl(
                workspaceId,
                result.document_id,
                page,
                submittedQuery,
                "highlighted",
              );
              if (!keepUrl(highlightedUrl)) return;
              setPreview(highlightedUrl);
            } catch {
              if (previewController.signal.aborted) return;
              setPreviewError("高亮层暂时不可用，已显示基础页面。");
            }
          }
        } else if (loadImage) {
          const imageUrl = await loadImage;
          if (!keepUrl(imageUrl)) return;
          setPreview(imageUrl);
          setPreviewState("ready");
        }
      } catch (reason) {
        if (previewController.signal.aborted) return;
        setPreviewState("error");
        setPreviewError(reason instanceof Error ? reason.message : "页面渲染失败。");
      }
    })();
    return () => {
      previewController.abort();
      loadedUrls.forEach((url) => {
        if (url.startsWith("blob:")) URL.revokeObjectURL(url);
      });
    };
  }, [page, previewNonce, result, selectedEvidence, submittedQuery, workspaceId]);

  const changePage = (delta: number) => {
    if (!result || page === null) return;
    const nextPage = Math.min(result.page_count, Math.max(1, page + delta));
    setPage(nextPage);
    const nextEvidenceIndex = evidence.findIndex((item) =>
      (item.locator?.page_number ?? item.page_number) === nextPage
    );
    setSelectedEvidenceIndex(nextEvidenceIndex >= 0 ? nextEvidenceIndex : null);
  };

  const chooseEvidence = (index: number) => {
    setSelectedEvidenceIndex(index);
    setPage(evidence[index]?.locator?.page_number ?? evidence[index]?.page_number ?? null);
  };

  const openSource = async () => {
    if (!result) return;
    setOpenError("");
    setOpening(true);
    try {
      const target = await api.openTarget(workspaceId, result.document_id);
      await openLocalUri(target.uri);
      recentActivity.recordOpen(result.document_id, result.name, displaySourcePath(result));
    } catch {
      setOpenError("来源当前不可访问，请同步后重新定位。");
    } finally {
      setOpening(false);
    }
  };

  const prepareVision = async () => {
    if (!result || visionPage === null) return;
    setVisionLoading(true);
    setVisionError("");
    setVisionAnalysis(null);
    try {
      setVisionPreflight(await api.visionPreflight(workspaceId, result.document_id, visionPage));
    } catch (reason) {
      setVisionError(reason instanceof Error ? reason.message : "无法准备当前页面。");
    } finally {
      setVisionLoading(false);
    }
  };

  const analyzeVision = async () => {
    if (!result || visionPage === null || !visionPreflight) return;
    setVisionLoading(true);
    setVisionError("");
    try {
      const analysis = await api.analyzeVisionPage(
        workspaceId,
        result.document_id,
        visionPage,
        visionPrompt,
        visionPreflight.requires_confirmation,
      );
      setVisionAnalysis(analysis);
    } catch (reason) {
      setVisionError(reason instanceof Error ? reason.message : "页面分析没有完成。");
    } finally {
      setVisionLoading(false);
    }
  };

  return (
    <aside className={`evidenceInspector ${open ? "inspectorOpen" : ""}`} aria-label="证据检查器">
      <div className="inspectorHeader">
        <div>
          <p className="eyebrow">证据定位</p>
          <h2>{result?.name ?? "选择一份资料"}</h2>
        </div>
        <button className="iconButton closeInspector" onClick={close} aria-label="关闭证据检查器" title="关闭"><X size={18} /></button>
      </div>

      {!result ? (
        <div className="emptyInspector"><FileSearch size={25} /><p>选择搜索结果后，在这里核对来源位置和命中片段。</p></div>
      ) : (
        <div className="inspectorScroll">
          <div className="documentFacts">
            <span className={`qualityBadge quality-${qualityState}`}>
              {documentQualityLabel(result.indexing_state, result.readability)}
            </span>
            <span className="sourceBadge">{sourceKindLabel(result)}</span>
            <span>{formatBytes(result.size_bytes)}</span>
            {result.page_count > 0 && <span>{result.page_count} 页</span>}
          </div>

          {result.extension === ".zip" && (
            <section className="archiveMembers" aria-label="压缩包成员">
              <div className="sectionHeading"><div><Archive size={16} /><h3>压缩包成员</h3></div><span>{members.data?.length ?? 0} 项</span></div>
              {members.isLoading && <p className="mutedLine">正在读取成员清单…</p>}
              {members.data?.map((member) => <div className="archiveMemberRow" key={member.document_id}><strong>{member.name}</strong><small>{member.relative_path}</small></div>)}
              {!members.isLoading && members.data?.length === 0 && <p className="mutedLine">未能读取成员清单，仍可按文件名搜索压缩包。</p>}
            </section>
          )}

          {result.indexing_state === "metadata_only" ? (
            <div className="textEvidencePreview"><FileSearch size={22} /><span>当前仅提供文件名、路径和元数据检索。</span></div>
          ) : result.indexing_state === "failed" ? (
            <div className="unlocatedNotice"><AlertTriangle size={17} />当前文件处理失败，可按文件名查找。</div>
          ) : result.extension === ".pdf" && page !== null ? (
            <section className="pageViewer" aria-label="PDF 页面预览">
              <div className="pageToolbar">
                <button className="iconButton" disabled={page <= 1} onClick={() => changePage(-1)} aria-label="上一页" title="上一页"><ChevronLeft size={18} /></button>
                <span>第 {page} / {result.page_count} 页</span>
                <button className="iconButton" disabled={page >= result.page_count} onClick={() => changePage(1)} aria-label="下一页" title="下一页"><ChevronRight size={18} /></button>
              </div>
              <PreviewCanvas preview={preview} state={previewState} alt={`${result.name} 第 ${page} 页`} />
              {previewError && previewState === "ready" && <div className="previewNotice" role="status">{previewError}</div>}
              {previewState === "error" && (
                <div className="previewFallback" role="alert">
                  <span>{previewError || "页面预览暂时不可用。"}</span>
                  <button className="secondaryButton smallButton" onClick={() => setPreviewNonce((value) => value + 1)}>重试预览</button>
                  <button className="textButton smallButton" onClick={() => document.getElementById("evidence-text")?.scrollIntoView({ block: "center" })}>查看文本证据</button>
                  <button className="textButton smallButton" onClick={() => void openSource()}>打开原文件</button>
                </div>
              )}
            </section>
          ) : result.extension === ".pdf" ? (
            <div className="unlocatedNotice"><AlertTriangle size={17} />当前命中无法可靠定位到页码。</div>
          ) : isImage ? (
            <section className="imageViewer" aria-label="图片内容预览">
              <PreviewCanvas preview={preview} state={previewState} alt={`${result.name} 内容预览`} />
              <span><FileImage size={15} />OCR 命中位置：{locatorLabel(selectedLocator, selectedEvidence?.page_number ?? null) || "整张图片"}</span>
            </section>
          ) : (
            <div className="textEvidencePreview">
              {result.source_ref?.kind === "archive_member" ? <Archive size={22} /> : <FileSearch size={22} />}
              <span>正文位置：{locatorLabel(selectedLocator, selectedEvidence?.page_number ?? null) || "文档内容"}</span>
            </div>
          )}

          {visionPage !== null && result.indexing_state === "indexed" && (
            <section className="visionAnalysis" aria-label="当前页视觉分析">
              <div className="sectionHeading">
                <div><ScanSearch size={16} /><h3>当前页分析</h3></div>
                {visionPreflight && <span>{visionPreflight.mode === "vision" ? "视觉" : "OCR"}</span>}
              </div>
              {!visionPreflight ? (
                <button className="secondaryButton smallButton" disabled={visionLoading} onClick={() => void prepareVision()}>
                  {visionLoading ? <LoaderCircle className="spin" size={15} /> : <ScanSearch size={15} />}准备分析
                </button>
              ) : (
                <>
                  <div className="visionFacts">
                    <span><strong>模型</strong>{visionPreflight.model}</span>
                    <span><strong>图片</strong>{visionPreflight.width} × {visionPreflight.height} · {formatBytes(visionPreflight.image_size_bytes)}</span>
                    <span><strong>费用</strong>{visionPreflight.cost_estimate_status === "unknown" ? "未知" : "按实际 token 统计"}</span>
                  </div>
                  {visionPreflight.warning && <div className="previewNotice" role="status">{visionPreflight.warning}</div>}
                  <textarea aria-label="当前页分析问题" value={visionPrompt} maxLength={2000} onChange={(event) => setVisionPrompt(event.target.value)} />
                  <div className="visionActions">
                    <button className="textButton smallButton" disabled={visionLoading} onClick={() => void prepareVision()}>重新准备</button>
                    <button className="primaryButton smallButton" disabled={visionLoading || !visionPrompt.trim()} onClick={() => void analyzeVision()}>
                      {visionLoading ? <LoaderCircle className="spin" size={15} /> : <Send size={15} />}
                      {visionPreflight.requires_confirmation ? "确认发送并分析" : "使用 OCR 文本"}
                    </button>
                  </div>
                </>
              )}
              {visionError && <div className="inspectorActionError" role="alert"><AlertTriangle size={15} />{visionError}</div>}
              {visionAnalysis && (
                <div className="visionAnswer">
                  <strong>{visionAnalysis.mode === "vision" ? "模型分析" : "OCR 回退"}</strong>
                  <p>{visionAnalysis.answer}</p>
                  {visionAnalysis.warning && <small>{visionAnalysis.warning}</small>}
                </div>
              )}
            </section>
          )}

          <section className="inspectorSection" id="evidence-text">
            <h3>命中内容</h3>
            {selectedEvidence ? (
              <>
                <p className="evidenceReason">
                  {selectedEvidence.reason}
                  {locatorLabel(selectedLocator, selectedEvidence.page_number) ? ` · ${locatorLabel(selectedLocator, selectedEvidence.page_number)}` : ""}
                  {selectedEvidence.heading ? ` · ${selectedEvidence.heading}` : ""}
                </p>
                <p className={result.indexing_state === "indexed" && result.readability === "low" ? "evidenceExcerpt lowExcerpt" : "evidenceExcerpt"}>
                  {result.indexing_state === "indexed" && result.readability !== "low"
                    ? <HighlightedText text={selectedEvidence.excerpt} query={submittedQuery} />
                    : searchEvidenceText(result.indexing_state, result.readability, selectedEvidence.excerpt)}
                </p>
              </>
            ) : (
              <>
                <p className="evidenceReason">当前页没有搜索命中</p>
                <p className="evidenceExcerpt">正在查看相邻原始内容，不会把其他位置的命中作为这里的证据。</p>
              </>
            )}
            {evidence.length > 1 && (
              <div className="evidenceChoices">
                {evidence.map((item, index) => (
                  <button key={`${item.page_number}-${index}`} className={selectedEvidenceIndex === index ? "evidenceActive" : ""} onClick={() => chooseEvidence(index)}>
                    {locatorLabel(item.locator ?? result.locator, item.page_number) || "文档信息"}
                  </button>
                ))}
              </div>
            )}
          </section>

          <section className="inspectorSection sourceMeta">
            <h3>来源</h3>
            <div className="sourceIdentity"><Archive size={16} /><strong>{sourceKindLabel(result)}</strong></div>
            <p>{displaySourcePath(result)}</p>
            <span>{new Date(result.modified_at).toLocaleString("zh-CN")}</span>
            {(result.quality_flags?.length ?? 0) > 0 && <small>提醒：{result.quality_flags?.join("、")}</small>}
          </section>
        </div>
      )}

      {result && (actionError || openError) && <div className="inspectorActionError" role="alert"><AlertTriangle size={16} />{actionError || openError}</div>}
      {result && (
        <div className="inspectorActions">
          <button className="secondaryButton" disabled={opening} onClick={() => void openSource()}>
            {opening ? <LoaderCircle className="spin" size={17} /> : <ExternalLink size={17} />}打开来源
          </button>
          <button className="primaryButton" disabled={selected || adding || !selectedEvidence} onClick={() => { if (selectedEvidence) void onAdd(result, selectedEvidence); }}>
            {selected ? <Check size={17} /> : adding ? <LoaderCircle className="spin" size={17} /> : <Plus size={17} />}
            {selected ? "已加入资料包" : adding ? "加入中" : selectedEvidence ? "加入资料包" : "当前页无命中"}
          </button>
        </div>
      )}
    </aside>
  );
}

function PreviewCanvas({ preview, state, alt }: { preview: string; state: "idle" | "loading" | "ready" | "error"; alt: string }) {
  return (
    <div className="pageCanvas">
      {state === "loading" && <div className="previewState"><LoaderCircle className="spin" size={22} />正在载入内容</div>}
      {state === "error" && <div className="previewState"><AlertTriangle size={22} />内容预览不可用</div>}
      {preview && <img src={preview} alt={alt} />}
    </div>
  );
}

function displaySourcePath(result: SearchResultV2): string {
  return result.source_ref?.virtual_path || result.relative_path;
}

function HighlightedText({ text, query }: { text: string; query: string }) {
  const value = query.trim();
  if (!value) return text;
  const index = text.toLocaleLowerCase().indexOf(value.toLocaleLowerCase());
  if (index < 0) return text;
  return (
    <Fragment>
      {text.slice(0, index)}
      <mark>{text.slice(index, index + value.length)}</mark>
      {text.slice(index + value.length)}
    </Fragment>
  );
}
