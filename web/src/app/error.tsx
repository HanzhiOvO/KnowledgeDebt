"use client";

import { useEffect } from "react";

export default function ErrorBoundary({ error, retry }: { error: Error & { digest?: string }; retry: () => void }) {
  useEffect(() => {
    console.error(error);
  }, [error]);

  return (
    <main className="page-stack wide-page">
      <section className="panel review-empty" role="alert">
        <span className="empty-glyph">!</span>
        <span className="eyebrow">WORKBENCH ERROR</span>
        <h1>页面暂时没有加载成功</h1>
        <p>原始文件和后台任务不会因此被删除。你可以重试；若问题持续，请检查本地后端是否已启动。</p>
        {error.digest ? <small className="muted">错误编号：{error.digest}</small> : null}
        <button className="button primary" onClick={() => retry()}>重新加载</button>
      </section>
    </main>
  );
}
