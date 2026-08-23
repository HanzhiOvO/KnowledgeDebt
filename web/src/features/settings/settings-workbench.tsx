"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useState } from "react";

import { mutate, publicApiUrl } from "@/lib/client-api";
import type { ApplicationSettings, LocalASRStatus, LocalModel, ProviderProfile, ProviderSettings, ProviderUsage, ScheduleConnection, StorageCleanupPreview, StorageCleanupResult, StorageMaintenanceSnapshot } from "@/types/domain";

const groups = [
  { id: "ai", label: "AI 分析", capability: "structured_generation" },
  { id: "asr", label: "语音转写", capability: "audio_transcription" },
  { id: "embedding", label: "向量检索", capability: "embeddings" },
] as const;

const adapters = {
  local_rule: {
    label: "本地透明规则引擎",
    hint: "零配置、完全离线；只整理已经检索到的真实资料，并把结果明确标为推断。",
    external: false,
    endpointLabel: "本地运行方式",
    endpointPlaceholder: "应用内置，无需填写",
    endpointRequired: false,
    modelLabel: "版本标识",
    modelPlaceholder: "transparent-rules-v1",
    credential: false,
    capabilities: ["structured_generation", "chat_analysis"],
    preset: ["structured_generation", "chat_analysis"],
    vendors: [["local_rule", "本地规则引擎"]] as Array<[string, string]>,
  },
  openai_compatible: {
    label: "外部 OpenAI 兼容接口",
    hint: "每次外发都要单独授权；密钥只允许 env 引用或加密保存。",
    external: true,
    endpointLabel: "Base URL",
    endpointPlaceholder: "https://api.openai.com/v1",
    endpointRequired: true,
    modelLabel: "默认模型",
    modelPlaceholder: "模型 ID",
    credential: true,
    capabilities: ["structured_generation", "chat_analysis", "embeddings", "audio_transcription", "segment_timestamps", "long_audio"],
    preset: [] as string[],
    vendors: [
      ["openai", "OpenAI"],
      ["deepseek", "DeepSeek"],
      ["opencode", "OpenCode Zen"],
      ["qwen_dashscope", "通义千问 / DashScope"],
      ["moonshot", "Kimi / Moonshot"],
      ["zhipu_glm", "智谱 GLM"],
      ["minimax", "MiniMax"],
      ["custom_openai_compatible", "自定义兼容接口"],
    ] as Array<[string, string]>,
  },
  local_whisper_cpp: {
    label: "本地 whisper.cpp（命令行）",
    hint: "音频不出本机：调用本地可执行文件，按分片写入带时间戳的转写结果。推荐直接使用下方经过校验的模型管理器。",
    external: false,
    endpointLabel: "可执行文件路径（留空则用服务端 KNOWLEDGEDEBT_LOCAL_ASR_BINARY）",
    endpointPlaceholder: "/opt/homebrew/bin/whisper-cli",
    endpointRequired: false,
    modelLabel: "模型（绝对路径、模型目录下文件名或 medium 这类简称）",
    modelPlaceholder: "ggml-medium.bin",
    credential: false,
    capabilities: ["audio_transcription", "segment_timestamps"],
    preset: ["audio_transcription", "segment_timestamps"],
    vendors: [["local_whisper_cpp", "本地 whisper.cpp"]] as Array<[string, string]>,
  },
  local_openai_asr: {
    label: "本地 / 私网 ASR 服务",
    hint: "只允许 localhost、私网或 Tailscale 地址；填入公网地址会被后端拒绝。",
    external: false,
    endpointLabel: "私网 Base URL",
    endpointPlaceholder: "http://192.168.1.30:8080/v1",
    endpointRequired: true,
    modelLabel: "服务加载的模型 ID",
    modelPlaceholder: "whisper-small",
    credential: true,
    capabilities: ["audio_transcription", "segment_timestamps"],
    preset: ["audio_transcription", "segment_timestamps"],
    vendors: [["local_asr_service", "本地 / 私网 ASR 服务"]] as Array<[string, string]>,
  },
} as const;

type AdapterId = keyof typeof adapters;

const quickProviderPresets = [
  { id: "openai", label: "OpenAI", vendor: "openai", name: "OpenAI", baseUrl: "https://api.openai.com/v1", model: "gpt-5-mini" },
  { id: "deepseek", label: "DeepSeek", vendor: "deepseek", name: "DeepSeek", baseUrl: "https://api.deepseek.com", model: "deepseek-chat" },
  { id: "opencode", label: "OpenCode Zen", vendor: "opencode", name: "OpenCode Zen", baseUrl: "https://opencode.ai/zen/v1", model: "gpt-5.5" },
] as const;

type QuickProviderPreset = (typeof quickProviderPresets)[number];

