"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";

import { mutate } from "@/lib/client-api";
import type { ModelProviderState, ProviderOptions } from "@/types/domain";

export function ProviderSettingsForm({ options }: { options: ProviderOptions }) {
  const router = useRouter();
  const activePreset = options.presets.find((preset) => preset.id === options.current.ai_provider);
  const [provider, setProvider] = useState(activePreset?.id ?? "local_rule");
  const [apiKey, setApiKey] = useState("");
  const [model, setModel] = useState(options.current.ai_model);
  const [baseUrl, setBaseUrl] = useState(options.current.base_url ?? "");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [saved, setSaved] = useState(false);
  const selected = options.presets.find((preset) => preset.id === provider);

  function selectProvider(nextId: string) {
    const preset = options.presets.find((item) => item.id === nextId);
    if (!preset) return;
    setProvider(nextId);
    setModel(preset.default_model);
    setBaseUrl(preset.base_url);
    setApiKey("");
    setSaved(false);
    setError("");
  }

  async function save() {
    setBusy(true);
    setSaved(false);
    setError("");
    try {
      const result = await mutate<{ current: ModelProviderState; saved: boolean }>(
        provider === "local_rule" ? "/settings/model-provider/local" : "/settings/model-provider",
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            provider,
            api_key: provider === "local_rule" ? null : apiKey.trim(),
            model,
            base_url: baseUrl.trim(),
          }),
        },
      );
      if (result.current) {
        setModel(result.current.ai_model);
        setBaseUrl(result.current.base_url ?? "");
      }
      setSaved(true);
      router.refresh();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "保存失败");
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="provider-config panel">
      <div className="section-heading">
        <div>
          <span className="eyebrow">ONE-KEY MODEL PROVIDER</span>
          <h2>模型 Provider 一键配置</h2>
          <p className="muted">选择官方 Provider，粘贴官方 API Key 即可。Key 只写入服务器数据目录，浏览器不保存。</p>
        </div>
      </div>

      <div className="provider-presets">
        {options.presets.map((preset) => (
          <button
            key={preset.id}
            className={provider === preset.id ? "provider-preset active" : "provider-preset"}
            type="button"
            onClick={() => selectProvider(preset.id)}
          >
            <strong>{preset.label}</strong>
            <small>{preset.description}</small>
            {preset.id === options.current.ai_provider ? <span className="current-tag">当前</span> : null}
          </button>
        ))}
      </div>

      {selected?.key_required ? (
        <div className="provider-fields">
          <label>
            官方 API Key
            <input
              type="password"
              autoComplete="off"
              value={apiKey}
              onChange={(event) => setApiKey(event.target.value)}
              placeholder={`粘贴 ${selected.label} 官方 API Key`}
            />
          </label>
          <details className="advanced-fields">
            <summary>高级选项：模型 / Base URL</summary>
            <label>
              模型
              <input value={model} onChange={(event) => setModel(event.target.value)} placeholder={selected.default_model} />
            </label>
            <label>
              Base URL
              <input value={baseUrl} onChange={(event) => setBaseUrl(event.target.value)} placeholder={selected.base_url} />
            </label>
          </details>
        </div>
      ) : (
        <p className="muted">本地规则引擎无需 Key，不会发起任何外部请求。</p>
      )}

      {error ? <p className="form-error">{error}</p> : null}
      {saved ? <p className="form-success">已保存并生效，无需重启服务。</p> : null}

      <div className="provider-actions">
        <button className="button primary" disabled={busy} onClick={save}>
          {busy ? "保存中…" : provider === "local_rule" ? "切换为本地规则引擎" : `保存并启用 ${selected?.label ?? ""}`}
        </button>
        <span className="key-status">
          {options.current.masked_api_key ? `当前 Key：${options.current.masked_api_key}` : "当前未配置外部 Key"}
        </span>
      </div>
    </section>
  );
}
