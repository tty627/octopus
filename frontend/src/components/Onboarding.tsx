import { useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { Check, FolderOpen, LoaderCircle } from "lucide-react";
import { ApiError, api, waitForJob } from "../api";
import { chooseDirectory } from "../bridge";
import type { Workspace } from "../types";

export function Onboarding({
  onCreated,
  compact = false,
}: {
  onCreated: (workspace: Workspace) => void;
  compact?: boolean;
}) {
  const [rawPath, setRawPath] = useState("");
  const [name, setName] = useState("我的资料");
  const [error, setError] = useState("");
  const create = useMutation({
    mutationFn: async () => {
      const value = await api.createWorkspace(rawPath, name);
      await waitForJob(value.job.job_id);
      return await api.workspace(value.workspace.workspace_id);
    },
    onSuccess: onCreated,
    onError: (reason) => setError(reason instanceof ApiError ? reason.message : "资料空间创建失败。"),
  });

  const selectRaw = async () => {
    const selected = await chooseDirectory();
    if (!selected) return;
    setRawPath(selected);
    const leaf = selected.replace(/[\\/]+$/, "").split(/[\\/]/).at(-1);
    if (leaf) setName(leaf);
  };

  return (
    <section className={compact ? "onboarding onboardingCompact" : "onboarding"} aria-labelledby="onboarding-title">
      <div>
        <p className="eyebrow">添加资料</p>
        <h1 id="onboarding-title">选择原始资料文件夹</h1>
      </div>
      <label className="fieldLabel" htmlFor="workspace-name">名称</label>
      <input id="workspace-name" value={name} onChange={(event) => setName(event.target.value)} />
      <label className="fieldLabel" htmlFor="raw-path">原始资料文件夹</label>
      <div className="pathPicker">
        <input id="raw-path" value={rawPath} readOnly placeholder="选择包含 PDF 和文本的文件夹" />
        <button className="secondaryButton" onClick={() => void selectRaw()}><FolderOpen size={17} />选择</button>
      </div>
      <div className="privacyNote"><Check size={16} />原文件只读，内部缓存由 Octopus 管理。</div>
      <div className="formActions">
        <button className="primaryButton" disabled={!rawPath || !name.trim() || create.isPending} onClick={() => create.mutate()}>
          {create.isPending ? <LoaderCircle className="spin" size={17} /> : <FolderOpen size={17} />}
          {create.isPending ? "正在建立" : "建立资料空间"}
        </button>
      </div>
      {error && <div className="errorBox" role="alert">{error}</div>}
    </section>
  );
}