export function SettingsWorkbench({
  settings,
  application,
  usage,
  connection,
  backendError,
}: {
  settings: ProviderSettings | null;
  application: ApplicationSettings | null;
  usage: ProviderUsage | null;
  connection: ScheduleConnection | null;
  backendError?: string;
}) {
  const router = useRouter();
  const [tab, setTab] = useState("general");
  const [adding, setAdding] = useState(false);
  const [adapter, setAdapter] = useState<AdapterId>("openai_compatible");
  const [quickPreset, setQuickPreset] = useState<QuickProviderPreset | null>(null);
  const [busy, setBusy] = useState("");
  const [error, setError] = useState(backendError ?? "");

  useEffect(() => {
    const selectLinkedTab = () => {
      const linked = window.location.hash.slice(1);
      if (["general", "providers", "schedule", "privacy", "usage"].includes(linked)) {
        setTab(linked);
        window.setTimeout(() => document.getElementById(linked)?.scrollIntoView({ behavior: "smooth", block: "start" }), 0);
      }
    };
    selectLinkedTab();
    window.addEventListener("hashchange", selectLinkedTab);
    return () => window.removeEventListener("hashchange", selectLinkedTab);
  }, []);

  async function setDefault(group: string, profileId: string) {
    setBusy(group);
    setError("");
    try {
      await mutate(`/settings/providers/defaults/${group}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ profile_id: profileId }),
      });
      router.refresh();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "设置失败");
    } finally { setBusy(""); }
  }

  async function testProfile(profileId: string) {
    const profile = settings?.profiles.find((item) => item.id === profileId);
    if (
      profile?.external
      && !window.confirm(
        `将向 ${profile.name}（${profile.vendor} · ${profile.default_model || "未填写模型"}）发送最小能力测试请求。`
        + "测试不会发送课程资料，但可能产生极少量调用费用。是否继续？",
      )
    ) return;
    setBusy(profileId);
    setError("");
    try {
      await mutate(`/settings/providers/${profileId}/test`, { method: "POST" });
      router.refresh();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "连接测试失败");
    } finally { setBusy(""); }
  }

  async function addProfile(formData: FormData) {
    setBusy("new");
    setError("");
    const capabilities = formData.getAll("capabilities");
    const body = Object.fromEntries(formData);
    delete body.capabilities;
    if (!body.credential) delete body.credential;
    if (!body.credential_reference) delete body.credential_reference;
    let customHeaders: Record<string, string> = {};
    if (adapter === "openai_compatible" && body.custom_headers) {
      try {
        const parsed = JSON.parse(String(body.custom_headers)) as unknown;
        if (!parsed || Array.isArray(parsed) || typeof parsed !== "object") throw new Error();
        if (!Object.values(parsed).every((value) => typeof value === "string")) throw new Error();
        customHeaders = parsed as Record<string, string>;
      } catch {
        setError("自定义请求头必须是 JSON 对象，名称和值都需要使用字符串。");
        setBusy("");
        return;
      }
    }
    delete body.custom_headers;
    try {
      await mutate("/settings/providers", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ ...body, adapter, capabilities, custom_headers: customHeaders, external: adapters[adapter].external, enabled: true }),
      });
      setAdding(false);
      setQuickPreset(null);
      router.refresh();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "新增失败");
    } finally { setBusy(""); }
  }

  function openProfile(preset?: QuickProviderPreset) {
    setAdapter("openai_compatible");
    setQuickPreset(preset ?? null);
    setAdding(true);
  }

  async function saveApplication(formData: FormData) {
    setBusy("application");
    setError("");
    try {
      await mutate("/settings/application", {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          timezone: formData.get("timezone"),
          auto_transcribe: formData.get("auto_transcribe") === "true",
        }),
      });
      router.refresh();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "应用偏好保存失败");
    } finally {
      setBusy("");
    }
  }

  return (
    <>
      <header className="page-header">
        <div><span className="eyebrow">SETTINGS & PRIVACY</span><h1>连接、能力与数据边界</h1><p>AI、ASR 和 Embedding 分开路由；每次外发都显示实际 Provider、模型与资源。</p></div>
        <span className="local-pill"><i />Local-first</span>
      </header>
      <div className="settings-tabs" role="tablist" aria-label="设置分类">
        {[["general", "常用设置"], ["providers", "模型与转写服务"], ["schedule", "教务同步"], ["privacy", "存储与诊断"], ["usage", "用量与费用"]].map(([id, label]) => <button aria-selected={tab === id} className={tab === id ? "active" : ""} key={id} onClick={() => setTab(id)} role="tab">{label}</button>)}
      </div>
      {error ? <div className="notice error" role="alert">{error}</div> : null}

      {tab === "general" ? <ApplicationPreferences application={application} busy={busy === "application"} onSave={saveApplication} /> : null}
      {tab === "providers" ? (
        <section className="settings-section" id="providers">
          <div className="section-heading"><div><span className="eyebrow">MODEL & TRANSCRIPTION</span><h2>模型与转写服务</h2><p>常用选项优先展示，兼容接口和原始能力放在高级设置中。</p></div><button className="button primary" onClick={() => openProfile()}>＋ 新建服务</button></div>
          <div className="quick-provider-bar" aria-label="常用 Provider 快速配置">
            <span><strong>快速配置文本模型</strong><small>选厂商、填密钥和模型即可；保存后请先测试连接。</small></span>
            <div>{quickProviderPresets.map((preset) => <button className="button ghost" key={preset.id} onClick={() => openProfile(preset)}>{preset.label}</button>)}</div>
          </div>
          <div className="routing-grid">
            {groups.filter((group) => group.id !== "embedding").map((group) => {
              const capable = settings?.profiles.filter((profile) => profile.enabled && profile.capabilities.some((capability) => capability === group.capability || (group.id === "asr" && capability === "async_audio_transcription"))) ?? [];
              return <label className="routing-card" key={group.id}><span>{group.label}</span><select disabled={busy === group.id || !capable.length} onChange={(event) => setDefault(group.id, event.target.value)} value={settings?.defaults[group.id]?.id ?? ""}><option value="">未配置</option>{capable.map((profile) => <option key={profile.id} value={profile.id}>{profile.name} · {profile.default_model}</option>)}</select><small>{group.id === "asr" ? "控制默认自动转写" : "课堂分析与验收"}</small></label>;
            })}
          </div>
          <details className="panel advanced-settings">
            <summary>高级设置</summary>
            <p>适合需要自定义兼容接口或向量检索的用户。折叠后已保存的值不会丢失。</p>
            {groups.filter((group) => group.id === "embedding").map((group) => {
              const capable = settings?.profiles.filter((profile) => profile.enabled && profile.capabilities.includes(group.capability)) ?? [];
              return <label className="routing-card" key={group.id}><span>{group.label}</span><select disabled={busy === group.id || !capable.length} onChange={(event) => setDefault(group.id, event.target.value)} value={settings?.defaults[group.id]?.id ?? ""}><option value="">未配置</option>{capable.map((profile) => <option key={profile.id} value={profile.id}>{profile.name} · {profile.default_model}</option>)}</select><small>默认本地 Hash 检索不外发</small></label>;
            })}
          </details>
          <LocalASRPanel status={settings?.local_asr} />
          <LocalModelManagerPanel
            initialModels={settings?.local_models ?? []}
            key={settings?.local_models.map((model) => `${model.id}:${model.status}:${model.selected}`).join("|")}
            onError={setError}
            onRouteChanged={() => router.refresh()}
            runtime={settings?.local_asr}
          />
          <div className="provider-grid">
            {settings?.profiles.map((profile) => <ProviderCard busy={busy === profile.id} key={profile.id} profile={profile} defaults={settings.defaults} onTest={() => testProfile(profile.id)} />)}
          </div>
          {!settings ? <div className="panel review-empty"><h2>后端暂时离线</h2><p>Provider Profile 会在连接恢复后显示。</p></div> : null}
        </section>
      ) : null}

      {tab === "schedule" ? <ScheduleSettings connection={connection} /> : null}
      {tab === "privacy" ? <StorageAndDiagnostics application={application} encryption={Boolean(settings?.secret_encryption_configured)} onError={setError} storage={settings?.storage_provider} /> : null}
      {tab === "usage" ? <UsageSettings usage={usage} /> : null}

      {adding ? (
        <div className="modal-backdrop" role="presentation">
          <form action={addProfile} className="consent-modal panel profile-form" role="dialog" aria-modal="true" aria-labelledby="profile-title">
            <span className="eyebrow">NEW SERVICE</span><h2 id="profile-title">新增模型或转写服务</h2>
            <p className="muted">{adapters[adapter].hint}</p>
            <div className="form-pair"><label>服务名称<input key={`${adapter}-${quickPreset?.id ?? "custom"}-name`} name="name" required defaultValue={quickPreset?.name ?? (adapter === "local_rule" ? "本地规则引擎" : "")} placeholder={adapter === "openai_compatible" ? "我的 OpenAI" : adapter === "local_rule" ? "本地规则引擎" : "本地转写服务"} /></label><label>厂商<select key={`${adapter}-${quickPreset?.id ?? "custom"}-vendor`} name="vendor" defaultValue={quickPreset?.vendor ?? adapters[adapter].vendors.at(-1)?.[0]}>{adapters[adapter].vendors.map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label></div>
            <label>{adapters[adapter].modelLabel}<input key={`${adapter}-${quickPreset?.id ?? "custom"}-model`} name="default_model" defaultValue={quickPreset?.model ?? (adapter === "local_rule" ? "transparent-rules-v1" : "")} required={adapter !== "local_whisper_cpp"} placeholder={adapters[adapter].modelPlaceholder} /></label>
            {adapters[adapter].credential ? <label>API 密钥<input autoComplete="off" disabled={!settings?.secret_encryption_configured} name="credential" placeholder={settings?.secret_encryption_configured ? "新密钥将加密保存" : "需先配置加密主密钥"} type="password" /></label> : null}
            <details className="advanced-settings">
              <summary>高级接入设置</summary>
              <label>接入方式<select onChange={(event) => { setAdapter(event.target.value as AdapterId); setQuickPreset(null); }} value={adapter}>{(Object.keys(adapters) as AdapterId[]).map((id) => <option key={id} value={id}>{adapters[id].label}</option>)}</select></label>
              <label>{adapters[adapter].endpointLabel}<input key={`${adapter}-${quickPreset?.id ?? "custom"}-endpoint`} name="base_url" defaultValue={quickPreset?.baseUrl ?? (adapter === "openai_compatible" ? "https://api.openai.com/v1" : "")} required={adapters[adapter].endpointRequired} placeholder={adapters[adapter].endpointPlaceholder} disabled={adapter === "local_rule"} /></label>
              {adapters[adapter].credential ? <label>环境变量引用<input name="credential_reference" placeholder={adapter === "openai_compatible" ? "env:OPENAI_API_KEY" : "env:LOCAL_ASR_TOKEN"} /></label> : null}
              {adapter === "openai_compatible" ? <label>可选自定义请求头（JSON）<textarea name="custom_headers" placeholder={'{"OpenAI-Organization":"org_..."}'} rows={3} /><small>敏感请求头会被拒绝，请使用加密密钥字段。</small></label> : null}
              <fieldset className="capability-picker" key={`${adapter}-${quickPreset?.id ?? "custom"}-capabilities`}><legend>原始能力</legend>{adapters[adapter].capabilities.map((capability) => <label key={`${adapter}-${capability}`}><input defaultChecked={quickPreset ? ["structured_generation", "chat_analysis"].includes(capability) : (adapters[adapter].preset as readonly string[]).includes(capability)} name="capabilities" type="checkbox" value={capability} />{capability}</label>)}</fieldset>
            </details>
            <p className="muted">{adapters[adapter].external ? "该 Profile 会被标记为外部：每次转写或分析都需要单独授权。" : "该 Profile 会被强制标记为本地：不外发音频，无需逐次授权。"}</p>
            <div className="modal-actions"><button className="button secondary" onClick={() => { setAdding(false); setQuickPreset(null); }} type="button">取消</button><button className="button primary" disabled={busy === "new"}>{busy === "new" ? "保存中…" : "保存服务"}</button></div>
          </form>
        </div>
      ) : null}
    </>
  );
}

function ApplicationPreferences({ application, busy, onSave }: { application: ApplicationSettings | null; busy: boolean; onSave: (formData: FormData) => Promise<void> }) {
  return <section className="settings-section"><div className="section-heading"><div><span className="eyebrow">APPLICATION DEFAULTS</span><h2>录音与时间偏好</h2></div></div><form action={onSave} className="panel connection-card"><label>显示时区<input defaultValue={application?.timezone ?? "Asia/Shanghai"} list="timezone-options" name="timezone" required /><datalist id="timezone-options"><option value="Asia/Shanghai" /><option value="Asia/Hong_Kong" /><option value="UTC" /></datalist><small>今日课程、课表日期和 Session 日期统一按此 IANA 时区计算。</small></label><label className="checkbox-row"><input defaultChecked={application?.auto_transcribe ?? true} name="auto_transcribe" type="checkbox" value="true" />录音或音频保存完成后自动转写</label><p className="muted">默认开启。没有可用模型或 API 时只会显示“等待配置转写服务”，原始录音照常保存且不会未经同意外发。</p><button className="button primary" disabled={busy}>{busy ? "保存中…" : "保存应用偏好"}</button></form></section>;
}

function ProviderCard({ profile, defaults, busy, onTest }: { profile: ProviderProfile; defaults: Record<string, ProviderProfile>; busy: boolean; onTest: () => void }) {
  const usedBy = groups.filter((group) => defaults[group.id]?.id === profile.id).map((group) => group.label);
  const customHeaderCount = Object.keys(profile.custom_headers ?? {}).length;
  const testedAt = profile.last_tested_at
    ? new Intl.DateTimeFormat("zh-CN", { dateStyle: "medium", timeStyle: "short" }).format(new Date(profile.last_tested_at))
    : "从未测试";
  return (
    <article className="panel provider-card">
      <header>
        <span className="provider-logo">{profile.name.slice(0, 1).toUpperCase()}</span>
        <span><strong>{profile.name}</strong><small>{profile.vendor}</small></span>
        <span className={`badge ${profile.enabled ? "accepted" : "cancelled"}`}>{profile.enabled ? "已启用" : "已禁用"}</span>
      </header>
      <dl className="provider-details">
        <div><dt>默认模型</dt><dd>{profile.default_model || "未设置"}</dd></div>
        <div><dt>最近测试</dt><dd>{testedAt}</dd></div>
      </dl>
      <div className="provider-meta">
        <span>{profile.external ? "外部 · 每次需授权" : "本地"}</span>
        <span>{profile.external ? (profile.credential_configured ? "密钥可用" : "密钥未配置") : "无需密钥"}</span>
        {customHeaderCount ? <span>{customHeaderCount} 个自定义请求头</span> : null}
        {usedBy.length ? <span>默认：{usedBy.join(" / ")}</span> : null}
      </div>
      <details className="advanced-settings compact"><summary>查看高级信息</summary><dl className="provider-details"><div><dt>接入方式</dt><dd>{profile.adapter}</dd></div><div><dt>{profile.external ? "API 地址" : "本地地址 / 路径"}</dt><dd title={profile.base_url || undefined}>{profile.base_url || "使用应用内置运行时"}</dd></div></dl><div className="capability-list">{profile.capabilities.length ? profile.capabilities.map((item) => <span key={item}>{item}</span>) : <span>未声明能力</span>}</div></details>
      {profile.last_test_message ? <p className={profile.last_test_status === "succeeded" ? "test-message success" : "test-message error"}>{profile.last_test_message}</p> : null}
      <footer><span>{statusLabel(profile.implementation_status)}</span><button className="button ghost" disabled={busy} onClick={onTest}>{busy ? "测试中…" : "测试连接"}</button></footer>
      <small className="provider-test-note">{profile.external ? "按供应商定价；测试会发送最小合成请求，不发送课程内容，可能产生极少量费用。系统不会静默切换到其他外部服务。" : "数据留在本机或私网，不产生外部 API 调用费用。"}</small>
    </article>
  );
}

function LocalASRPanel({ status }: { status?: LocalASRStatus | null }) {
  if (!status) return null;
  const megabytes = status.model_bytes ? Math.round(status.model_bytes / 1048576) : null;
  return (
    <article className="panel connection-card">
      <div className="connection-hero">
        <span className={`connection-orb ${status.ready ? "state-connected" : "state-error"}`} />
        <span><strong>本地转写 · whisper.cpp</strong><small>{status.ready ? status.active ? "已就绪并作为默认转写：音频只在本机处理" : "组件已就绪；选择下方模型即可切换为默认转写" : "尚未就绪：请按下方提示补齐运行时或模型"}</small></span>
      </div>
      <dl>
        <div><dt>可执行文件</dt><dd>{status.binary_ready ? status.binary_resolved : `${status.binary || "未配置"} · 未找到`}</dd></div>
        <div><dt>模型</dt><dd>{status.model_ready ? `${status.model_resolved}${megabytes ? ` · ${megabytes} MB` : ""}` : `${status.model || "未配置"} · 未找到`}</dd></div>
        <div><dt>模型目录</dt><dd>{status.model_dir ?? "未配置"}</dd></div>
        <div><dt>语言 / 线程</dt><dd>{status.language} / {status.threads > 0 ? status.threads : "由 whisper.cpp 决定"}</dd></div>
        <div><dt>单分片超时</dt><dd>{status.timeout_seconds} 秒</dd></div>
        <div><dt>FFmpeg</dt><dd>{status.ffmpeg_ready ? "可用" : "缺失 · 非 WAV 分片无法转换"}</dd></div>
      </dl>
      <p>{status.ready ? "长录音仍按本地分片处理，失败分片可断点续跑。切换到外部 Provider 不会删除已下载模型。" : status.binary_ready ? "运行时已找到；请在下方选择并下载模型。下载前会显示大小，完成后会校验 SHA-256。" : "原生安装包应自带 whisper.cpp；源码开发模式可配置 KNOWLEDGEDEBT_LOCAL_ASR_BINARY。即使尚未配置，录音仍会安全保存。"}</p>
    </article>
  );
}

type PendingModelAction = { kind: "download" | "delete"; model: LocalModel };

function LocalModelManagerPanel({
  initialModels,
  runtime,
  onError,
  onRouteChanged,
}: {
  initialModels: LocalModel[];
  runtime?: LocalASRStatus | null;
  onError: (message: string) => void;
  onRouteChanged: () => void;
}) {
  const [models, setModels] = useState(initialModels);
  const [busy, setBusy] = useState("");
  const [pending, setPending] = useState<PendingModelAction | null>(null);
  const shouldPoll = models.some((model) => ["queued", "downloading", "cancelling"].includes(model.status));

  useEffect(() => {
    if (!shouldPoll) return;
    let active = true;
    const refresh = async () => {
      try {
        const latest = await mutate<LocalModel[]>("/settings/local-models", { method: "GET" });
        if (active) setModels(latest);
      } catch (reason) {
        if (active) onError(reason instanceof Error ? reason.message : "模型下载状态刷新失败");
      }
    };
    const timer = window.setInterval(refresh, 800);
    return () => {
      active = false;
      window.clearInterval(timer);
    };
  }, [onError, shouldPoll]);

  function replaceModel(updated: LocalModel) {
    setModels((current) => current.map((model) => model.id === updated.id ? updated : model));
  }

  async function runAction(kind: "download" | "delete" | "activate" | "cancel", model: LocalModel) {
    setBusy(`${kind}:${model.id}`);
    onError("");
    try {
      const request = kind === "download"
        ? { path: `/settings/local-models/${model.id}/download`, init: { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ confirmed: true }) } }
        : kind === "delete"
          ? { path: `/settings/local-models/${model.id}`, init: { method: "DELETE", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ confirmed: true }) } }
          : { path: `/settings/local-models/${model.id}/${kind}`, init: { method: "POST" } };
      replaceModel(await mutate<LocalModel>(request.path, request.init));
      if (kind === "activate") onRouteChanged();
    } catch (reason) {
      onError(reason instanceof Error ? reason.message : "本地模型操作失败");
    } finally {
      setBusy("");
      setPending(null);
    }
  }

  if (!models.length) return null;
  return (
    <section className="local-model-section" aria-labelledby="local-model-heading">
      <div className="section-heading">
        <div><span className="eyebrow">LOCAL MODEL LIBRARY</span><h2 id="local-model-heading">本地模型</h2><p>只在确认后下载到用户数据目录；支持取消、断点续传、完整性校验、切换和删除。</p></div>
        <span className={`badge ${runtime?.binary_ready ? "accepted" : "scheduled"}`}>{runtime?.binary_ready ? "运行时可用" : "等待本地运行时"}</span>
      </div>
      <div className="local-model-grid">
        {models.map((model) => {
          const activeDownload = ["queued", "downloading", "cancelling"].includes(model.status);
          return (
            <article className={`panel local-model-card ${model.selected ? "selected" : ""}`} key={model.id}>
              <header>
                <span><strong>{model.name}</strong><small>{model.file_name}</small></span>
                <span className={`badge ${model.selected ? "accepted" : model.corrupted || model.status === "failed" ? "cancelled" : model.installed ? "scheduled" : ""}`}>{model.selected ? "正在使用" : localModelStatus(model)}</span>
              </header>
              <p>{model.use_case}</p>
              <dl>
                <div><dt>下载 / 占用</dt><dd>{formatBytes(model.download_bytes)} / {formatBytes(model.disk_bytes)}</dd></div>
                <div><dt>速度 / 准确率</dt><dd>{model.speed} / {model.accuracy}</dd></div>
                <div><dt>语言</dt><dd>{model.languages.join("、")}</dd></div>
                <div><dt>校验</dt><dd title={model.sha256}>SHA-256 · {model.sha256.slice(0, 12)}…</dd></div>
              </dl>
              {model.recommended ? <span className="model-recommendation">推荐用于大学课堂</span> : null}
              {activeDownload || model.can_resume ? (
                <div className="model-download-progress">
                  <span><strong>{activeDownload ? "正在下载" : "已保留断点"}</strong><small>{formatBytes(model.bytes_downloaded)} / {formatBytes(model.download_bytes)} · {model.progress.toFixed(1)}%</small></span>
                  <progress aria-label={`${model.name} 下载进度`} max="100" value={model.progress} />
                </div>
              ) : null}
              {model.error ? <p className="model-error" role="status">{model.error}</p> : null}
              <footer>
                {activeDownload ? <button className="button secondary" disabled={busy === `cancel:${model.id}` || model.status === "cancelling"} onClick={() => runAction("cancel", model)}>{model.status === "cancelling" ? "正在停止…" : "取消下载"}</button> : null}
                {!activeDownload && !model.installed ? <button className="button primary" onClick={() => setPending({ kind: "download", model })}>{model.can_resume ? "继续下载" : model.corrupted || model.status === "failed" || model.status === "missing" ? "重新下载" : "下载"}</button> : null}
                {model.installed && !model.selected ? <button className="button primary" disabled={busy === `activate:${model.id}`} onClick={() => runAction("activate", model)}>{busy === `activate:${model.id}` ? "切换中…" : "设为本地转写模型"}</button> : null}
                {model.installed && !model.selected ? <button className="button ghost" onClick={() => setPending({ kind: "delete", model })}>删除</button> : null}
                {model.selected ? <span className="model-active-note">当前录音会优先使用此模型，不会外发音频。</span> : null}
              </footer>
            </article>
          );
        })}
      </div>
      <p className="model-directory">保存目录：<code>{models[0]?.model_directory}</code></p>
      {pending ? (
        <div className="modal-backdrop" role="presentation">
          <div aria-labelledby="model-confirm-title" aria-modal="true" className="consent-modal panel" role="dialog">
            <span className="eyebrow">CONFIRM LOCAL MODEL</span>
            <h2 id="model-confirm-title">{pending.kind === "download" ? `下载 ${pending.model.name}` : `删除 ${pending.model.name}`}</h2>
            {pending.kind === "download" ? <p>将从经过固定提交和 SHA-256 审核的官方模型仓库下载约 <strong>{formatBytes(pending.model.download_bytes)}</strong>。文件只保存到本地；取消后会保留已下载部分，便于继续。</p> : <p>将从本机删除约 <strong>{formatBytes(pending.model.disk_bytes)}</strong> 的模型文件。课程、录音和转写结果不会删除。</p>}
            <div className="modal-actions"><button className="button secondary" onClick={() => setPending(null)}>返回</button><button className={`button ${pending.kind === "delete" ? "danger" : "primary"}`} disabled={Boolean(busy)} onClick={() => runAction(pending.kind, pending.model)}>{busy ? "处理中…" : pending.kind === "download" ? "确认下载" : "确认删除"}</button></div>
          </div>
        </div>
      ) : null}
    </section>
  );
}

function formatBytes(bytes: number) {
  if (bytes >= 1024 ** 3) return `${(bytes / 1024 ** 3).toFixed(1)} GB`;
  return `${Math.round(bytes / 1024 ** 2)} MB`;
}

function localModelStatus(model: LocalModel) {
  return ({
    not_downloaded: "未下载",
    queued: "等待下载",
    downloading: "下载中",
    cancelling: "正在停止",
    paused: "已暂停",
    cancelled: "已取消",
    completed: "已下载",
    failed: "下载失败",
    corrupted: "文件损坏",
    missing: "文件缺失",
  } as Record<LocalModel["status"], string>)[model.status];
}

function ScheduleSettings({ connection }: { connection: ScheduleConnection | null }) { return <section className="settings-section"><div className="section-heading"><div><span className="eyebrow">ZJSU UNDERGRADUATE V-9.0</span><h2>浙江工商大学本科教务</h2></div><Link className="button primary" href="/schedule">打开课表工作台</Link></div><article className="panel connection-card"><div className="connection-hero"><span className={`connection-orb state-${connection?.state ?? "disconnected"}`} /><span><strong>{connection?.display_name ?? "尚未初始化"}</strong><small>{connection?.base_url ?? "https://jwxt.zjgsu.edu.cn/jwglxt"}</small></span></div><dl><div><dt>连接状态</dt><dd>{connection?.state ?? "disconnected"}</dd></div><div><dt>同步间隔</dt><dd>{connection?.sync_interval_minutes ?? 360} 分钟</dd></div><div><dt>会话保留</dt><dd>仅加密 Cookie / Session，不保存账号密码</dd></div><div><dt>实时登录</dt><dd>{connection?.capability.live_login ? "已验证" : "等待授权 HAR / 测试账号"}</dd></div></dl><p>{connection?.capability.reason}</p></article></section>; }

function StorageAndDiagnostics({ application, encryption, storage, onError }: { application: ApplicationSettings | null; encryption: boolean; storage?: string; onError: (message: string) => void }) {
  const [snapshot, setSnapshot] = useState<StorageMaintenanceSnapshot | null>(null);
  const [preview, setPreview] = useState<StorageCleanupPreview | null>(null);
  const [busy, setBusy] = useState("");
  const [message, setMessage] = useState("");
  const [retention, setRetention] = useState(String(application?.recording_chunk_retention_days ?? "permanent"));

  const refreshStorage = useCallback(async () => {
    setBusy("refresh");
    onError("");
    try {
      setSnapshot(await mutate<StorageMaintenanceSnapshot>("/maintenance/storage?refresh=true", { method: "GET" }));
    } catch (reason) {
      onError(reason instanceof Error ? reason.message : "存储统计刷新失败");
    } finally { setBusy(""); }
  }, [onError]);

  useEffect(() => {
    const timer = window.setTimeout(() => { void refreshStorage(); }, 0);
    return () => window.clearTimeout(timer);
  }, [refreshStorage]); // 仅进入本页时显式扫描。

  async function saveRetention() {
    setBusy("retention");
    onError("");
    try {
      await mutate<ApplicationSettings>("/settings/application", {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ recording_chunk_retention_days: retention === "permanent" ? null : Number(retention) }),
      });
      setMessage("保留时间已保存。已有录音不会因更改设置而立即被删除。");
      await refreshStorage();
    } catch (reason) {
      onError(reason instanceof Error ? reason.message : "保留时间保存失败");
    } finally { setBusy(""); }
  }

  async function prepareCleanup() {
    setBusy("preview");
    onError("");
    setMessage("");
    try {
      const result = await mutate<StorageCleanupPreview>("/maintenance/storage/preview", { method: "POST" });
      if (!result.recording_count) {
        setMessage("现在没有可安全清理的录音分片。未完成、校验失败或仍在保留期内的分片都会继续保留。");
      } else {
        setPreview(result);
      }
    } catch (reason) {
      onError(reason instanceof Error ? reason.message : "清理预览失败");
    } finally { setBusy(""); }
  }

  async function confirmCleanup() {
    if (!preview) return;
    setBusy("cleanup");
    onError("");
    try {
      const result = await mutate<StorageCleanupResult>("/maintenance/storage/cleanup", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ preview_id: preview.preview_id, confirmed: true }),
      });
      setMessage(`已清理 ${result.cleaned_recordings} 条录音的原始分片，释放 ${formatBytes(result.reclaimed_bytes)}${result.failures.length ? `；${result.failures.length} 条未完成登记，将在下次清理时继续恢复` : ""}。`);
      setPreview(null);
      await refreshStorage();
    } catch (reason) {
      onError(reason instanceof Error ? reason.message : "清理失败");
    } finally { setBusy(""); }
  }

  async function exportDiagnostics() {
    setBusy("diagnostics");
    onError("");
    try {
      const response = await fetch(`${publicApiUrl}/maintenance/diagnostics/export`, { method: "POST" });
      if (!response.ok) {
        const body = await response.json().catch(() => ({})) as { detail?: string };
        throw new Error(body.detail ?? `生成失败 (${response.status})`);
      }
      const blob = await response.blob();
      const url = URL.createObjectURL(blob);
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = `KnowledgeDebt-diagnostics-${new Date().toISOString().slice(0, 10)}.zip`;
      anchor.click();
      window.setTimeout(() => URL.revokeObjectURL(url), 1000);
      setMessage("诊断包已下载到本机，应用没有自动上传。");
    } catch (reason) {
      onError(reason instanceof Error ? reason.message : "诊断包生成失败");
    } finally { setBusy(""); }
  }

  return <section className="settings-section" id="privacy">
    <div className="section-heading"><div><span className="eyebrow">LOCAL STORAGE</span><h2>本地空间管理</h2><p>只在进入本页或点击刷新时统计磁盘，不会影响日常页面速度。</p></div><button className="button secondary" disabled={Boolean(busy)} onClick={refreshStorage}>{busy === "refresh" ? "统计中…" : "刷新占用"}</button></div>
    <div className="storage-layout">
      <article className="panel storage-policy"><h3>录音原始分片保留时间</h3><p>只有录音已完成、最终音频可读且通过完整性校验后，才会进入保留期。转写和课堂资料不会删除。</p><label>保留时间<select aria-label="录音原始分片保留时间" onChange={(event) => setRetention(event.target.value)} value={retention}><option value="7">7 天</option><option value="14">14 天（推荐）</option><option value="30">30 天</option><option value="permanent">永久保留</option></select></label><button className="button secondary" disabled={Boolean(busy)} onClick={saveRetention}>{busy === "retention" ? "保存中…" : "保存保留时间"}</button></article>
      <article className="panel storage-summary"><h3>当前本地占用</h3>{snapshot?.categories.length ? <div className="storage-categories">{snapshot.categories.map((category) => <div key={category.key}><span><strong>{category.label}</strong><small>{category.files} 个文件{category.truncated ? " · 统计已达上限" : ""}</small></span><b>{formatBytes(category.bytes)}</b></div>)}</div> : <p className="muted">{busy === "refresh" ? "正在统计…" : "暂无占用数据，点击“刷新占用”后显示。"}</p>}<div className="cleanup-summary"><strong>{snapshot?.cleanup.eligible_recordings ?? 0} 条录音可清理</strong><span>预计可释放 {formatBytes(snapshot?.cleanup.eligible_bytes ?? 0)}</span><small>{snapshot?.cleanup.protected_recordings ?? 0} 条录音的分片受保护</small></div><button className="button danger" disabled={Boolean(busy) || !(snapshot?.cleanup.eligible_recordings)} onClick={prepareCleanup}>{busy === "preview" ? "正在预览…" : "预览并清理分片"}</button></article>
    </div>
    <article className="panel diagnostic-card"><div><span className="eyebrow">LOCAL DIAGNOSTICS</span><h2>下载诊断包</h2><p><strong>只会下载到本机，绝不自动上传。</strong>包含应用与系统版本、数据库升级版本、组件状态、不敏感的服务标识、任务状态和经过脱敏且截断的运行日志。</p><p>明确不包含：数据库、课表、转写文本、音频、课程文档、笔记和密钥。</p></div><button className="button primary" disabled={Boolean(busy)} onClick={exportDiagnostics}>{busy === "diagnostics" ? "生成中…" : "生成并下载"}</button></article>
    <div className="privacy-grid compact-grid"><article className="panel"><span className="privacy-icon">▣</span><h2>原始文件优先保存</h2><p>失败或未完成的录音永远不会自动清理。当前存储：{storage ?? "local"}。</p></article><article className="panel"><span className="privacy-icon">⌁</span><h2>逐次外发授权</h2><p>取消外发只会保留为“未转写”，不影响原始媒体。</p></article><article className="panel"><span className="privacy-icon">⌘</span><h2>密钥不明文落库</h2><p>{encryption ? "已配置加密主密钥。" : "尚未配置加密主密钥，只允许环境变量引用。"}</p></article></div>
    {message ? <div className="notice success" aria-live="polite">{message}</div> : null}
    {preview ? <div className="modal-backdrop" role="presentation"><div aria-labelledby="cleanup-confirm-title" aria-modal="true" className="consent-modal panel" role="dialog"><span className="eyebrow">CONFIRM CLEANUP</span><h2 id="cleanup-confirm-title">确认清理录音原始分片</h2><p>已重新校验最终音频。本次将清理 <strong>{preview.recording_count}</strong> 条录音的分片，预计释放 <strong>{formatBytes(preview.bytes)}</strong>。最终录音、转写和 Session 不会删除。</p><div className="modal-actions"><button className="button secondary" onClick={() => setPreview(null)}>返回</button><button className="button danger" disabled={busy === "cleanup"} onClick={confirmCleanup}>{busy === "cleanup" ? "清理中…" : "确认清理"}</button></div></div></div> : null}
  </section>;
}

function UsageSettings({ usage }: { usage: ProviderUsage | null }) { return <section className="settings-section"><div className="metric-grid four"><article className="metric-card"><span>本月调用</span><strong>{usage?.request_count ?? 0}</strong><small>所有 Provider 请求</small></article><article className="metric-card"><span>转写分钟</span><strong>{usage?.transcription_minutes ?? 0}</strong><small>按保留媒体时长统计</small></article><article className="metric-card"><span>已知费用</span><strong>¥{usage?.known_cost ?? 0}</strong><small>没有价格则不猜测</small></article><article className="metric-card"><span>失败调用</span><strong>{usage?.failure_count ?? 0}</strong><small>可按任务回溯</small></article></div><article className="panel usage-table"><div className="section-heading"><div><span className="eyebrow">CALL LEDGER</span><h2>调用台账</h2></div><span className="badge">{usage?.month ?? "本月"}</span></div>{usage?.items.length ? usage.items.map((item) => <div className="usage-row" key={item.id}><span><strong>{item.operation}</strong><small>{item.provider_name} · {item.model ?? "未记录模型"}</small></span><span>{item.audio_minutes ? `${item.audio_minutes.toFixed(1)} 分钟` : "—"}</span><span>{item.cost_known ? `${item.estimated_cost}` : "费用未知"}</span><span className={`badge ${item.status === "succeeded" ? "accepted" : "cancelled"}`}>{item.status}</span></div>) : <p className="muted">尚无外部调用记录。默认开发与测试不会消耗付费 API。</p>}</article></section>; }

function statusLabel(value: string) { return ({ available: "可用", tested: "已测试", tested_by_contract: "合同已测试", compatible_preset_unverified: "兼容预设 · 未实测", interface_slot: "接口槽位 · 未启用", user_verified: "需用户验证", local_runtime_required: "本地运行时 · 适配器已实测" } as Record<string, string>)[value] ?? value; }
