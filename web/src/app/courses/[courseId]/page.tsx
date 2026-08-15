import Link from "next/link";
import type { Metadata } from "next";

import { EmptyState } from "@/components/empty-state";
import { SessionForm } from "@/features/courses/session-form";
import { getCourse } from "@/lib/api";

export const metadata: Metadata = { title: "课程详情" };

export default async function CoursePage(props: PageProps<"/courses/[courseId]">) {
  const { courseId } = await props.params;
  const result = await getCourse(courseId);
  if (!result.ok) {
    return <main className="page-stack"><EmptyState eyebrow="COURSE" title="无法读取课程" detail={result.error} actionHref="/courses" actionLabel="返回课程" /></main>;
  }
  const course = result.data;
  return (
    <main className="page-stack">
      <header className="page-header">
        <div><Link className="back-link" href="/courses">← 所有课程</Link><span className="eyebrow">{course.semester || "COURSE"}</span><h1>{course.name}</h1><p>{course.description || "为每次真实课堂建立独立 Session。"}</p></div>
        <SessionForm courseId={course.id} />
      </header>
      <section className="panel">
        <div className="section-heading"><div><span className="eyebrow">SESSIONS</span><h2>课堂记录</h2></div><span className="count-chip">{course.sessions?.length ?? 0}</span></div>
        <div className="session-list">
          {course.sessions?.length ? course.sessions.map((session) => (
            <Link className="session-row" href={`/sessions/${session.id}`} key={session.id}>
              <span className={session.status === "complete" ? "state-icon complete" : "state-icon"}>{session.status === "complete" ? "✓" : "·"}</span>
              <span className="session-main"><strong>{session.title}</strong><small>{session.starts_at ? new Date(session.starts_at).toLocaleDateString("zh-CN") : "未设置时间"}</small></span>
              <span className="session-score">{session.reconstruction_score}% 还原</span>
              <span className="session-debt">{session.status === "complete" ? "已清债" : "进行中"}</span>
              <span aria-hidden>→</span>
            </Link>
          )) : <p className="muted">创建 Session 后，无论是否有资料，它都会保留为一节合法课堂。</p>}
        </div>
      </section>
    </main>
  );
}
