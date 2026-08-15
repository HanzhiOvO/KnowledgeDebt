import Link from "next/link";

import type { HomePayload } from "@/types/domain";

export function HomeDashboard({ home }: { home: HomePayload }) {
  const nextSession = home.sessions.find((session) => session.status !== "complete");

  return (
    <>
      <header className="page-header">
        <div>
          <span className="eyebrow">TODAY · KNOWLEDGE DEBT</span>
          <h1>先处理最影响后续学习的缺口</h1>
          <p>课堂资料只是证据；真正的完成标准是通过验收。</p>
        </div>
        <Link className="button primary" href="/courses">
          + 新建 Session
        </Link>
      </header>

      <section className="metric-grid" aria-label="今日概览">
        <article className="metric-card accent">
          <span>待偿还债务</span>
          <strong>{home.open_debt_count}</strong>
          <small>{home.urgent_debt_count} 项高优先级</small>
        </article>
        <article className="metric-card">
          <span>未完成课堂</span>
          <strong>{home.pending_session_count}</strong>
          <small>Session 不依赖录音存在</small>
        </article>
        <article className="metric-card">
          <span>今日最低投入</span>
          <strong>
            {home.minimum_minutes}<em> min</em>
          </strong>
          <small>按债务优先级动态估算</small>
        </article>
      </section>

      <section className="dashboard-grid">
        <div className="panel next-action">
          <div className="section-heading">
            <div>
              <span className="eyebrow">NEXT BEST ACTION</span>
              <h2>下一步</h2>
            </div>
          </div>
          {nextSession ? (
            <>
              <span className="course-chip">{nextSession.course_name ?? "课程"}</span>
              <h3>{nextSession.title}</h3>
              <p>
                还原度 {nextSession.reconstruction_score}% · 学习覆盖 {nextSession.learning_coverage}% ·{" "}
                {nextSession.open_debt_count ?? 0} 项待验收
              </p>
              <Link className="button primary" href={`/sessions/${nextSession.id}`}>
                继续这节课 →
              </Link>
            </>
          ) : (
            <p className="muted">当前没有待处理的 Session。新建一节真实课堂开始收集证据。</p>
          )}
        </div>

        <div className="panel flow-card">
          <span className="eyebrow">THE LOOP</span>
          <h2>不是总结器，是掌握闭环</h2>
          <ol className="flow-list">
            <li><span>01</span> 收集课堂证据</li>
            <li><span>02</span> 还原知识点</li>
            <li><span>03</span> 从零学习</li>
            <li><span>04</span> 验收并清债</li>
          </ol>
        </div>
      </section>

      <section className="panel">
        <div className="section-heading">
          <div>
            <span className="eyebrow">COURSE SESSIONS</span>
            <h2>最近课堂</h2>
          </div>
          <Link href="/courses">全部课程 →</Link>
        </div>
        <div className="session-list">
          {home.sessions.length ? (
            home.sessions.slice(0, 6).map((session) => (
              <Link className="session-row" href={`/sessions/${session.id}`} key={session.id}>
                <span className={session.status === "complete" ? "state-icon complete" : "state-icon"}>
                  {session.status === "complete" ? "✓" : "·"}
                </span>
                <span className="session-main">
                  <strong>{session.title}</strong>
                  <small>{session.course_name ?? "未命名课程"}</small>
                </span>
                <span className="session-score">{session.reconstruction_score}% 还原</span>
                <span className="session-debt">{session.open_debt_count ?? 0} 债务</span>
                <span aria-hidden>→</span>
              </Link>
            ))
          ) : (
            <p className="muted">还没有课堂记录。</p>
          )}
        </div>
      </section>
    </>
  );
}
