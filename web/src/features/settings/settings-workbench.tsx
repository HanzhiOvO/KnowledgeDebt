"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";

import { mutate } from "@/lib/client-api";
import type { LocalASRStatus, ProviderProfile, ProviderSettings, ProviderUsage, ScheduleConnection } from "@/types/domain";

const groups = [
  { id: "ai", label: "AI 分析", capability: "structured_generation" },
  { id: "asr", label: "语音转写", capability: "audio_transcription" },
  { id: "embedding", label: "向量检索", capability: "embeddings" },
] as const;

const adapters = {
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
      ["qwen_dashscope", "通义千问 / DashScope"],
      ["moonshot", "Kimi / Moonshot"],
      ["zhipu_glm", "智谱 GLM"],
      ["minimax", "MiniMax"],
      ["custom_openai_compatible", "自定义兼容接口"],
    ] as Array<[string, string]>,
  },
  local_whisper_cpp: {
    label: "本地 whisper.cpp（命令行）",
    hint: "音频不出本机：调用本地可执行文件，按分片写入带时间戳的转写结果。需自行安装 whisper.cpp 与 ggml 模型。",
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

export function SettingsWorkbench({
  settings,
  usage,
  connection,
  backendError,
}: {
  settings: ProviderSettings | null;
  usage: ProviderUsage | null;
  connection: ScheduleConnection | null;
  backendError?: string;
}) {
  const router = useRouter();
  const [tab, setTab] = useState("providers");
  const [adding, setAdding] = useState(false);
  const [adapter, setAdapter] = useState<AdapterId>("openai_compatible");
  const [busy, setBusy] = useState("");
  const [error, setError] = useState(backendError ?? "");

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
    try {
      await mutate("/settings/providers", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ ...body, adapter, capabilities, external: adapters[adapter].external, enabled: true }),
      });
      setAdding(false);
      router.refresh();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "新增失败");
    } finally { setBusy(""); }
  }

  return (
    <>
      <header className="page-header">
        <div><span className="eyebrow">SETTINGS & PRIVACY</span><h1>连接、能力与数据边界</h1><p>AI、ASR 和 Embedding 分开路由；每次外发都显示实际 Provider、模型与资源。</p></div>
        <span className="local-pill"><i />Local-first</span>
      </header>
      <div className="settings-tabs" role="tablist" aria-label="设置分类">
        {[["providers", "Provider"], ["schedule", "教务同步"], ["privacy", "隐私与存储"], ["usage", "用量与费用"]].map(([id, label]) => <button aria-selected={tab === id} className={tab === id ? "active" : ""} key={id} onClick={() => setTab(id)} role="tab">{label}</button>)}
      </div>
      {error ? <div className="notice error" role="alert">{error}</div> : null}

      {tab === "providers" ? (
        <section className="settings-section">
          <div className="section-heading"><div><span className="eyebrow">ROUTING</span><h2>默认能力路由</h2></div><button className="button primary" onClick={() => setAdding(true)}>＋ 新建 Profile</button></div>
          <div className="routing-grid">
            {groups.map((group) => {
              const capable = settings?.profiles.filter((profile) => profile.enabled && profile.capabilities.some((capability) => capability === group.capability || (group.id === "asr" && capability === "async_audio_transcription"))) ?? [];
              return <label className="routing-card" key={group.id}><span>{group.label}</span><select disabled={busy === group.id || !capable.length} onChange={(event) => setDefault(group.id, event.target.value)} value={settings?.defaults[group.id]?.id ?? ""}><option value="">未配置</option>{capable.map((profile) => <option key={profile.id} value={profile.id}>{profile.name} · {profile.default_model}</option>)}</select><small>{group.id === "asr" ? "控制默认自动转写" : group.id === "embedding" ? "本地 Hash 默认不外发" : "课堂分析与验收"}</small></label>;
            })}
          </div>
          <LocalASRPanel status={settings?.local_asr} />
          <div className="provider-grid">
            {settings?.profiles.map((profile) => <ProviderCard busy={busy === profile.id} key={profile.id} profile={profile} defaults={settings.defaults} onTest={() => testProfile(profile.id)} />)}
          </div>
          {!settings ? <div className="panel review-empty"><h2>后端暂时离线</h2><p>Provider Profile 会在连接恢复后显示。</p></div> : null}
        </section>
      ) : null}

      {tab === "schedule" ? <ScheduleSettings connection={connection} /> : null}
      {tab === "privacy" ? <PrivacySettings encryption={Boolean(settings?.secret_encryption_configured)} storage={settings?.storage_provider} /> : null}
      {tab === "usage" ? <UsageSettings usage={usage} /> : null}

      {adding ? (
        <div className="modal-backdrop" role="presentation">
          <form action={addProfile} className="consent-modal panel profile-form" role="dialog" aria-modal="true" aria-labelledby="profile-title">
            <span className="eyebrow">NEW PROVIDER PROFILE</span><h2 id="profile-title">新增 Provider Profile</h2>
            <label>接入方式<select onChange={(event) => setAdapter(event.target.value as AdapterId)} value={adapter}>{(Object.keys(adapters) as AdapterId[]).map((id) => <option key={id} value={id}>{adapters[id].label}</option>)}</select></label>
            <p className="muted">{adapters[adapter].hint}</p>
            <div className="form-pair"><label>名称<input name="name" required placeholder={adapter === "openai_compatible" ? "我的 OpenAI" : "寝室服务器本地转写"} /></label><label>Vendor<select key={adapter} name="vendor" defaultValue={adapters[adapter].vendors.at(-1)?.[0]}>{adapters[adapter].vendors.map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label></div>
            <label>{adapters[adapter].endpointLabel}<input key={`${adapter}-endpoint`} name="base_url" required={adapters[adapter].endpointRequired} placeholder={adapters[adapter].endpointPlaceholder} /></label>
            <label>{adapters[adapter].modelLabel}<input key={`${adapter}-model`} name="default_model" required={adapter !== "local_whisper_cpp"} placeholder={adapters[adapter].modelPlaceholder} /></label>
            {adapters[adapter].credential ? <div className="form-pair"><label>环境变量引用<input name="credential_reference" placeholder={adapter === "openai_compatible" ? "env:OPENAI_API_KEY" : "env:LOCAL_ASR_TOKEN（本地服务通常不需要）"} /></label><label>或直接输入密钥<input disabled={!settings?.secret_encryption_configured} name="credential" placeholder={settings?.secret_encryption_configured ? "将加密保存" : "需先配置加密主密钥"} type="password" /></label></div> : null}
            <fieldset className="capability-picker"><legend>真实能力（按实际接口勾选）</legend>{adapters[adapter].capabilities.map((capability) => <label key={`${adapter}-${capability}`}><input defaultChecked={(adapters[adapter].preset as readonly string[]).includes(capability)} name="capabilities" type="checkbox" value={capability} />{capability}</label>)}</fieldset>
            <p className="muted">{adapters[adapter].external ? "该 Profile 会被标记为外部：每次转写或分析都需要单独授权。" : "该 Profile 会被强制标记为本地：不外发音频，无需逐次授权。"}</p>
            <div className="modal-actions"><button className="button secondary" onClick={() => setAdding(false)} type="button">取消</button><button className="button primary" disabled={busy === "new"}>{busy === "new" ? "保存中…" : "保存 Profile"}</button></div>
          </form>
        </div>
      ) : null}
    </>
  );
}

