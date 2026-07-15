import { useCallback, useEffect, useState } from "react";
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
import type {
  AIConnectionResult,
  AIProviderId,
  AISettingsInputV2,
  AISettingsV2,
} from "../types";

interface SettingsMutationInput {
  sourceWorkspaceId: string;
  settings: AISettingsInputV2;
  visionEnabled?: boolean;
}

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

  const applySettings = useCallback((value: AISettingsV2) => {
    setEnabled(value.enabled);
    setProvider(value.provider);
    setBaseUrl(value.base_url);
    setModel(value.model);
    setVisionEnabled(value.vision_enabled);
  }, []);

  useEffect(() => {
    if (!settings.data) return;
    applySettings(settings.data);
  }, [applySettings, settings.data]);

  useEffect(() => {
    setApiKey("");
    setClearApiKey(false);
    setConnection(null);
    setNotice("");
    setError("");
  }, [workspaceId]);

  const payload = () => ({
    enabled,
    provider,
    base_url: baseUrl,
    model,
    ...(apiKey ? { api_key: apiKey } : {}),
    ...(clearApiKey ? { clear_api_key: true } : {}),
  });
  const test = useMutation({
    mutationFn: (input: SettingsMutationInput) =>
      api.testAISettings(input.sourceWorkspaceId, input.settings),
    onSuccess: (value, input) => {
      if (useAppStore.getState().workspaceId !== input.sourceWorkspaceId) return;
      setConnection(value);
      setError("");
    },
    onError: (reason, input) => {
      if (useAppStore.getState().workspaceId !== input.sourceWorkspaceId) return;
      setError(reason instanceof ApiError ? reason.message : "连接测试没有完成。");
    },
  });
  const save = useMutation({
    mutationFn: async (input: SettingsMutationInput) => {
      const value = await api.saveAISettings(input.sourceWorkspaceId, input.settings);
      await api.setVisionAuthorization(
        input.sourceWorkspaceId,
        Boolean(input.visionEnabled),
      );
      return value;
    },
    onSuccess: async (_, input) => {
      let refreshed: AISettingsV2;
      try {
        refreshed = await api.aiSettings(input.sourceWorkspaceId);
        queryClient.setQueryData(["ai-settings", input.sourceWorkspaceId], refreshed);
      } catch {
        if (useAppStore.getState().workspaceId !== input.sourceWorkspaceId) return;
        setNotice("");
        setApiKey("");
        setClearApiKey(false);
        setError("设置已保存，但重新读取当前状态失败，请重试。");
        return;
      }
      if (useAppStore.getState().workspaceId !== input.sourceWorkspaceId) return;
      setNotice("设置已保存。");
      setError("");
      setApiKey("");
      setClearApiKey(false);
      applySettings(refreshed);
      await queryClient.invalidateQueries({ queryKey: ["workspaces"] });
    },
    onError: async (reason, input) => {
      const detail = reason instanceof ApiError ? reason.message : "设置没有完整保存。";
      try {
        const refreshed = await api.aiSettings(input.sourceWorkspaceId);
        queryClient.setQueryData(["ai-settings", input.sourceWorkspaceId], refreshed);
        if (useAppStore.getState().workspaceId !== input.sourceWorkspaceId) return;
        setNotice("");
        setError(`设置未完整保存，已重新读取当前状态。${detail}`);
        setApiKey("");
        setClearApiKey(false);
        applySettings(refreshed);
      } catch {
        if (useAppStore.getState().workspaceId !== input.sourceWorkspaceId) return;
        setNotice("");
        setApiKey("");
        setClearApiKey(false);
        setError(`设置未完整保存，重新读取当前状态也失败。${detail}`);
      }
      await queryClient.invalidateQueries({ queryKey: ["workspaces"] });
    },
  });

  if (settings.isLoading) return <div className="settingsPage"><LoaderCircle className="spin" size={22} /></div>;
  if (settings.isError || !settings.data) {
    return (
      <div className="settingsPage">
        <div className="pageHeading"><div><h1>设置</h1><p>AI 仅处理本地检索得到的候选；页面图像使用单独授权。</p></div></div>
        <div className="errorBox" role="alert"><AlertTriangle size={18} /><span>无法读取当前设置。为避免覆盖已有配置，编辑功能已暂停。</span></div>
        <button className="secondaryButton" disabled={settings.isFetching} onClick={() => void settings.refetch()}>{settings.isFetching ? <LoaderCircle className="spin" size={17} /> : <PlugZap size={17} />}重新读取</button>
      </div>
    );
  }

  const credentialAvailable = Boolean(
    apiKey.trim() || (settings.data.credential_configured && !clearApiKey),
  );
  const missingEnabledCredential = enabled && !credentialAvailable;

  return (
    <div className="settingsPage">
      <div className="pageHeading"><div><h1>设置</h1><p>AI 仅处理本地检索得到的候选；页面图像使用单独授权。</p></div></div>
      <form className="settingsForm" onSubmit={(event) => {
        event.preventDefault();
        save.mutate({
          sourceWorkspaceId: workspaceId,
          settings: payload(),
          visionEnabled,
        });
      }}>
        <section className="settingsSection">
          <div className="settingsSectionTitle"><PlugZap size={19} /><div><h2>辅助模型</h2><span>{settings.data.credential_configured ? "凭据已保存" : "未保存凭据"}</span></div></div>
          <label className="switchRow"><input type="checkbox" checked={enabled} onChange={(event) => setEnabled(event.target.checked)} /><span>启用辅助整理</span></label>
          <div className="settingsGrid">
            <label>服务商<select value={provider} onChange={(event) => setProvider(event.target.value as AIProviderId)}><option value="deepseek">DeepSeek</option><option value="openai_compatible">OpenAI Compatible</option></select></label>
            <label>模型<input value={model} onChange={(event) => setModel(event.target.value)} /></label>
            <label className="wideField">Base URL<input value={baseUrl} onChange={(event) => setBaseUrl(event.target.value)} /></label>
            <label className="wideField"><span><KeyRound size={15} />API Key</span><input type="password" aria-label="API Key" value={apiKey} onChange={(event) => { setApiKey(event.target.value); setClearApiKey(false); }} placeholder={settings.data.credential_configured ? "已安全保存，留空保持不变" : "输入 API Key"} /></label>
          </div>
          {settings.data.credential_configured && <label className="clearCredential"><input type="checkbox" checked={clearApiKey} onChange={(event) => { setClearApiKey(event.target.checked); if (event.target.checked) setApiKey(""); }} />删除已保存的 API Key</label>}
          {missingEnabledCredential && <div className="warningBox" role="alert"><AlertTriangle size={17} />启用辅助整理前，请先填写 API Key。</div>}
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
          <button type="button" className="secondaryButton" disabled={!baseUrl.trim() || !model.trim() || !credentialAvailable || test.isPending || save.isPending} onClick={() => test.mutate({ sourceWorkspaceId: workspaceId, settings: payload() })}>{test.isPending ? <LoaderCircle className="spin" size={17} /> : <PlugZap size={17} />}测试连接</button>
          <button className="primaryButton" disabled={!baseUrl.trim() || !model.trim() || missingEnabledCredential || save.isPending || test.isPending}>{save.isPending ? <LoaderCircle className="spin" size={17} /> : <Save size={17} />}保存设置</button>
        </div>
      </form>
    </div>
  );
}
