import Link from "next/link";

import type { HomePayload, Job, ScheduleOccurrence } from "@/types/domain";

const automationLabels: Record<string, string> = {
  awaiting_consent: "等待外部转写授权",
  queued: "转写已排队",
  preparing: "正在准备媒体",
  transcribing: "正在转写",
  partial: "部分完成，可重试",
  failed: "转写失败，可重试",
  saved: "文件已保存",
};

export function HomeDashboard({ home }: { home: HomePayload }) {
  const today = new Intl.DateTimeFormat("zh-CN", {
    month: "long",
    day: "numeric",
    weekday: "long",
  }).format(new Date());
  const activeJobs = (home.jobs ?? []).filter((job) => ["queued", "running"].includes(job.status));

  return (
    <>
      <header className="hero-header">
        <div>
          <span className="eyebrow">AUTOMATED COURSE WORKBENCH · {today}</span>
          <h1>今天的课堂，系统已经替你接住。</h1>
          <p>课表、录音、资料和转写先可靠归档；不确定的结果留给你审核。</p>
        </div>
        <div className="hero-actions">
          <Link className="button secondary" href="/schedule">查看本周课表</Link>
          <Link className="button primary" href="/review#inbox">＋ 快速收件</Link>
        </div>
      </header>

      <section className="metric-grid four" aria-label="工作台概览">
        <Metric label="今日课程" value={(home.today_occurrences ?? []).length} detail="来自当前学期课表" tone="blue" />
        <Metric label="待审核" value={home.pending_review_count ?? 0} detail="低置信结果不会静默覆盖" tone="violet" />
        <Metric label="自动任务" value={activeJobs.length} detail="可刷新、可恢复、可重试" tone="amber" />
        <Metric label="知识债务" value={home.open_debt_count} detail={`${home.urgent_debt_count} 项高优先级`} tone="rose" />
      </section>

      <section className="overview-layout">
        <article className="panel today-panel">
          <div className="section-heading">
            <div><span className="eyebrow">TODAY</span><h2>今日课程</h2></div>
            <Link href="/schedule">完整课表 →</Link>
          </div>
          <div className="today-timeline">
            {(home.today_occurrences ?? []).length ? home.today_occurrences.map((occurrence) => (
              <TodayCourse key={occurrence.id} occurrence={occurrence} />
            )) : (
              <div className="compact-empty"><span>今天没有已同步课程</span><Link href="/schedule">连接或导入课表</Link></div>
            )}
          </div>
        </article>

        <article className="panel automation-panel">
          <div className="section-heading">
            <div><span className="eyebrow">AUTOMATION</span><h2>自动化队列</h2></div>
            <span className="count-chip">{(home.pending_automation ?? []).length + activeJobs.length}</span>
          </div>
          <div className="automation-list">
            {(home.pending_automation ?? []).slice(0, 5).map((item) => (
              <Link href={`/sessions/${item.session_id}`} className="automation-row" key={item.resource_id}>
                <span className={`state-dot state-${item.state}`} />
                <span><strong>{item.name}</strong><small>{automationLabels[item.state] ?? item.state}</small></span>
                <span aria-hidden>→</span>
              </Link>
            ))}
            {activeJobs.slice(0, 3).map((job) => <JobRow job={job} key={job.id} />)}
            {!home.pending_automation?.length && !activeJobs.length ? (
              <div className="compact-empty"><span>当前没有等待处理的自动任务</span><small>上传资料后会在这里显示进度</small></div>
            ) : null}
          </div>
        </article>
      </section>

      <section className="panel recent-panel">
        <div className="section-heading">
          <div><span className="eyebrow">RECENT SESSIONS</span><h2>最近课堂</h2></div>
          <Link href="/courses">全部课程 →</Link>
        </div>
        <div className="session-list">
          {home.sessions.length ? home.sessions.slice(0, 7).map((session) => (
            <Link className="session-row" href={`/sessions/${session.id}`} key={session.id}>
              <span className={session.status === "complete" ? "state-icon complete" : "state-icon"}>{session.status === "complete" ? "✓" : "·"}</span>
              <span className="session-main"><strong>{session.title}</strong><small>{session.course_name ?? "未命名课程"}</small></span>
              <span className="session-score">{session.reconstruction_score}% 还原</span>
              <span className="session-debt">{session.open_debt_count ?? 0} 项债务</span>
              <span aria-hidden>→</span>
            </Link>
          )) : <div className="compact-empty"><span>还没有课堂记录</span><Link href="/courses">创建第一门课程</Link></div>}
        </div>
      </section>
    </>
  );
}

function Metric({ label, value, detail, tone }: { label: string; value: number; detail: string; tone: string }) {
  return <article className={`metric-card tone-${tone}`}><span>{label}</span><strong>{value}</strong><small>{detail}</small></article>;
}

function TodayCourse({ occurrence }: { occurrence: ScheduleOccurrence }) {
  const time = new Intl.DateTimeFormat("zh-CN", { hour: "2-digit", minute: "2-digit", hour12: false }).format(new Date(occurrence.starts_at));
  const content = (
    <>
      <time>{time}</time>
      <span className="timeline-marker" />
      <span className="today-course-main">
        <strong>{occurrence.rule.course_name}</strong>
        <small>{[occurrence.building, occurrence.room, occurrence.teacher].filter(Boolean).join(" · ") || "地点待同步"}</small>
      </span>
      <span className={`badge ${occurrence.status}`}>{occurrence.status === "cancelled" ? "已取消" : occurrence.session_id ? "已建立 Session" : "待发生"}</span>
    </>
  );
  return occurrence.session_id ? <Link className="today-course" href={`/sessions/${occurrence.session_id}`}>{content}</Link> : <div className="today-course">{content}</div>;
}

function JobRow({ job }: { job: Job }) {
  return <div className="automation-row"><span className="state-dot state-running" /><span><strong>{job.kind}</strong><small>{job.stage} · {job.progress}%</small></span><span className="mini-progress"><i style={{ width: `${job.progress}%` }} /></span></div>;
}
