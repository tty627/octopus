import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  AlertTriangle,
  CheckCircle2,
  File,
  LoaderCircle,
  RefreshCw,
  RotateCcw,
} from "lucide-react";
import { ApiError, api, waitForJob } from "../api";
import { useAppStore } from "../store";
import type { Workspace } from "../types";
import { formatBytes, readabilityLabel, relativeTime } from "../utils";
import { Onboarding } from "./Onboarding";

export function RepositoriesView({ workspace }: { workspace: Workspace }) {
  const workspaceId = useAppStore((state) => state.workspaceId);
  const setWorkspaceId = useAppStore((state) => state.setWorkspaceId);
  const queryClient = useQueryClient();
  const [notice, setNotice] = useState("");
  const [error, setError] = useState("");
  const documents = useQuery({
    queryKey: ["documents", workspaceId],
    queryFn: () => api.documents(workspaceId),
    enabled: Boolean(workspaceId),
    refetchInterval: 20_000,
  });
  const sync = useMutation({
    mutationFn: async () => {
      const job = await api.syncWorkspace(workspaceId);
      return await waitForJob(job.job_id);
    },
    onSuccess: async () => {
      setNotice("同步完成。 ");
      setError("");
      await queryClient.invalidateQueries({ queryKey: ["workspaces"] });
      await queryClient.invalidateQueries({ queryKey: ["documents", workspaceId] });
    },
    onError: (reason) => setError(reason instanceof ApiError ? reason.message : "同步没有完成。"),
  });
  const reprocess = useMutation({
    mutationFn: async (documentId: string) => {
      const job = await api.reprocessDocument(workspaceId, documentId);
      return await waitForJob(job.job_id);
    },
    onSuccess: async () => {
      setNotice("文件已重新处理。 ");
      await queryClient.invalidateQueries({ queryKey: ["workspaces"] });
      await queryClient.invalidateQueries({ queryKey: ["documents", workspaceId] });
    },
    onError: (reason) => setError(reason instanceof ApiError ? reason.message : "重新处理没有完成。"),
  });
  const health = workspace.health;

  return (
    <div className="documentsPage">
      <div className="pageHeading">
        <div><h1>资料</h1><p>{workspace.raw_path}</p></div>
        <button className="primaryButton" disabled={sync.isPending} onClick={() => sync.mutate()}>
          {sync.isPending ? <LoaderCircle className="spin" size={17} /> : <RefreshCw size={17} />}
          {sync.isPending ? "同步中" : "同步"}
        </button>
      </div>

      <div className="healthStrip">
        <div><strong>{health.document_count}</strong><span>文档</span></div>
        <div className="healthGood"><strong>{health.readable_count}</strong><span>正文可读</span></div>
        <div className="healthPartial"><strong>{health.partial_count}</strong><span>部分可读</span></div>
        <div className="healthLow"><strong>{health.low_quality_count}</strong><span>识别质量低</span></div>
        <div><strong>{health.metadata_only_count}</strong><span>仅文件信息</span></div>
      </div>
      <div className="syncLine"><CheckCircle2 size={15} />上次同步：{relativeTime(health.last_sync_at)}</div>
      {notice && <div className="successBox" role="status">{notice}</div>}
      {error && <div className="errorBox" role="alert"><AlertTriangle size={16} />{error}</div>}

      <section className="documentTable" aria-label="文档处理状态">
        <div className="documentTableHeader"><span>文件</span><span>正文质量</span><span>大小</span><span>操作</span></div>
        {documents.isLoading && <div className="tableLoading"><LoaderCircle className="spin" size={20} />正在读取文档状态</div>}
        {documents.data?.map((document) => (
          <div className="documentRow" key={document.document_id}>
            <File size={18} />
            <span className="documentIdentity"><strong>{document.name}</strong><small>{document.relative_path}</small></span>
            <span className={`qualityBadge quality-${document.readability}`}>{readabilityLabel(document.readability)}</span>
            <span>{formatBytes(document.size_bytes)}</span>
            <button className="iconButton" disabled={reprocess.isPending} onClick={() => reprocess.mutate(document.document_id)} aria-label={`重新处理 ${document.name}`} title="重新处理"><RotateCcw size={16} /></button>
          </div>
        ))}
      </section>

      <details className="addWorkspacePanel">
        <summary>添加另一个资料空间</summary>
        <Onboarding compact onCreated={(created) => {
          setWorkspaceId(created.workspace_id);
          void queryClient.invalidateQueries({ queryKey: ["workspaces"] });
        }} />
      </details>
    </div>
  );
}
