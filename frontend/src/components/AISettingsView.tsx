import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  AlertTriangle,
  CheckCircle2,
  Eye,
  EyeOff,
  KeyRound,
  PlugZap,
  Save,
  ShieldCheck,
  Trash2,
} from "lucide-react";
import { ApiError, api } from "../api";
import { useAppStore } from "../store";
import type {
  AIConnectionResult,
  AIProviderId,
  AISettingsInput,
  Repository,
} from "../types";

const providerLabels: Record<AIProviderId, string> = {
  deepseek: "DeepSeek",
  openai_compatible: "OpenAI 兼容服务",
};

export function AISettingsView({ repository }: { repository: Repository }) {
  const queryClient = useQueryClient();
  const setAiEnabled = useAppStore((state) => state.setAiEnabled);
  const settings = useQuery({
    queryKey: ["ai-settings", repository.repository_id],
    queryFn: () => api.aiSettings(repository.repository_id),
  });
  const [provider, setProvider] = useState<AIProviderId>("deepseek");
  const [baseUrl, setBaseUrl] = useState("https://api.deepseek.com");
  const [model, setModel] = useState("");
  const [apiKey, setApiKey] = useState("");
  const [enabled, setEnabled] = useState(false);
  const [showKey, setShowKey] = useState(false);
  const [notice, setNotice] = useState("");
  const [error, setError] = useState("");
  const [connection, setConnection] = useState<AIConnectionResult | null>(null);
  const [confirmRemove, setConfirmRemove] = useState(false);

  useEffect(() => {
    if (!settings.data) return;
    setProvider(settings.data.provider);
    setBaseUrl(settings.data.base_url);
    setModel(settings.data.model);
    setEnabled(settings.data.enabled);
    setApiKey("");
    setConnection(null);
    setConfirmRemove(false);
  }, [settings.data]);

  const payload = (overrides: Partial<AISettingsInput> = {}): AISettingsInput => ({
    provider,
    base_url: baseUrl.trim(),
    model: model.trim(),
    enabled,
    ...(apiKey.trim() ? { api_key: apiKey.trim() } : {}),
    ...overrides,
  });

  const save = useMutation({
    mutationFn: (value: AISettingsInput) =>
      api.saveAISettings(repository.repository_id, value),
    onSuccess: (value) => {
      queryClient.setQueryData(["ai-settings", repository.repository_id], value);
      setAiEnabled(value.enabled && value.credential_configured);
      setApiKey("");
      setNotice("AI 设置已保存。");
      setError("");
      setConfirmRemove(false);
    },
    onError: (reason) => {
      setNotice("");
      setError(reason instanceof ApiError ? reason.message : "AI 设置没有保存，请重试。");
    },
  });

  const test = useMutation({
    mutationFn: () =>
      api.testAISettings(repository.repository_id, {
        provider,
        base_url: baseUrl.trim(),
        model: model.trim(),
        ...(apiKey.trim() ? { api_key: apiKey.trim() } : {}),
      }),
    onSuccess: (value) => {
      setConnection(value);
      setError("");
    },
    onError: (reason) => {
      setConnection(null);
      setError(reason instanceof ApiError ? reason.message : "连接测试没有完成。");
    },
  });

  const configured = Boolean(apiKey.trim() || settings.data?.credential_configured);
  const status = settings.data?.credential_error
    ? "凭据存储不可用"
    : settings.data?.enabled && settings.data.credential_configured
      ? "已启用"
      : settings.data?.credential_configured
        ? "已配置，当前关闭"
        : "未配置";

  if (settings.isLoading) {
    return <div className="settingsPage"><p className="mutedText">正在读取 AI 设置...</p></div>;
  }
  if (settings.isError) {
    return <div className="settingsPage"><div className="pageHeading"><div><p className="eyebrow">设置</p><h1>AI 服务</h1></div></div><div className="pageError" role="alert"><AlertTriangle size={20} /><div><strong>无法读取 AI 设置</strong><p>本地服务没有返回当前资料空间的模型配置。</p></div><button className="secondaryButton" onClick={() => void settings.refetch()}>重试</button></div></div>;
  }

  return (
    <div className="settingsPage">
      <div className="pageHeading">
        <div>
          <p className="eyebrow">设置</p>
          <h1>AI 服务</h1>
          <p>{repository.name}</p>
        </div>
        <span className={`aiSettingsStatus ${status === "已启用" ? "aiStatusReady" : ""}`}>
          {status === "已启用" ? <CheckCircle2 size={16} /> : <KeyRound size={16} />}
          {status}
        </span>
      </div>

      <form className="aiSettingsForm" onSubmit={(event) => { event.preventDefault(); save.mutate(payload()); }}>
        <section className="settingsSection">
          <div className="settingsSectionHeading">
            <div><h2>模型连接</h2><p>配置当前资料空间使用的模型服务。</p></div>
            <ShieldCheck size={20} />
          </div>

          <div className="settingsFields">
            <label>
              <span>服务商</span>
              <select value={provider} onChange={(event) => { setProvider(event.target.value as AIProviderId); setConnection(null); }}>
                {Object.entries(providerLabels).map(([value, label]) => <option value={value} key={value}>{label}</option>)}
              </select>
            </label>
            <label className="wideField">
              <span>API Base URL</span>
              <input value={baseUrl} onChange={(event) => { setBaseUrl(event.target.value); setConnection(null); }} placeholder="https://api.example.com/v1" spellCheck={false} />
            </label>
            <label>
              <span>模型</span>
              <input value={model} onChange={(event) => { setModel(event.target.value); setConnection(null); }} placeholder="输入模型名称" spellCheck={false} />
            </label>
            <div className="wideField settingsField">
              <label htmlFor="ai-api-key">API Key</label>
              <div className="secretField">
                <input id="ai-api-key" type={showKey ? "text" : "password"} value={apiKey} onChange={(event) => { setApiKey(event.target.value); setConnection(null); }} placeholder={settings.data?.credential_configured ? "已安全保存；留空表示不更改" : "输入 API Key"} autoComplete="off" spellCheck={false} />
                <button type="button" className="iconButton" aria-label={showKey ? "隐藏 API Key" : "显示 API Key"} title={showKey ? "隐藏" : "显示"} onClick={() => setShowKey((value) => !value)}>{showKey ? <EyeOff size={17} /> : <Eye size={17} />}</button>
              </div>
            </div>
          </div>

          <div className="credentialLine">
            <span><ShieldCheck size={16} />{settings.data?.credential_source === "environment" ? "由环境变量提供" : settings.data?.credential_configured ? "已保存到 Windows 凭据管理器" : "尚未保存密钥"}</span>
            {settings.data?.credential_source === "windows_credential" && !confirmRemove && <button type="button" className="textButton dangerText" onClick={() => setConfirmRemove(true)}><Trash2 size={15} />移除密钥</button>}
            {confirmRemove && <div className="removeCredentialActions"><button type="button" className="secondaryButton smallButton" onClick={() => setConfirmRemove(false)}>取消</button><button type="button" className="dangerButton smallButton" onClick={() => save.mutate(payload({ enabled: false, clear_api_key: true, api_key: undefined }))}>确认移除</button></div>}
          </div>
        </section>

        <section className="settingsSection">
          <div className="settingsSectionHeading"><div><h2>使用状态</h2><p>联网模型只在明确启用时参与摘要和搜索增强。</p></div></div>
          <label className={`settingsToggle ${configured ? "" : "toggleDisabled"}`}>
            <input type="checkbox" checked={enabled} disabled={!configured} onChange={(event) => setEnabled(event.target.checked)} />
            <span><strong>在当前资料空间启用 AI</strong><small>{configured ? "本地搜索始终先返回，模型不可用时自动降级。" : "请先填写 API Key。"}</small></span>
          </label>
        </section>

        {connection && <div className={connection.ok ? "connectionResult connectionSuccess" : "connectionResult connectionError"} role="status">{connection.ok ? <CheckCircle2 size={18} /> : <AlertTriangle size={18} />}<span><strong>{connection.ok ? "连接成功" : "连接失败"}</strong><small>{connection.message}</small></span></div>}
        {notice && <div className="successBox" role="status"><CheckCircle2 size={17} />{notice}</div>}
        {error && <div className="errorBox" role="alert">{error}</div>}

        <div className="settingsActions">
          <button type="button" className="secondaryButton" disabled={!baseUrl.trim() || !model.trim() || test.isPending} onClick={() => test.mutate()}><PlugZap size={17} />{test.isPending ? "正在测试" : "测试连接"}</button>
          <button className="primaryButton" disabled={!baseUrl.trim() || !model.trim() || save.isPending}><Save size={17} />{save.isPending ? "正在保存" : "保存设置"}</button>
        </div>
      </form>
    </div>
  );
}
