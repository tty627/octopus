import { Fragment, useEffect, useMemo, useState } from "react";
import {
  AlertTriangle,
  Check,
  ChevronLeft,
  ChevronRight,
  ExternalLink,
  FileSearch,
  LoaderCircle,
  Plus,
  X,
} from "lucide-react";
import { api } from "../api";
import { openLocalUri } from "../bridge";
import { useAppStore } from "../store";
import type { SearchResultV2 } from "../types";
import { formatBytes, readabilityLabel } from "../utils";

interface EvidenceInspectorProps {
  onAdd: (result: SearchResultV2) => Promise<void>;
}

export function EvidenceInspector({ onAdd }: EvidenceInspectorProps) {
  const workspaceId = useAppStore((state) => state.workspaceId);
  const result = useAppStore((state) => state.inspector);
  const open = useAppStore((state) => state.inspectorOpen);
  const close = useAppStore((state) => state.closeInspector);
  const query = useAppStore((state) => state.query);
  const activeTask = useAppStore((state) => state.activeTask);
  const [page, setPage] = useState<number | null>(null);
  const [preview, setPreview] = useState("");
  const [previewState, setPreviewState] = useState<"idle" | "loading" | "ready" | "error">("idle");
  const evidence = useMemo(
    () => result ? [result.best_evidence, ...result.additional_evidence] : [],
    [result],
  );
  const selectedEvidence = evidence.find((item) => item.page_number === page) ?? result?.best_evidence;
  const selected = Boolean(
    result && activeTask?.items.some((item) =>
      item.document_id === result.document_id &&
      item.page_number === result.best_evidence.page_number
    ),
  );

  useEffect(() => {
    setPage(result?.best_evidence.page_number ?? null);
  }, [result]);

  useEffect(() => {
    let active = true;
    let nextUrl = "";
    setPreview("");
    if (!result || result.extension !== ".pdf" || page === null) {
      setPreviewState("idle");
      return () => undefined;
    }
    setPreviewState("loading");
    void api.previewUrl(workspaceId, result.document_id, page).then((url) => {
      nextUrl = url;
      if (!active) {
        if (url.startsWith("blob:")) URL.revokeObjectURL(url);
        return;
      }
      setPreview(url);
      setPreviewState("ready");
    }).catch(() => {
      if (active) setPreviewState("error");
    });
    return () => {
      active = false;
      if (nextUrl.startsWith("blob:")) URL.revokeObjectURL(nextUrl);
    };
  }, [page, result, workspaceId]);

  const changePage = (delta: number) => {
    if (!result || page === null) return;
    setPage(Math.min(result.page_count, Math.max(1, page + delta)));
  };

  return (
    <aside className={`evidenceInspector ${open ? "inspectorOpen" : ""}`} aria-label="页面证据检查器">
      <div className="inspectorHeader">
        <div>
          <p className="eyebrow">页面证据</p>
          <h2>{result?.name ?? "选择一份资料"}</h2>
        </div>
        <button className="iconButton closeInspector" onClick={close} aria-label="关闭页面证据检查器" title="关闭"><X size={18} /></button>
      </div>

      {!result ? (
        <div className="emptyInspector"><FileSearch size={25} /><p>选择搜索结果后，在这里核对原始页面和命中片段。</p></div>
      ) : (
        <div className="inspectorScroll">
          <div className="documentFacts">
            <span className={`qualityBadge quality-${result.readability}`}>{readabilityLabel(result.readability)}</span>
            <span>{formatBytes(result.size_bytes)}</span>
            {result.page_count > 0 && <span>{result.page_count} 页</span>}
          </div>

          {result.extension === ".pdf" && page !== null ? (
            <section className="pageViewer" aria-label="PDF 页面预览">
              <div className="pageToolbar">
                <button className="iconButton" disabled={page <= 1} onClick={() => changePage(-1)} aria-label="上一页" title="上一页"><ChevronLeft size={18} /></button>
                <span>第 {page} / {result.page_count} 页</span>
                <button className="iconButton" disabled={page >= result.page_count} onClick={() => changePage(1)} aria-label="下一页" title="下一页"><ChevronRight size={18} /></button>
              </div>
              <div className="pageCanvas">
                {previewState === "loading" && <div className="previewState"><LoaderCircle className="spin" size={22} />正在载入页面</div>}
                {previewState === "error" && <div className="previewState"><AlertTriangle size={22} />页面预览不可用</div>}
                {preview && <img src={preview} alt={`${result.name} 第 ${page} 页`} />}
              </div>
            </section>
          ) : result.extension === ".pdf" ? (
            <div className="unlocatedNotice"><AlertTriangle size={17} />当前命中无法可靠定位到页码。</div>
          ) : (
            <div className="textEvidencePreview"><FileSearch size={22} /><span>文本证据已从原文件中定位。</span></div>
          )}

          <section className="inspectorSection">
            <h3>命中内容</h3>
            <p className="evidenceReason">
              {selectedEvidence?.reason}
              {selectedEvidence?.heading ? ` · ${selectedEvidence.heading}` : ""}
            </p>
            <p className={result.readability === "low" ? "evidenceExcerpt lowExcerpt" : "evidenceExcerpt"}>
              {result.readability === "low"
                ? "正文识别质量较低，可按文件名查找。"
                : <HighlightedText text={selectedEvidence?.excerpt ?? ""} query={query} />}
            </p>
            {evidence.length > 1 && (
              <div className="evidenceChoices">
                {evidence.map((item, index) => (
                  <button key={`${item.page_number}-${index}`} className={item.page_number === page ? "evidenceActive" : ""} onClick={() => setPage(item.page_number)}>
                    {item.page_number ? `第 ${item.page_number} 页` : "文档信息"}
                  </button>
                ))}
              </div>
            )}
          </section>

          <section className="inspectorSection sourceMeta">
            <h3>来源</h3>
            <p>{result.relative_path}</p>
            <span>{new Date(result.modified_at).toLocaleString("zh-CN")}</span>
          </section>
        </div>
      )}

      {result && (
        <div className="inspectorActions">
          <button className="secondaryButton" onClick={() => void openLocalUri(result.source_uri)}><ExternalLink size={17} />打开原文件</button>
          <button className="primaryButton" disabled={selected} onClick={() => void onAdd(result)}>{selected ? <Check size={17} /> : <Plus size={17} />}{selected ? "已加入任务" : "加入任务"}</button>
        </div>
      )}
    </aside>
  );
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
