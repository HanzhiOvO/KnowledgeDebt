"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";

import { mutate } from "@/lib/client-api";
import type { Course } from "@/types/domain";

const channels = [
  { key: "classroom", label: "课堂现场证据", hint: "录音、视频、现场笔记" },
  { key: "official_session", label: "本节官方资料", hint: "本节课件、作业" },
  { key: "course_context", label: "课程上下文", hint: "大纲、教材" },
  { key: "supplementary", label: "补充资料", hint: "链接、外部材料" },
] as const;

export function CourseProfileForm({ course }: { course: Course }) {
  const router = useRouter();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  async function submit(formData: FormData) {
    setBusy(true);
    setError("");
    const profile = Object.fromEntries(
      channels.map((channel) => [channel.key, Number(formData.get(channel.key))]),
    );
    try {
      await mutate(`/courses/${course.id}/profile`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ profile }),
      });
      router.refresh();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "权重保存失败");
    } finally {
      setBusy(false);
    }
  }

  return (
    <form className="profile-form panel" action={submit}>
      <div className="section-heading">
        <div><span className="eyebrow">EVIDENCE WEIGHTS</span><h2>证据通道权重</h2><p className="muted">四项合计必须为 100，决定课堂还原度如何计算。</p></div>
        <span className="count-chip">100</span>
      </div>
      <div className="profile-grid">
        {channels.map((channel) => (
          <label key={channel.key}>
            <span>{channel.label}<small>{channel.hint}</small></span>
            <input name={channel.key} type="number" min="0" max="100" step="1" defaultValue={course.profile[channel.key] ?? 0} />
          </label>
        ))}
      </div>
      {error ? <p className="form-error">{error}</p> : null}
      <button className="button primary" disabled={busy}>{busy ? "保存中…" : "保存权重"}</button>
    </form>
  );
}
