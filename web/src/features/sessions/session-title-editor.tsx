"use client";

import { useRouter } from "next/navigation";
import { type FormEvent, useState } from "react";

import { mutate } from "@/lib/client-api";

const sourceLabels: Record<string, string> = {
  course_name: "系统临时标题",
  transcript_rule: "转写自动生成",
  ai: "AI 自动生成",
  user: "用户手动命名",
  user_review: "用户审核命名",
};

export function SessionTitleEditor({
  sessionId,
  initialTitle,
  source,
  locked,
}: {
  sessionId: string;
  initialTitle: string;
  source?: string;
  locked?: boolean;
}) {
  const router = useRouter();
  const [editing, setEditing] = useState(false);
  const [title, setTitle] = useState(initialTitle);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  async function save(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const normalized = title.trim();
    if (!normalized) {
      setError("标题不能为空。");
      return;
    }
    setBusy(true);
    setError("");
    try {
      await mutate(`/sessions/${sessionId}/title`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ title: normalized, locked: true }),
      });
      setTitle(normalized);
      setEditing(false);
      router.refresh();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "标题保存失败，请稍后重试。");
    } finally {
      setBusy(false);
    }
  }

  if (editing) {
    return (
      <form className="session-title-form" onSubmit={save}>
        <label htmlFor="session-title-input">Session 标题</label>
        <div>
          <input
            autoFocus
            id="session-title-input"
            maxLength={160}
            onChange={(event) => setTitle(event.target.value)}
            value={title}
          />
          <button className="button primary" disabled={busy} type="submit">
            {busy ? "保存中…" : "保存并锁定"}
          </button>
          <button
            className="button ghost"
            disabled={busy}
            onClick={() => {
              setTitle(initialTitle);
              setError("");
              setEditing(false);
            }}
            type="button"
          >
            取消
          </button>
        </div>
        <small>保存后标记为用户手动命名，后续自动转写不会覆盖。</small>
        {error ? <span className="form-error" role="alert">{error}</span> : null}
      </form>
    );
  }

  return (
    <div className="session-title-tools">
      <span className={`badge ${locked ? "accepted" : ""}`}>
        {sourceLabels[source ?? ""] ?? "标题来源未记录"}{locked ? " · 已锁定" : ""}
      </span>
      <button className="text-button" onClick={() => setEditing(true)} type="button">
        修改标题
      </button>
    </div>
  );
}
