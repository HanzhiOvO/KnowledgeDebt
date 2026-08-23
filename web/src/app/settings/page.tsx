import type { Metadata } from "next";

import { EmptyState } from "@/components/empty-state";
import { ProviderSettingsForm } from "@/features/settings/provider-settings-form";
import { getProviderOptions } from "@/lib/api";

export const metadata: Metadata = { title: "设置" };

const providerLabels: Record<string, string> = {
  local_rule: "本地规则引擎",
  local_whisper: "本地 Whisper",
  openai_compatible: "OpenAI Compatible",
  hash: "本地 Hash Embedding",
};

export default async function SettingsPage() {
  const result = await getProviderOptions();

  if (!result.ok) {
    return (
      <main className="page-stack">
        <EmptyState eyebrow="SETTINGS" title="无法读取设置" detail={result.error} />
      </main>
    );
  }

  const current = result.data.current;
  const aiLabel = current.ai_label ?? providerLabels[current.ai_provider] ?? current.ai_provider;
  const asrLabel = providerLabels[current.asr_provider] ?? current.asr_provider;
  const storageLabel = current.storage_provider === "s3" ? "S3 兼容对象存储" : "本地文件系统";
  const localMode = current.local_mode;

  return (
    <main className="page-stack">
      <header className="page-header">
        <div>
          <span className="eyebrow">SETTINGS</span>
          <h1>部署与隐私边界</h1>
          <p>默认薄后端；重型 AI、ASR 与 Embedding 由可替换 Provider 承担。</p>
        </div>
      </header>

      <ProviderSettingsForm options={result.data} />

      <section className="settings-grid">
        <article className="panel">
          <span className="eyebrow">STORAGE</span>
          <h2>{storageLabel}</h2>
          <p>上传资料保存在自托管服务器。可通过 <code>KNOWLEDGEDEBT_STORAGE_PROVIDER</code> 切换 S3-compatible StorageProvider。</p>
          <span className="status-badge complete">● {storageLabel}</span>
        </article>
        <article className="panel">
          <span className="eyebrow">AI PROVIDER</span>
          <h2>{aiLabel}</h2>
          <p>{localMode ? "零配置模式：只在服务器本地整理已检索资料，不会联网，也不会伪造课堂内容。" : `当前模型：${current.ai_model}。只有操作前确认的具体资源才会发送给外部 Provider。`}</p>
          <span className={current.configured ? "status-badge complete" : "status-badge"}>{current.configured ? "● 已就绪" : "○ 未配置"}</span>
        </article>
        <article className="panel">
          <span className="eyebrow">ASR & ACCESS</span>
          <h2>{asrLabel}</h2>
          <p>
            {current.asr_provider === "local_whisper"
              ? `本地中英文语音识别，模型 ${current.local_asr_model ?? "small"}，音频不会离开服务器。`
              : `ASR：${asrLabel}。`}
            访问令牌：{current.access_token_configured ? "已启用" : "本地免登录"}。
          </p>
          <span className={current.asr_configured || current.access_token_configured ? "status-badge complete" : "status-badge"}>
            {current.asr_configured ? "● ASR 可用" : "○ 需安装/配置"}{current.access_token_configured ? " · Token" : " · Local"}
          </span>
        </article>
      </section>
    </main>
  );
}
