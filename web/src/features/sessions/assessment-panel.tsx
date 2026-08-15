"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";

import { mutate, publicApiUrl } from "@/lib/client-api";
import type { AssessmentQuestion, ConsentManifest, KnowledgePoint } from "@/types/domain";
import { JobAction } from "./job-action";

interface AnswerResult {
  evaluation: { score: number; verdict: string; feedback: string; missing_criteria: string[] };
  mastery_updates: Array<{ knowledge_point_id: string; title: string; mastery: number; status: string }>;
  follow_up_questions: AssessmentQuestion[];
  session_status: string;
}

export function AssessmentPanel({
  sessionId,
  questions: initialQuestions,
  knowledgePoints,
}: {
  sessionId: string;
  questions: AssessmentQuestion[];
  knowledgePoints: KnowledgePoint[];
}) {
  const router = useRouter();
  const [questions, setQuestions] = useState(initialQuestions);
  const [index, setIndex] = useState(0);
  const [answer, setAnswer] = useState("");
  const [result, setResult] = useState<AnswerResult | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [manifest, setManifest] = useState<ConsentManifest | null>(null);
  const [confirmed, setConfirmed] = useState(false);
  const question = questions[index];
  const pointNames = new Map(knowledgePoints.map((point) => [point.id, point.title]));

  if (!questions.length) {
    return (
      <div className="assessment-intro">
        <span className="eyebrow">MASTERY ASSESSMENT</span>
        <h2>看过不等于掌握</h2>
        <p>系统会生成可覆盖多个关联知识点的高信息量题目。每次作答都保存为独立 MasteryEvidence；单题不会直接清空债务。</p>
        <JobAction sessionId={sessionId} kind="assessment" label="生成自适应验收" />
      </div>
    );
  }

  async function submit() {
    if (!answer.trim() || !question) return;
    const response = await fetch(`${publicApiUrl}/sessions/${sessionId}/consent-manifest?operation=assessment`);
    if (!response.ok) {
      setError("无法读取隐私清单");
      return;
    }
    const nextManifest = (await response.json()) as ConsentManifest;
    if (nextManifest.confirmation_required) {
      setManifest(nextManifest);
      return;
    }
    await sendAnswer(false);
  }

  async function sendAnswer(consent: boolean) {
    if (!question) return;
    setManifest(null);
    setBusy(true);
    setError("");
    try {
      const next = await mutate<AnswerResult>(`/questions/${question.id}/answer`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ answer, confirm_external_upload: consent }),
      });
      setResult(next);
      if (next.follow_up_questions.length) {
        setQuestions((current) => [...current, ...next.follow_up_questions]);
      }
      router.refresh();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "提交失败");
    } finally {
      setBusy(false);
    }
  }

  function nextQuestion() {
    setIndex((value) => Math.min(value + 1, questions.length - 1));
    setAnswer("");
    setResult(null);
  }

  return (
    <div className="quiz-layout">
      <div className="quiz-progress"><span style={{ width: `${((index + 1) / questions.length) * 100}%` }} /></div>
      <div className="quiz-meta"><span>{index + 1} / {questions.length}</span><span>{question.question_type} · {question.level}</span></div>
      <div className="point-chips">{question.knowledge_point_ids.map((id) => <span key={id}>{pointNames.get(id) ?? "Knowledge Point"}</span>)}</div>
      <h2>{question.prompt}</h2>
      {!result ? (
        <>
          <textarea value={answer} onChange={(event) => setAnswer(event.target.value)} placeholder="用自己的话解释推理过程；等价表达不会被扣分。" rows={7} />
          {error ? <p className="form-error">{error}</p> : null}
          <button className="button primary" disabled={busy || !answer.trim()} onClick={submit}>{busy ? "正在验收…" : "提交答案"}</button>
          {manifest ? <section className="inline-consent"><span className="eyebrow">SEND TO {manifest.provider}</span><p>将发送：{manifest.will_send.join("、")}。不会发送：{manifest.will_not_send.join("、")}。</p><p>涉及资源：{manifest.resources.map((resource) => resource.name).join("、")}</p><label><input type="checkbox" checked={confirmed} onChange={(event) => setConfirmed(event.target.checked)} />仅同意本次答案验收</label><div><button className="button secondary" onClick={() => setManifest(null)}>取消</button><button className="button primary" disabled={!confirmed} onClick={() => sendAnswer(true)}>确认并提交</button></div></section> : null}
        </>
      ) : (
        <section className={result.evaluation.score >= 0.75 ? "result-card passed" : "result-card"}>
          <span className="eyebrow">{result.evaluation.verdict} · {Math.round(result.evaluation.score * 100)}%</span>
          <h3>{result.evaluation.feedback}</h3>
          {result.mastery_updates.map((update) => <p key={update.knowledge_point_id}>{update.title}：掌握度 {update.mastery} · {update.status}</p>)}
          {result.follow_up_questions.length ? <p>已根据缺口追加 {result.follow_up_questions.length} 道针对性追问。</p> : null}
          {index + 1 < questions.length || result.follow_up_questions.length ? <button className="button primary" onClick={nextQuestion}>下一题 →</button> : <p><strong>{result.session_status === "complete" ? "Session 已清债" : "本轮完成，仍需更多证据"}</strong></p>}
        </section>
      )}
    </div>
  );
}
