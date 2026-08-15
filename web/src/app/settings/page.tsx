import type { Metadata } from "next";

import { getProviderSettings } from "@/lib/api";

export const metadata: Metadata = { title: "设置" };

export default async function SettingsPage() {
  const result = await getProviderSettings();
  return (
    <main className="page-stack">
      <header className="page-header"><div><span className="eyebrow">SETTINGS</span><h1>部署与隐私边界</h1><p>默认薄后端；重型 AI、ASR 与 Embedding 由可替换 Provider 承担。</p></div></header>
      <section className="settings-grid">
        <article className="panel"><span className="eyebrow">STORAGE</span><h2>本地文件系统</h2><p>上传资料保存在自托管服务器。后续可切换 S3-compatible StorageProvider。</p><span className="status-badge complete">● Local</span></article>
        <article className="panel"><span className="eyebrow">AI PROVIDER</span><h2>{result.ok ? String(result.data.ai_provider ?? "未配置") : "后端离线"}</h2><p>只有在操作前确认的具体资源才会发送给外部 Provider。</p><span className={result.ok && result.data.configured ? "status-badge complete" : "status-badge"}>{result.ok && result.data.configured ? "● 已配置" : "○ 未配置"}</span></article>
        <article className="panel"><span className="eyebrow">ACCESS</span><h2>本地免登录</h2><p>默认只监听本机。暴露到网络时应配置单用户访问令牌；托管多用户架构属于后续能力。</p><span className="status-badge">○ Local mode</span></article>
      </section>
    </main>
  );
}
