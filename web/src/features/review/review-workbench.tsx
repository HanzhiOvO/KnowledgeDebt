"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";

import { mutate, publicApiUrl } from "@/lib/client-api";
import type { InboxItem, ReviewItem, SessionSummary } from "@/types/domain";

const kindLabels = {
  archive_match: "归档匹配",
  session_topic: "课堂主题",
  schedule_conflict: "课表冲突",
  transcription_failure: "转写异常",
};

export function ReviewWorkbench({
  reviews,
  inbox,
  sessions,
  backendError,
}: {
  reviews: ReviewItem[];
  inbox: InboxItem[];
  sessions: SessionSummary[];
  backendError?: string;
}) {
  const router = useRouter();
  const [busyId, setBusyId] = useState<string | null>(null);
  const [error, setError] = useState(backendError ?? "");
  const [uploading, setUploading] = useState(false);

  async function decide(review: ReviewItem, action: "accept" | "edit_accept" | "reject" | "later", value?: string) {
    setBusyId(review.id);
    setError("");
    try {
      await mutate(`/reviews/${review.id}/decision`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ action, edited_value: value || null, reason: action === "later" ? "稍后处理" : "" }),
      });
      router.refresh();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "审核操作失败");
    } finally {
      setBusyId(null);
    }
  }

  async function upload(formData: FormData) {
    setUploading(true);
    setError("");
    const localTime = formData.get("captured_at");
    if (typeof localTime === "string" && localTime) formData.set("captured_at", new Date(localTime).toISOString());
    try {
      const response = await fetch(`${publicApiUrl}/inbox/upload`, { method: "POST", body: formData });
      const body = (await response.json().catch(() => ({}))) as { detail?: string };
      if (!response.ok) throw new Error(body.detail ?? "保存失败");
      router.refresh();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "保存失败");
    } finally {
      setUploading(false);
    }
  }

  return (
    <>
      <header className="page-header">
        <div><span className="eyebrow">REVIEW CENTER</span><h1>只审核真正不确定的结果</h1><p>自动化有证据、有置信度、可拒绝；你的修正会被锁定，不再被后台覆盖。</p></div>
        <span className="review-total"><strong>{reviews.length}</strong><small>待审核</small></span>
      </header>
      {error ? <div className="notice error" role="alert">{error}</div> : null}

      <section className="review-layout">
        <div className="review-list">
          {reviews.length ? reviews.map((review) => (
            <ReviewCard
              busy={busyId === review.id}
              key={review.id}
              review={review}
              sessions={sessions}
              onDecision={(action, value) => decide(review, action, value)}
            />
          )) : (
            <div className="panel review-empty"><span className="success-glyph">✓</span><h2>没有等待确认的结果</h2><p>高置信结果已经自动处理；低置信结果会保留在这里，而不是静默覆盖。</p></div>
          )}
        </div>

        <aside className="inbox-column" id="inbox">
          <form className="panel inbox-dropzone" action={upload}>
            <span className="eyebrow">GLOBAL INBOX</span>
            <h2>快速收件</h2>
            <p>无需先选择课程或 Session。文件可靠保存后，再按时间、课表和名称匹配。</p>
            <label className="file-drop"><input name="file" required type="file" /><span>选择录音、视频或资料</span><small>原始文件不会因识别失败丢失</small></label>
            <div className="form-pair">
              <label>类型<select name="resource_type" defaultValue="audio"><option value="audio">录音</option><option value="video">视频</option><option value="slides">课件</option><option value="note">笔记</option><option value="other">其他</option></select></label>
              <label>采集时间<input defaultValue={localDateTime()} name="captured_at" type="datetime-local" /></label>
            </div>
            <button className="button primary" disabled={uploading}>{uploading ? "正在可靠保存…" : "保存并自动匹配"}</button>
          </form>
          <section className="panel inbox-history">
            <div className="section-heading"><div><span className="eyebrow">RECENT INBOX</span><h2>最近收件</h2></div><span className="count-chip">{inbox.length}</span></div>
            {inbox.slice(0, 8).map((item) => (
              <article className="inbox-row" key={item.id}>
                <span className="file-icon">{item.type.slice(0, 3).toUpperCase()}</span>
                <span><strong>{item.name}</strong><small>{inboxState(item.matching_status)} · {Math.round(item.match_confidence * 100)}%</small></span>
                {item.adopted_resource_id ? <span className="badge accepted">已归档</span> : null}
              </article>
            ))}
            {!inbox.length ? <p className="muted">还没有全局收件记录。</p> : null}
          </section>
        </aside>
      </section>
    </>
  );
}

function ReviewCard({
  review,
  sessions,
  busy,
  onDecision,
}: {
  review: ReviewItem;
  sessions: SessionSummary[];
  busy: boolean;
  onDecision: (action: "accept" | "edit_accept" | "reject" | "later", value?: string) => void;
}) {
  const [value, setValue] = useState(review.proposed_value ?? "");
  const archive = review.kind === "archive_match";
  return (
    <article className="panel review-card">
      <header><span className="badge review-kind">{kindLabels[review.kind]}</span><span className="confidence">置信度 {Math.round(review.confidence * 100)}%</span></header>
      <h2>{review.title}</h2>
      {archive ? (
        <label className="review-field">建议归档到<select value={value} onChange={(event) => setValue(event.target.value)}><option value="">请选择 Session</option>{sessions.map((session) => <option key={session.id} value={session.id}>{session.title}</option>)}</select></label>
      ) : (
        <label className="review-field">候选结果<input value={value} onChange={(event) => setValue(event.target.value)} /></label>
      )}
      <div className="reason-list"><strong>判断依据</strong>{review.reasons.map((reason) => <span key={reason}>· {reason}</span>)}</div>
      <footer>
        {review.navigation_path ? <Link href={review.navigation_path}>查看来源</Link> : <span />}
        <div><button className="text-button" disabled={busy} onClick={() => onDecision("later")}>稍后</button><button className="button ghost danger-text" disabled={busy} onClick={() => onDecision("reject")}>拒绝</button><button className="button primary" disabled={busy || !value} onClick={() => onDecision(value === review.proposed_value ? "accept" : "edit_accept", value)}>{busy ? "处理中…" : value === review.proposed_value ? "接受" : "修改并接受"}</button></div>
      </footer>
    </article>
  );
}

function localDateTime() { const date = new Date(); date.setMinutes(date.getMinutes() - date.getTimezoneOffset()); return date.toISOString().slice(0, 16); }
function inboxState(state: string) { return ({ pending: "待匹配", review: "待审核", accepted: "已归档", rejected: "已拒绝" } as Record<string, string>)[state] ?? state; }

