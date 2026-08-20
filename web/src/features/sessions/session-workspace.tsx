"use client";

import { useState } from "react";

import { ProgressRing } from "@/components/progress-ring";
import { mutate } from "@/lib/client-api";
import type { AssessmentQuestion, SessionDetail, SourceRef } from "@/types/domain";
import { useRouter } from "next/navigation";
import { AssessmentPanel } from "./assessment-panel";
import { JobAction } from "./job-action";
import { ResourcePanel } from "./resource-panel";

const tabs = ["概览", "录音与转写", "资料", "课堂还原", "学习", "验收"] as const;
type Tab = (typeof tabs)[number];

export function SessionWorkspace({ session, questions }: { session: SessionDetail; questions: AssessmentQuestion[] }) {
  const [tab, setTab] = useState<Tab>("概览");

  return (
    <>
      <div className="score-strip">
        <ProgressRing value={session.reconstruction_score} label="还原度" />
        <ProgressRing value={session.learning_coverage} label="学习覆盖" />
        <div className="debt-counter"><strong>{session.debts.filter((item) => item.status !== "mastered").length}</strong><span>待偿还债务</span></div>
        <div className="completion-rule"><span className="status-dot" /><p><strong>完成条件</strong><br />所有知识点均通过真实资料驱动的 Mastery Assessment</p></div>
      </div>
      <div className="tabs" role="tablist" aria-label="Session 工作区">
        {tabs.map((item) => <button role="tab" aria-selected={tab === item} className={tab === item ? "active" : ""} key={item} onClick={() => setTab(item)}>{item}</button>)}
      </div>
      <section className="workspace-panel panel">
        {tab === "概览" ? <Overview session={session} /> : null}
        {tab === "录音与转写" ? <ResourcePanel mode="media" sessionId={session.id} resources={session.resources} /> : null}
        {tab === "资料" ? <ResourcePanel mode="resources" sessionId={session.id} resources={session.resources} /> : null}
        {tab === "课堂还原" ? <ReconstructionView session={session} /> : null}
        {tab === "学习" ? <LearningPath session={session} /> : null}
        {tab === "验收" ? <AssessmentPanel sessionId={session.id} questions={questions} knowledgePoints={session.knowledge_points} /> : null}
      </section>
    </>
  );
}

function Overview({ session }: { session: SessionDetail }) {
  return (
    <div className="overview-grid">
      <div>
        <span className="eyebrow">SESSION STATE</span>
        <h2>{session.status === "complete" ? "这节课已完成" : "这节课仍在学习中"}</h2>
        <p>{session.notes || "没有现场备注。你仍可添加资料并开始还原。"}</p>
        <div className="evidence-summary">
          <div><strong>{session.resources.length}</strong><span>资料</span></div>
          <div><strong>{session.knowledge_points.length}</strong><span>知识点</span></div>
          <div><strong>{session.learning_steps.filter((item) => item.completed).length}/{session.learning_steps.length}</strong><span>学习步骤</span></div>
        </div>
      </div>
      <div className="debt-stack">
        <div className="section-heading"><h3>优先债务</h3><span className="count-chip">{session.debts.length}</span></div>
        {session.debts.slice(0, 5).map((debt) => (
          <article className="debt-row" key={debt.id}>
            <span className={`priority priority-${debt.priority}`}>P{debt.priority}</span>
            <span><strong>{debt.title}</strong><small>掌握 {debt.current_mastery.toFixed(1)} / {debt.target_mastery}</small></span>
            <span className={`debt-state ${debt.status}`}>{debt.status}</span>
          </article>
        ))}
        {!session.debts.length ? <p className="muted">分析课堂资料后，待掌握知识点会出现在这里。</p> : null}
      </div>
    </div>
  );
}

function ReconstructionView({ session }: { session: SessionDetail }) {
  const reconstruction = session.reconstruction;
  if (!reconstruction) return <div className="empty-pane"><span className="empty-glyph">∅</span><h2>尚未还原课堂</h2><p>添加至少一份证据后运行分析；没有证据时系统不会编造课堂内容。</p><JobAction sessionId={session.id} kind="analysis" label="运行课堂分析" /></div>;
  return (
    <div className="reconstruction-layout">
      <div><span className="eyebrow">EVIDENCE-BACKED RECONSTRUCTION</span><h2>{reconstruction.title}</h2><p className="lead">{reconstruction.summary}</p></div>
      <div className="timeline">
        {reconstruction.timeline.map((item, index) => (
          <article className="timeline-item" key={`${item.title}-${index}`}>
            <span className="timeline-time">{formatTime(item.start_time)}</span>
            <div><h3>{item.title}</h3><p>{item.summary}</p><SourceList sources={item.sources} /></div>
          </article>
        ))}
        {!reconstruction.timeline.length ? <p className="muted">当前证据没有足够可靠的时间定位，因此不显示虚构时间线。</p> : null}
      </div>
      <div className="confidence-grid">
        <div><span className="eyebrow">CONFIRMED</span>{reconstruction.confirmed.map((item) => <p key={item}>✓ {item}</p>)}</div>
        <div><span className="eyebrow">INFERRED</span>{reconstruction.inferred.map((item) => <p key={item}>? {item}</p>)}</div>
      </div>
    </div>
  );
}

function LearningPath({ session }: { session: SessionDetail }) {
  const router = useRouter();
  if (!session.learning_steps.length) return <EmptyPane title="学习路径尚未生成" detail="路径会从零解释，并把每个结论绑定回真实课程资料。" />;
  async function complete(stepId: string) {
    await mutate(`/learning-steps/${stepId}/complete`, { method: "POST" });
    router.refresh();
  }
  return (
    <div className="learning-list">
      {session.learning_steps.map((step) => (
        <article className={step.completed ? "learning-step completed" : "learning-step"} key={step.id}>
          <span className="step-number">{String(step.position).padStart(2, "0")}</span>
          <div><span className="eyebrow">{step.estimated_minutes} MIN</span><h3>{step.title}</h3><p>{step.brief_explanation}</p><details><summary>展开解释</summary><p>{step.full_explanation}</p><SourceList sources={step.sources} /></details></div>
          <button className="step-state" disabled={step.completed} onClick={() => complete(step.id)}>{step.completed ? "✓ 已学习" : "标记学完"}</button>
        </article>
      ))}
    </div>
  );
}

function SourceList({ sources }: { sources: SourceRef[] }) {
  return <div className="source-list">{sources.map((source, index) => <span key={`${source.resource_id}-${index}`}>{source.label}{source.locator ? ` · ${source.locator}` : ""}</span>)}</div>;
}

function EmptyPane({ title, detail }: { title: string; detail: string }) {
  return <div className="empty-pane"><span className="empty-glyph">∅</span><h2>{title}</h2><p>{detail}</p></div>;
}

function formatTime(value?: number | null) {
  if (value == null) return "—";
  return `${Math.floor(value / 60)}:${String(Math.floor(value % 60)).padStart(2, "0")}`;
}
