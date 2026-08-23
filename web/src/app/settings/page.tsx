import type { Metadata } from "next";

import { getProviderSettings } from "@/lib/api";

export const metadata: Metadata = { title: "设置" };

const providerLabels: Record<string, string> = {
  local_rule: "本地规则引擎",
  openai_compatible: "OpenAI Compatible",
  hash: "本地 Hash Embedding",
};

export default async function SettingsPage() {
  const result = await getProviderSettings();
  const settings = result.ok ? result.data : {};
  const localMode = Boolean(settings.local_mode);
  const aiLabel = String(settings.ai_provider ? (providerLabels[String(settings.ai_provider)] ?? settings.ai_provider) : "未配置");
  const asrLabel = String(settings.asr_provider ? (providerLabels[String(settings.asr_provider)] ?? settings.asr_provider) : "未配置");
  const storageLabel = settings.storage_provider === "s3" ? "S3 兼容对象存储" : "本地文件系统";

  return (
    <main className="page-stack">
      <header className="page-header"><div><span className="eyebrow">SETTINGS</span><h1>部署与隐私边界</h1><p>默认薄后端；重型 AI、ASR 与 Embedding 由可替换 Provider 承担。</p></div></header>
      <section className="settings-grid">
        <article className="panel">
          <span className="eyebrow">STORAGE</span>
          <h2>{result.ok ? storageLabel : "后端离线"}</h2>
          <p>上传资料保存在自托管服务器。可通过 <code>KNOWLEDGEDEBT_STORAGE_PROVIDER</code> 切换 S3-compatible StorageProvider。</p>
          <span className={result.ok ? "status-badge complete" : "status-badge"}>{result.ok ? `● ${storageLabel}` : "○ 未连接"}</span>
        </article>
        <article className="panel">
          <span className="eyebrow">AI PROVIDER</span>
          <h2>{result.ok ? aiLabel : "后端离线"}</h2>
          <p>{localMode ? "零配置模式：只在服务器本地整理已检索资料，不会联网，也不会伪造课堂内容。" : "只有在操作前确认的具体资源才会发送给外部 Provider。"}</p>
          <span className={result.ok && settings.configured ? "status-badge complete" : "status-badge"}>{result.ok && settings.configured ? "● 已就绪" : "○ 未配置"}</span>
        </article>
        <article className="panel">
          <span className="eyebrow">ACCESS & ASR</span>
          <h2>{result.ok ? (settings.access_token_configured ? "访问令牌已启用" : "本地免登录") : "后端离线"}</h2>
          <p>ASR：{result.ok ? asrLabel : "未知"}（{result.ok && settings.asr_configured ? "已就绪" : "本地模式不包含转写"}）。暴露到网络时应设置单用户访问令牌。</p>
          <span className={result.ok && settings.access_token_configured ? "status-badge complete" : "status-badge"}>{result.ok && settings.access_token_configured ? "● Protected" : "○ Local mode"}</span>
        </article>
      </section>
    </main>
  );
}