function ProviderCard({ profile, defaults, busy, onTest }: { profile: ProviderProfile; defaults: Record<string, ProviderProfile>; busy: boolean; onTest: () => void }) {
  const usedBy = groups.filter((group) => defaults[group.id]?.id === profile.id).map((group) => group.label);
  return <article className="panel provider-card"><header><span className="provider-logo">{profile.name.slice(0, 1).toUpperCase()}</span><span><strong>{profile.name}</strong><small>{profile.vendor} · {profile.adapter}</small></span><span className={`badge ${profile.enabled ? "accepted" : "cancelled"}`}>{profile.enabled ? "已启用" : "已禁用"}</span></header><div className="provider-model"><span>默认模型</span><strong>{profile.default_model || "未设置"}</strong></div><div className="capability-list">{profile.capabilities.length ? profile.capabilities.map((item) => <span key={item}>{item}</span>) : <span>未声明能力</span>}</div><div className="provider-meta"><span>{profile.external ? "外部 · 每次需授权" : "本地"}</span><span>{profile.external ? (profile.credential_configured ? "密钥可用" : "密钥未配置") : "无需密钥"}</span>{usedBy.length ? <span>默认：{usedBy.join(" / ")}</span> : null}</div>{profile.last_test_message ? <p className={profile.last_test_status === "succeeded" ? "test-message success" : "test-message error"}>{profile.last_test_message}</p> : null}<footer><span>{statusLabel(profile.implementation_status)}</span><button className="button ghost" disabled={busy} onClick={onTest}>{busy ? "测试中…" : "测试连接"}</button></footer></article>;
}

function LocalASRPanel({ status }: { status?: LocalASRStatus | null }) {
  if (!status) return null;
  const megabytes = status.model_bytes ? Math.round(status.model_bytes / 1048576) : null;
  return (
    <article className="panel connection-card">
      <div className="connection-hero">
        <span className={`connection-orb ${status.ready ? "state-connected" : "state-error"}`} />
        <span><strong>本地转写 · whisper.cpp</strong><small>{status.ready ? "已就绪：音频只在本机处理，无需逐次授权" : "未就绪：需要安装本地运行时并下载模型"}</small></span>
      </div>
      <dl>
        <div><dt>可执行文件</dt><dd>{status.binary_ready ? status.binary_resolved : `${status.binary || "未配置"} · 未找到`}</dd></div>
        <div><dt>模型</dt><dd>{status.model_ready ? `${status.model_resolved}${megabytes ? ` · ${megabytes} MB` : ""}` : `${status.model || "未配置"} · 未找到`}</dd></div>
        <div><dt>模型目录</dt><dd>{status.model_dir ?? "未配置"}</dd></div>
        <div><dt>语言 / 线程</dt><dd>{status.language} / {status.threads > 0 ? status.threads : "由 whisper.cpp 决定"}</dd></div>
        <div><dt>单分片超时</dt><dd>{status.timeout_seconds} 秒</dd></div>
        <div><dt>FFmpeg</dt><dd>{status.ffmpeg_ready ? "可用" : "缺失 · 非 WAV 分片无法转换"}</dd></div>
      </dl>
      <p>{status.ready ? "可在上方把「语音转写」默认路由指向本地 Profile；长录音仍按分片处理，失败分片可断点续跑。" : "在服务端 .env 设置 KNOWLEDGEDEBT_LOCAL_ASR_BINARY 与 KNOWLEDGEDEBT_LOCAL_ASR_MODEL 后重启 API；安装与选型见 docs/local-asr.md。"}</p>
    </article>
  );
}

