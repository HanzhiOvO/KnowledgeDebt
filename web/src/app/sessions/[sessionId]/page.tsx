import Link from "next/link";
import type { Metadata } from "next";

import { EmptyState } from "@/components/empty-state";
import { SessionTitleEditor } from "@/features/sessions/session-title-editor";
import { SessionWorkspace } from "@/features/sessions/session-workspace";
import { getAssessment, getSession } from "@/lib/api";

export const metadata: Metadata = { title: "Session 工作台" };

export default async function SessionPage(props: PageProps<"/sessions/[sessionId]">) {
  const { sessionId } = await props.params;
  const [result, assessment] = await Promise.all([getSession(sessionId), getAssessment(sessionId)]);
  if (!result.ok) return <main className="page-stack"><EmptyState eyebrow="SESSION" title="无法读取这节课" detail={result.error} actionHref="/courses" actionLabel="返回课程" /></main>;
  const session = result.data;
  return (
    <main className="page-stack session-page">
      <header className="page-header compact">
        <div><Link className="back-link" href={`/courses/${session.course_id}`}>← 返回课程</Link><span className="eyebrow">COURSE SESSION</span><h1>{session.title}</h1><SessionTitleEditor initialTitle={session.title} locked={session.automation?.title_locked} sessionId={session.id} source={session.automation?.title_source} /><p>{session.status === "complete" ? "知识债务已清零" : "资料 → 还原 → 学习 → 验收"}</p></div>
        <span className={session.status === "complete" ? "status-badge complete" : "status-badge"}>{session.status === "complete" ? "✓ 已完成" : "● 进行中"}</span>
      </header>
      <SessionWorkspace session={session} questions={assessment.ok ? assessment.data : []} />
    </main>
  );
}
