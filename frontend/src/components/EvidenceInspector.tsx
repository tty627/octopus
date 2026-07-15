import { AlertTriangle, ExternalLink, FileSearch, FolderOpen, Plus, X } from "lucide-react";
import { openLocalUri } from "../bridge";
import { useAppStore } from "../store";
import type { SearchResult } from "../types";
import { formatBytes, humanStatus } from "../utils";

interface EvidenceInspectorProps {
  onAdd: (result: SearchResult) => Promise<void>;
}

export function EvidenceInspector({ onAdd }: EvidenceInspectorProps) {
  const result = useAppStore((state) => state.inspector);
  const open = useAppStore((state) => state.inspectorOpen);
  const close = useAppStore((state) => state.closeInspector);
  const activeTaskPack = useAppStore((state) => state.activeTaskPack);
  const selected = Boolean(result && activeTaskPack?.items.some((item) => item.node_id === result.node_id));

  return (
    <aside className={`evidenceInspector ${open ? "inspectorOpen" : ""}`} aria-label="证据检查器">
      <div className="inspectorHeader">
        <div><p className="eyebrow">当前证据</p><h2>{result ? result.name : "选择一项资料"}</h2></div>
        <button className="iconButton closeInspector" aria-label="关闭证据检查器" title="关闭" onClick={close}><X size={18} /></button>
      </div>
      {!result ? (
        <div className="emptyInspector"><FileSearch size={24} /><p>单击搜索结果，在这里核对摘要、命中原因和证据位置。</p></div>
      ) : (
        <div className="inspectorScroll">
          <section className="inspectorSection">
            <div className="statusLine"><span className="statusText">{humanStatus(result.status)}</span><span>{formatBytes(result.size_bytes)}</span></div>
            <p className="summaryText">{result.summary || result.description || "索引中暂无摘要。"}</p>
          </section>
          <section className="inspectorSection">
            <h3>为什么出现</h3>
            <ul className="reasonList">
              {(result.match_reasons.length ? result.match_reasons : [result.explanation]).filter(Boolean).map((reason) => <li key={reason}>{reason}</li>)}
            </ul>
            {result.explanation.startsWith("AI 建议") && <div className="aiNotice">AI 建议 · 已基于当前候选排序</div>}
          </section>
          <section className="inspectorSection">
            <h3>证据位置</h3>
            {result.evidence.length === 0 ? <p className="mutedText">未找到可直接定位的结构锚点，打开文件后请使用摘要核对。</p> : (
              <div className="anchorList">
                {result.evidence.map((anchor) => (
                  <button className="anchorRow" key={`${anchor.locator}-${anchor.kind}`} onClick={() => void openLocalUri(result.open_target_uri)}>
                    <span>{anchor.locator}</span><small>{anchor.text_excerpt || anchor.kind}</small>
                  </button>
                ))}
              </div>
            )}
          </section>
          {(result.quality_flags.length > 0 || result.risk_flags.length > 0) && (
            <section className="inspectorSection warningSection"><h3><AlertTriangle size={16} />需要核验</h3><p>该资料存在 { [...result.quality_flags, ...result.risk_flags].join("、") }。加入后会放入“待核验”槽位。</p></section>
          )}
          <section className="inspectorSection sourceMeta">
            <h3>来源</h3><p>{result.raw_relative_path || result.index_path}</p>
            <dl><div><dt>更新时间</dt><dd>{result.modified_at ? new Date(result.modified_at).toLocaleString("zh-CN") : "未知"}</dd></div><div><dt>内容标识</dt><dd>{result.content_id ? result.content_id.slice(0, 18) : "未记录"}</dd></div></dl>
          </section>
        </div>
      )}
      {result && (
        <div className="inspectorActions">
          <button className="secondaryButton" onClick={() => void openLocalUri(result.open_target_uri)}><ExternalLink size={17} />打开来源</button>
          <button className="primaryButton" disabled={selected} onClick={() => void onAdd(result)}>{selected ? <FolderOpen size={17} /> : <Plus size={17} />}{selected ? "已在任务包" : "加入任务包"}</button>
        </div>
      )}
    </aside>
  );
}
