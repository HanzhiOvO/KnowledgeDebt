"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";

import { mutate } from "@/lib/client-api";
import type { SessionDetail } from "@/types/domain";

export function SessionForm({ courseId }: { courseId: string }) {
  const router = useRouter();
  const [open, setOpen] = useState(false);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  async function submit(formData: FormData) {
    setBusy(true);
    setError("");
    try {
      const session = await mutate<SessionDetail>(`/courses/${courseId}/sessions`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ title: formData.get("title"), notes: formData.get("notes") }),
      });
      router.push(`/sessions/${session.id}`);
      router.refresh();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "创建失败");
      setBusy(false);
    }
  }

  return (
    <>
      <button className="button primary" onClick={() => setOpen((value) => !value)}>
        {open ? "取消" : "+ 新建 Session"}
      </button>
      {open ? (
        <form className="inline-form panel span-all" action={submit}>
          <label>
            课堂标题
            <input name="title" required maxLength={200} placeholder="Lecture 12 · 中值定理" />
          </label>
          <label className="form-wide">
            现场备注
            <input name="notes" maxLength={2000} placeholder="缺席、迟到、老师临时调整等" />
          </label>
          {error ? <p className="form-error">{error}</p> : null}
          <button className="button primary" disabled={busy}>{busy ? "创建中…" : "进入课堂"}</button>
        </form>
      ) : null}
    </>
  );
}