function ScheduleSettings({ connection }: { connection: ScheduleConnection | null }) { return <section className="settings-section"><div className="section-heading"><div><span className="eyebrow">ZJSU UNDERGRADUATE V-9.0</span><h2>浙江工商大学本科教务</h2></div><Link className="button primary" href="/schedule">打开课表工作台</Link></div><article className="panel connection-card"><div className="connection-hero"><span className={`connection-orb state-${connection?.state ?? "disconnected"}`} /><span><strong>{connection?.display_name ?? "尚未初始化"}</strong><small>{connection?.base_url ?? "https://jwxt.zjgsu.edu.cn/jwglxt"}</small></span></div><dl><div><dt>连接状态</dt><dd>{connection?.state ?? "disconnected"}</dd></div><div><dt>同步间隔</dt><dd>{connection?.sync_interval_minutes ?? 360} 分钟</dd></div><div><dt>会话保留</dt><dd>仅加密 Cookie / Session，不保存账号密码</dd></div><div><dt>实时登录</dt><dd>{connection?.capability.live_login ? "已验证" : "等待授权 HAR / 测试账号"}</dd></div></dl><p>{connection?.capability.reason}</p></article></section>; }

function PrivacySettings({ encryption, storage }: { encryption: boolean; storage?: string }) { return <section className="settings-section privacy-grid"><article className="panel"><span className="privacy-icon">▣</span><h2>原始文件优先保存</h2><p>转写、匹配或 Provider 失败都不会删除原始媒体。当前存储：{storage ?? "local"}。</p><span className="badge accepted">默认开启</span></article><article className="panel"><span className="privacy-icon">⌁</span><h2>逐次外发授权</h2><p>确认框列出实际 Vendor、模型、资源和数据类型；取消只会保留为“未转写”。</p><span className="badge accepted">强制执行</span></article><article className="panel"><span className="privacy-icon">⌘</span><h2>密钥不明文落库</h2><p>{encryption ? "已配置后端加密主密钥，可以加密保存 Profile 密钥。" : "尚未配置加密主密钥，只允许 env:VARIABLE 引用。"}</p><span className={`badge ${encryption ? "accepted" : "scheduled"}`}>{encryption ? "已加密" : "环境引用模式"}</span></article></section>; }

function UsageSettings({ usage }: { usage: ProviderUsage | null }) { return <section className="settings-section"><div className="metric-grid four"><article className="metric-card"><span>本月调用</span><strong>{usage?.request_count ?? 0}</strong><small>所有 Provider 请求</small></article><article className="metric-card"><span>转写分钟</span><strong>{usage?.transcription_minutes ?? 0}</strong><small>按保留媒体时长统计</small></article><article className="metric-card"><span>已知费用</span><strong>¥{usage?.known_cost ?? 0}</strong><small>没有价格则不猜测</small></article><article className="metric-card"><span>失败调用</span><strong>{usage?.failure_count ?? 0}</strong><small>可按任务回溯</small></article></div><article className="panel usage-table"><div className="section-heading"><div><span className="eyebrow">CALL LEDGER</span><h2>调用台账</h2></div><span className="badge">{usage?.month ?? "本月"}</span></div>{usage?.items.length ? usage.items.map((item) => <div className="usage-row" key={item.id}><span><strong>{item.operation}</strong><small>{item.provider_name} · {item.model ?? "未记录模型"}</small></span><span>{item.audio_minutes ? `${item.audio_minutes.toFixed(1)} 分钟` : "—"}</span><span>{item.cost_known ? `${item.estimated_cost}` : "费用未知"}</span><span className={`badge ${item.status === "succeeded" ? "accepted" : "cancelled"}`}>{item.status}</span></div>) : <p className="muted">尚无外部调用记录。默认开发与测试不会消耗付费 API。</p>}</article></section>; }

function statusLabel(value: string) { return ({ available: "可用", tested: "已测试", tested_by_contract: "合同已测试", compatible_preset_unverified: "兼容预设 · 未实测", interface_slot: "接口槽位 · 未启用", user_verified: "需用户验证", local_runtime_required: "本地运行时 · 适配器已实测" } as Record<string, string>)[value] ?? value; }
