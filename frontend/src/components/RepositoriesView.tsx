import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Activity, AlertTriangle, CheckCircle2, Database, Plus, RefreshCw, RotateCcw, Wrench } from "lucide-react";
import { api } from "../api";
import { useAppStore } from "../store";
import type { Repository, ValidationReport } from "../types";
import { humanStatus, relativeTime } from "../utils";
import { Onboarding } from "./Onboarding";

export function RepositoriesView({ repositories }: { repositories: Repository[] }) {
  const repositoryId = useAppStore((state) => state.repositoryId);
  const setRepositoryId = useAppStore((state) => state.setRepositoryId);
  const [adding, setAdding] = useState(false);
  const [validation, setValidation] = useState<ValidationReport | null>(null);
  const [notice, setNotice] = useState("");
  const queryClient = useQueryClient();
  const current = repositories.find((item) => item.repository_id === repositoryId) ?? repositories[0];
  const action = useMutation({
    mutationFn: async (kind: "update" | "retry" | "validate" | "rebuild") => {
      if (!current) return;
      if (kind === "validate") {
        const result = await api.validate(current.repository_id);
        setValidation(result);
        return;
      }
      if (kind === "rebuild") await api.rebuildSearch(current.repository_id);
      else await api.updateRepository(current.repository_id, kind === "retry");
    },
    onSuccess: () => setNotice("操作已进入后台，可以继续使用当前资料。"),
    onError: () => setNotice("当前操作没有启动，请稍后重试。"),
  });

  if (!current) return null;
  const abnormal = Object.entries(current.states ?? {}).filter(([state, count]) => !["clean", "indexed"].includes(state) && count > 0);
  return (
    <div className="repositoriesView">
      <div className="pageHeading"><div><p className="eyebrow">资料空间</p><h1>范围与健康</h1><p>正常同步保持安静，只有影响结果可信度的情况才需要处理。</p></div><button className="primaryButton" onClick={() => setAdding((value) => !value)}><Plus size={17} />添加资料空间</button></div>
      {adding && <Onboarding compact onCreated={(repository) => { void queryClient.invalidateQueries({ queryKey: ["repositories"] }); setRepositoryId(repository.repository_id); setAdding(false); }} />}
      <div className="repositoryLayout">
        <nav className="repositoryList" aria-label="资料空间列表">{repositories.map((repository) => <button className={repository.repository_id === current.repository_id ? "repositoryActive" : ""} key={repository.repository_id} onClick={() => setRepositoryId(repository.repository_id)}><Database size={18} /><span><strong>{repository.name}</strong><small>{repository.available ? `同步于 ${relativeTime(repository.last_successful_update_at)}` : "来源不可访问"}</small></span></button>)}</nav>
        <div className="healthCenter">
          <section className="healthHero"><div className={abnormal.length ? "healthIcon healthWarning" : "healthIcon healthGood"}>{abnormal.length ? <AlertTriangle size={22} /> : <CheckCircle2 size={22} />}</div><div><h2>{abnormal.length ? "有资料等待处理" : "资料空间运行正常"}</h2><p>{current.raw_repository_path}</p></div><button className="secondaryButton" onClick={() => action.mutate("update")} disabled={action.isPending}><RefreshCw size={17} />立即同步</button></section>
          <section className="healthDetail"><div className="sectionHeading"><div><p className="eyebrow">需要处理</p><h2>{abnormal.length ? `${abnormal.reduce((sum, [, count]) => sum + count, 0)} 项资料` : "当前没有异常"}</h2></div></div>{abnormal.length ? abnormal.map(([state, count]) => <div className="issueRow" key={state}><AlertTriangle size={18} /><div><strong>{count} 项 · {humanStatus(state)}</strong><p>现有索引仍可使用，系统会在条件满足后继续处理。</p></div><button className="textButton" onClick={() => action.mutate("retry")}><RotateCcw size={16} />重试</button></div>) : <div className="inlineEmpty"><CheckCircle2 size={21} /><div><strong>没有需要人工处理的项目</strong><p>Octopus 会继续在后台维护索引。</p></div></div>}</section>
          <section className="healthDetail"><div className="sectionHeading"><div><p className="eyebrow">恢复工具</p><h2>检查与修复</h2></div></div><div className="recoveryActions"><button className="secondaryButton" onClick={() => action.mutate("validate")}><Activity size={17} />校验资料空间</button><button className="secondaryButton" onClick={() => action.mutate("rebuild")}><Wrench size={17} />修复搜索数据</button></div>{validation && <div className={validation.error_count ? "errorBox" : "successBox"}>{validation.error_count ? `发现 ${validation.error_count} 个错误、${validation.warning_count} 个提醒。` : `校验完成，没有错误；${validation.warning_count} 个提醒。`}</div>}</section>
          {notice && <div className="inlineNotice" role="status">{notice}</div>}
        </div>
      </div>
    </div>
  );
}
