import type { Metadata } from "next";

import { EmptyState } from "@/components/empty-state";
import { getDebts } from "@/lib/api";

export const metadata: Metadata = { title: "知识债务" };

export default async function DebtsPage() {
  const result = await getDebts();
  return (
    <main className="page-stack">
      <header className="page-header"><div><span className="eyebrow">KNOWLEDGE DEBT</span><h1>尚未通过验收的知识</h1><p>优先处理会阻塞后续课程的关键缺口。</p></div></header>
      {!result.ok ? <EmptyState eyebrow="BACKEND OFFLINE" title="暂时无法读取债务" detail={result.error} /> : (
        <section className="panel debt-table">
          {result.data.length ? result.data.map((debt) => (
            <article className="debt-row large" key={debt.id}>
              <span className={`priority priority-${debt.priority}`}>P{debt.priority}</span>
              <span><strong>{debt.title}</strong><small>{debt.description}</small></span>
              <span>{debt.estimated_minutes} min</span>
              <span className={`debt-state ${debt.status}`}>{debt.status}</span>
            </article>
          )) : <p className="muted">当前没有知识债务。新建并分析 Session 后，待掌握知识点会显示在这里。</p>}
        </section>
      )}
    </main>
  );
}
