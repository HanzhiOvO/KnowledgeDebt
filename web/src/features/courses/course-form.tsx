"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";

import { mutate } from "@/lib/client-api";
import type { Course } from "@/types/domain";

export function CourseForm() {
  const router = useRouter();
  const [open, setOpen] = useState(false);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  async function submit(formData: FormData) {
    setBusy(true);
    setError("");
    try {
      const course = await mutate<Course>("/courses", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          name: formData.get("name"),
          semester: formData.get("semester"),
          description: formData.get("description"),
        }),
      });
      setOpen(false);
      router.push(`/courses/${course.id}`);
      router.refresh();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "创建失败");
    } finally {
      setBusy(false);
    }
  }

  if (!open) {
    return <button className="button primary" onClick={() => setOpen(true)}>+ 新建课程</button>;
  }

  return (
    <form className="inline-form panel" action={submit}>
      <div className="form-heading">
        <strong>新建课程</strong>
        <button className="icon-button" type="button" onClick={() => setOpen(false)} aria-label="关闭">×</button>
      </div>
      <label>
        课程名称
        <input name="name" required maxLength={120} placeholder="例如：高等数学" />
      </label>
      <label>
        学期
        <input name="semester" maxLength={80} placeholder="2026 Fall" />
      </label>
      <label className="form-wide">
        说明
        <input name="description" maxLength={500} placeholder="可选" />
      </label>
      {error ? <p className="form-error">{error}</p> : null}
      <button className="button primary" disabled={busy}>{busy ? "创建中…" : "创建"}</button>
    </form>
  );
}
