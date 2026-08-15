"use client";

import { useRouter } from "next/navigation";
import { useEffect, useRef, useState } from "react";

import { publicApiUrl } from "@/lib/client-api";
import type { Resource } from "@/types/domain";

export function ResourcePanel({ sessionId, resources }: { sessionId: string; resources: Resource[] }) {
  const router = useRouter();
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  async function upload(formData: FormData) {
    setBusy(true);
    setError("");
    try {
      const response = await fetch(`${publicApiUrl}/sessions/${sessionId}/resources/upload`, {
        method: "POST",
        body: formData,
      });
      const body = (await response.json().catch(() => ({}))) as { detail?: string };
      if (!response.ok) throw new Error(body.detail ?? "上传失败");
      router.refresh();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "上传失败");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="resource-layout">
      <div className="resource-list">
        {resources.length ? resources.map((resource) => (
          <article className="resource-row" key={resource.id}>
            <span className="file-icon">{resource.type.slice(0, 3).toUpperCase()}</span>
            <span className="resource-main"><strong>{resource.name}</strong><small>{resource.evidence_level} · {resource.upload_state}</small></span>
            <span className="quality-chip">{Math.round(resource.coverage * resource.quality * resource.relevance * 100)}% 有效</span>
          </article>
        )) : <p className="muted">尚无资料。Session 仍然有效，你可以从备注或后续补充开始。</p>}
      </div>
      <form className="upload-card" action={upload}>
        <span className="eyebrow">ADD EVIDENCE</span>
        <h3>上传课堂资料</h3>
        <input name="file" type="file" required />
        <div className="form-pair">
          <label>类型<select name="resource_type" defaultValue="slides"><option value="slides">课件 / PPT</option><option value="audio">录音</option><option value="video">视频</option><option value="textbook">教材</option><option value="note">笔记</option><option value="syllabus">大纲</option><option value="assignment">作业</option></select></label>
          <label>证据级别<select name="evidence_level" defaultValue="official"><option value="official">课程官方</option><option value="classroom">课堂现场</option><option value="supplementary">补充资料</option></select></label>
        </div>
        <input type="hidden" name="coverage" value="1" />
        <input type="hidden" name="quality" value="1" />
        <input type="hidden" name="relevance" value="1" />
        {error ? <p className="form-error">{error}</p> : null}
        <button className="button primary" disabled={busy}>{busy ? "上传中…" : "上传到本地存储"}</button>
      </form>
      <BrowserRecorder sessionId={sessionId} onSaved={() => router.refresh()} />
    </div>
  );
}

function BrowserRecorder({ sessionId, onSaved }: { sessionId: string; onSaved: () => void }) {
  const recorder = useRef<MediaRecorder | null>(null);
  const chunks = useRef<Blob[]>([]);
  const startedAt = useRef(0);
  const timer = useRef<ReturnType<typeof setInterval> | null>(null);
  const [supported] = useState(
    () => typeof window !== "undefined" && "MediaRecorder" in window && Boolean(navigator.mediaDevices?.getUserMedia),
  );
  const [recording, setRecording] = useState(false);
  const [seconds, setSeconds] = useState(0);
  const [error, setError] = useState("");

  useEffect(() => {
    return () => {
      if (timer.current) clearInterval(timer.current);
      recorder.current?.stream.getTracks().forEach((track) => track.stop());
    };
  }, []);

  async function start() {
    setError("");
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      const next = new MediaRecorder(stream);
      chunks.current = [];
      next.ondataavailable = (event) => { if (event.data.size) chunks.current.push(event.data); };
      next.onstop = async () => {
        const duration = Math.max(1, (Date.now() - startedAt.current) / 1000);
        const blob = new Blob(chunks.current, { type: next.mimeType || "audio/webm" });
        const form = new FormData();
        form.append("file", blob, `browser-${new Date().toISOString().replaceAll(":", "-")}.webm`);
        form.append("resource_type", "audio");
        form.append("evidence_level", "classroom");
        form.append("duration_seconds", String(duration));
        form.append("coverage", "1");
        form.append("quality", "0.9");
        form.append("relevance", "1");
        try {
          const response = await fetch(`${publicApiUrl}/sessions/${sessionId}/resources/upload`, { method: "POST", body: form });
          if (!response.ok) throw new Error("录音保存失败");
          onSaved();
        } catch (reason) {
          setError(reason instanceof Error ? reason.message : "录音保存失败");
        } finally {
          next.stream.getTracks().forEach((track) => track.stop());
        }
      };
      recorder.current = next;
      startedAt.current = Date.now();
      setSeconds(0);
      next.start(5000);
      timer.current = setInterval(() => setSeconds(Math.floor((Date.now() - startedAt.current) / 1000)), 1000);
      setRecording(true);
    } catch {
      setError("无法访问麦克风，请检查浏览器权限。桌面端更推荐先用系统录音后上传。 ");
    }
  }

  function stop() {
    if (timer.current) clearInterval(timer.current);
    recorder.current?.stop();
    setRecording(false);
  }

  return (
    <section className="recorder-card">
      <span className="eyebrow">BROWSER RECORDER · EXPERIMENTAL</span>
      <h3>{recording ? `正在录音 ${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, "0")}` : "浏览器现场录音"}</h3>
      <p>切换应用、锁屏或系统回收浏览器标签页都可能中断录音。重要课堂请优先使用系统录音并在课后上传。</p>
      {error ? <p className="form-error">{error}</p> : null}
      <button className={recording ? "button danger" : "button secondary"} disabled={!supported} onClick={recording ? stop : start} type="button">
        {recording ? "停止并保存" : supported ? "开始录音" : "当前浏览器不支持"}
      </button>
    </section>
  );
}
