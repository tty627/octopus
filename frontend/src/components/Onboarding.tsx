import { useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { ArrowLeft, ArrowRight, Check, FolderOpen, HardDrive, Sparkles } from "lucide-react";
import { api, ApiError } from "../api";
import { chooseDirectory, suggestIndexPath } from "../bridge";
import type { Repository, RepositoryEstimate } from "../types";
import { formatBytes } from "../utils";

interface OnboardingProps {
  onCreated: (repository: Repository) => void;
  compact?: boolean;
}

export function Onboarding({ onCreated, compact = false }: OnboardingProps) {
  const [step, setStep] = useState(1);
  const [rawPath, setRawPath] = useState("");
  const [indexPath, setIndexPath] = useState("");
  const [name, setName] = useState("我的资料空间");
  const [estimate, setEstimate] = useState<RepositoryEstimate | null>(null);
  const [error, setError] = useState("");

  const preflight = useMutation({
    mutationFn: () => api.preflight(rawPath, indexPath),
    onSuccess: (value) => {
      setEstimate(value);
      setStep(2);
      setError("");
    },
    onError: (reason) => setError(reason instanceof ApiError ? reason.message : "无法检查所选目录。"),
  });
  const create = useMutation({
    mutationFn: () => api.createRepository(rawPath, indexPath, name),
    onSuccess: ({ repository }) => {
      setStep(3);
      onCreated(repository);
    },
    onError: (reason) => setError(reason instanceof ApiError ? reason.message : "资料空间创建失败。"),
  });
  const sample = useMutation({
    mutationFn: api.createSample,
    onSuccess: ({ repository }) => onCreated(repository),
    onError: () => setError("示例资料创建失败，请重试。"),
  });

  const selectRaw = async () => {
    const selected = await chooseDirectory();
    if (!selected) return;
    setRawPath(selected);
    setIndexPath(await suggestIndexPath(selected));
    const leaf = selected.replace(/[\\/]+$/, "").split(/[\\/]/).at(-1);
    if (leaf) setName(leaf);
  };

  return (
    <section className={compact ? "onboarding onboardingCompact" : "onboarding"} aria-labelledby="onboarding-title">
      <div className="onboardingHeader">
        <div>
          <p className="eyebrow">资料空间设置</p>
          <h1 id="onboarding-title">让现有文件夹直接可用于任务</h1>
          <p>Octopus 只读取所选目录，索引保存在独立位置，不会修改原文件。</p>
        </div>
        <div className="stepIndicator" aria-label={`第 ${step} 步，共 3 步`}>
          {[1, 2, 3].map((value) => (
            <span key={value} className={value <= step ? "stepActive" : ""}>{value}</span>
          ))}
        </div>
      </div>

      {step === 1 && (
        <div className="onboardingBody">
          <label className="fieldLabel" htmlFor="space-name">资料空间名称</label>
          <input id="space-name" value={name} onChange={(event) => setName(event.target.value)} />
          <label className="fieldLabel" htmlFor="raw-path">现有资料文件夹</label>
          <div className="pathPicker">
            <input id="raw-path" value={rawPath} readOnly placeholder="选择包含原始资料的文件夹" />
            <button className="secondaryButton" onClick={() => void selectRaw()}><FolderOpen size={17} />选择</button>
          </div>
          <label className="fieldLabel" htmlFor="index-path">索引保存位置</label>
          <div className="pathPicker">
            <input id="index-path" value={indexPath} onChange={(event) => setIndexPath(event.target.value)} placeholder="独立于原资料的位置" />
            <button className="iconButton" aria-label="选择索引目录" title="选择索引目录" onClick={() => void chooseDirectory().then(setIndexPath)}><HardDrive size={17} /></button>
          </div>
          <div className="privacyNote"><Check size={17} /> 原文件保持只读；复制或导出必须由你明确触发。</div>
          <div className="formActions">
            <button className="textButton" onClick={() => sample.mutate()} disabled={sample.isPending}><Sparkles size={17} />使用示例资料</button>
            <button className="primaryButton" onClick={() => preflight.mutate()} disabled={!rawPath || !indexPath || preflight.isPending}>检查范围<ArrowRight size={17} /></button>
          </div>
        </div>
      )}

      {step === 2 && estimate && (
        <div className="onboardingBody">
          <div className="estimateSummary">
            <div><strong>{estimate.file_count}</strong><span>个文件</span></div>
            <div><strong>{estimate.supported_file_count}</strong><span>可建立内容索引</span></div>
            <div><strong>{formatBytes(estimate.estimated_index_bytes)}</strong><span>预计索引空间</span></div>
            <div><strong>约 {Math.max(1, Math.ceil(estimate.estimated_seconds_p50 / 60))} 分钟</strong><span>首批结果预计</span></div>
          </div>
          <div className="formatList" aria-label="文件格式概览">
            {Object.entries(estimate.format_counts).slice(0, 8).map(([format, count]) => <span key={format}>{format || "其他"} · {count}</span>)}
          </div>
          {estimate.warnings.length > 0 && <div className="warningBox">有 {estimate.warnings.length} 项文件可能需要稍后核验，仍可继续建立资料空间。</div>}
          {estimate.blockers.length > 0 && <div className="errorBox">当前目录无法继续：{estimate.blockers.join("、")}</div>}
          <div className="formActions">
            <button className="secondaryButton" onClick={() => setStep(1)}><ArrowLeft size={17} />返回</button>
            <button className="primaryButton" onClick={() => create.mutate()} disabled={estimate.blockers.length > 0 || create.isPending}>建立资料空间<ArrowRight size={17} /></button>
          </div>
        </div>
      )}

      {step === 3 && (
        <div className="onboardingBody onboardingSuccess">
          <span className="successMark"><Check size={24} /></span>
          <h2>资料空间已经可用</h2>
          <p>首批索引正在后台继续完善，你现在就可以进入工作台查找资料。</p>
        </div>
      )}
      {error && <div className="errorBox" role="alert">{error}</div>}
    </section>
  );
}
