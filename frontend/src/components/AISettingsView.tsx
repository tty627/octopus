import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  AlertTriangle,
  CheckCircle2,
  Eye,
  KeyRound,
  LoaderCircle,
  PlugZap,
  Save,
  ShieldCheck,
} from "lucide-react";
import { ApiError, api } from "../api";
import { useAppStore } from "../store";
import type { AIConnectionResult, AIProviderId } from "../types";

export function AISettingsView() {
  const workspaceId = useAppStore((state) => state.workspaceId);
  const queryClient = useQueryClient();
  const settings = useQuery({
    queryKey: ["ai-settings", workspaceId],
    queryFn: () => api.aiSettings(workspaceId),
    enabled: Boolean(workspaceId),
  });
  const [enabled, setEnabled] = useState(false);
  const [provider, setProvider] = useState<AIProviderId>("deepseek");
  const [baseUrl, setBaseUrl] = useState("https://api.deepseek.com");
  const [model, setModel] = useState("deepseek-v4-flash");
  const [apiKey, setApiKey] = useState("");
  const [clearApiKey, setClearApiKey] = useState(false);
  const [visionEnabled, setVisionEnabled] = useState(false);
  const [connection, setConnection] = useState<AIConnectionResult | null>(null);
  const [notice, setNotice] = useState("");
  const [error, setError] = useState("");

  useEffect(() => {
    if (!settings.data) return;
    setEnabled(settings.data.enabled);
    setProvider(settings.data.provider);
    setBaseUrl(settings.data.base_url);
    setModel(settings.data.model);
    setVisionEnabled(settings.data.vision_enabled);
  }, [settings.data]);

  const payload = () => ({
    enabled,
    provider,
    base_url: baseUrl,
    model,
    ...(apiKey ? { api_key: apiKey } : {}),
    ...(clearApiKey ? { clear_api_key: true } : {}),
  });
  const test = useMutation({
    mutationFn: () => api.testAISettings(workspaceId, payload()),
    onSuccess: (value) => { setConnection(value); setError(""); },
    onError: (reason) => setError(reason instanceof ApiError ? reason.message : "连接测试没有完成。"),
  });
  const save = useMutation({
    mutationFn: async () => {
      const value = await api.saveAISettings(workspaceId, payload());
      await api.setVisionAuthorization(workspaceId, visionEnabled);
      return value;
    },
    onSuccess: async () => {
      setNotice("设置已保存。 ");
      setError("");
      setApiKey("");
      setClearApiKey(false);
      await queryClient.invalidateQueries({ queryKey: ["ai-settings", workspaceId] });
      await queryClient.invalidateQueries({ queryKey: ["workspaces"] });
    },
    onError: (reason) => setError(reason instanceof ApiError ? reason.message : "设置没有保存。"),
  });

  if (settings.isLoading) return <div className="settingsPage"><LoaderCircle className="spin" size={22} /></div>;

  return (
    <div className="settingsPage">
      <div className="pageHeading"><div><h1>设置</h1><p>AI 仅处理本地检索得到的候选；页面图像使用单独授权。</p></div></div>
      <form className="settingsForm" onSubmit={(event) => { event.preventDefault(); save.mutate(); }}>
        <section className="settingsSection">
          <div className="settingsSectionTitle"><PlugZap size={19} /><div><h2>辅助模型</h2><span>{settings.data?.credential_configured ? "凭据已保存" : "未保存凭据"}</span></div></div>
          <label className="switchRow"><input type="checkbox" checked={enabled} onChange={(event) => setEnabled(event.target.checked)} /><span>启用辅助整理</span></label>
          <div className="settingsGrid">
            <label>服务商<select value={provider} onChange={(event) => setProvider(event.target.value as AIProviderId)}><option value="deepseek">DeepSeek</option><option value="openai_compatible">OpenAI Compatible</option></select></label>
            <label>模型<input value={model} onChange={(event) => setModel(event.target.value)} /></label>
            <label className="wideField">Base URL<input value={baseUrl} onChange={(event) => setBaseUrl(event.target.value)} /></label>
            <label className="wideField"><span><KeyRound size={15} />API Key</span><input type="password" aria-label="API Key" value={apiKey} onChange={(event) => { setApiKey(event.target.value); setClearApiKey(false); }} placeholder={settings.data?.credential_configured ? "已安全保存，留空保持不变" : "输入 API Key"} /></label>
          </div>
          {settings.data?.credential_configured && <label className="clearCredential"><input type="checkbox" checked={clearApiKey} onChange={(event) => { setClearApiKey(event.target.checked); if (event.target.checked) setApiKey(""); }} />删除已保存的 API Key</label>}
        </section>

        <section className="settingsSection visionSection">
          <div className="settingsSectionTitle"><Eye size={19} /><div><h2>页面图像授权</h2><span>{visionEnabled ? "已授权" : "仅本地处理"}</span></div></div>
          <label className="switchRow"><input type="checkbox" checked={visionEnabled} onChange={(event) => setVisionEnabled(event.target.checked)} /><span>允许发送疑难页面图像</span></label>
          <div className="localOnlyLine"><ShieldCheck size={16} />关闭时只使用 PDF 文本提取与本地 OCR。</div>
        </section>

        {connection && <div className={connection.ok ? "connectionResult connectionSuccess" : "connectionResult connectionError"}>{connection.ok ? <CheckCircle2 size={18} /> : <AlertTriangle size={18} />}<span><strong>{connection.ok ? "连接成功" : "连接失败"}</strong><small>{connection.message}</small></span></div>}
        {notice && <div className="successBox" role="status">{notice}</div>}
        {error && <div className="errorBox" role="alert">{error}</div>}
        <div className="settingsActions">
          <button type="button" className="secondaryButton" disabled={!baseUrl.trim() || !model.trim() || test.isPending} onClick={() => test.mutate()}>{test.isPending ? <LoaderCircle className="spin" size={17} /> : <PlugZap size={17} />}测试连接</button>
          <button className="primaryButton" disabled={!baseUrl.trim() || !model.trim() || save.isPending}>{save.isPending ? <LoaderCircle className="spin" size={17} /> : <Save size={17} />}保存设置</button>
        </div>
      </form>
    </div>
  );
}
