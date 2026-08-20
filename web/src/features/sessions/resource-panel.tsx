"use client";

import { useRouter } from "next/navigation";
import { useEffect, useRef, useState } from "react";

import { mutate, publicApiUrl } from "@/lib/client-api";
import type { ConsentManifest, Job, Resource, ResourceAutomation } from "@/types/domain";

const transcriptionLabels: Record<string, string> = {
  saved: "已保存",
  preparing: "准备中",
  awaiting_consent: "等待授权",
  queued: "已排队",
  transcribing: "转写中",
  partial: "部分完成",
  transcribed: "已转写",
  failed: "转写失败",
  cancelled: "未转写",
};

const terminalTranscriptionStates = ["transcribed", "partial", "failed", "cancelled"];

type TranscriptionSnapshot = {
  automation: ResourceAutomation;
  active_job?: Job | null;
};

async function loadTranscription(resourceId: string): Promise<TranscriptionSnapshot> {
  const response = await fetch(`${publicApiUrl}/resources/${resourceId}/transcription`, {
    cache: "no-store",
  });
  const body = (await response.json().catch(() => ({}))) as TranscriptionSnapshot & { detail?: string };
  if (!response.ok) throw new Error(body.detail ?? "无法读取转写状态");
  return body;
}

export function ResourcePanel({ sessionId, resources, mode = "resources" }: { sessionId: string; resources: Resource[]; mode?: "media" | "resources" }) {
  const router = useRouter();
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const visible = resources.filter((resource) => mode === "media" ? ["audio", "video"].includes(resource.type) : !["audio", "video"].includes(resource.type));

  async function upload(formData: FormData) {
    for (const key of ["start_offset", "end_offset", "session_duration"]) {
      if (!formData.get(key)) formData.delete(key);
    }
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
        <div className="resource-list-heading"><span><strong>{mode === "media" ? "录音与视频" : "课堂资料"}</strong><small>{visible.length} 个资源</small></span><span className="local-pill small"><i />原文件已保留</span></div>
        {visible.length ? visible.map((resource) => (
          <article className="resource-row detailed" key={resource.id}>
            <span className="file-icon">{resource.type.slice(0, 3).toUpperCase()}</span>
            <span className="resource-main"><strong>{resource.name}</strong><small>{resource.evidence_level} · {resource.duration_seconds ? `${Math.round(resource.duration_seconds / 60)} 分钟` : resource.upload_state}</small>{resource.automation?.failure_reason ? <span className="resource-error">{resource.automation.failure_reason}</span> : null}</span>
            {mode === "media" ? <TranscriptionControl key={`${resource.id}:${resource.automation?.transcription_state}:${resource.automation?.last_job_id ?? "none"}`} sessionId={sessionId} resource={resource} /> : <span className="quality-chip">{resource.chunks?.length ? `${resource.chunks.length} 个内容块` : `${Math.round(resource.coverage * resource.quality * resource.relevance * 100)}% 有效`}</span>}
          </article>
        )) : <p className="muted">{mode === "media" ? "还没有录音或视频。" : "尚无资料。Session 仍然有效，你可以稍后补充。"}</p>}
        {mode === "media" ? <TranscriptPreview resources={visible} /> : null}
      </div>
      <form className="upload-card" action={upload}>
        <span className="eyebrow">{mode === "media" ? "ADD RECORDING" : "ADD EVIDENCE"}</span>
        <h3>{mode === "media" ? "上传录音或视频" : "上传课堂资料"}</h3>
        <input accept={mode === "media" ? "audio/*,video/*" : undefined} name="file" type="file" required />
        <div className="form-pair">
          <label>类型<select name="resource_type" defaultValue={mode === "media" ? "audio" : "slides"}>{mode === "media" ? <><option value="audio">录音</option><option value="video">视频</option></> : <><option value="slides">课件 / PPT</option><option value="textbook">教材</option><option value="note">笔记</option><option value="syllabus">大纲</option><option value="assignment">作业</option></>}</select></label>
          <label>证据级别<select name="evidence_level" defaultValue={mode === "media" ? "classroom" : "official"}><option value="official">课程官方</option><option value="classroom">课堂现场</option><option value="supplementary">补充资料</option></select></label>
        </div>
        {mode === "media" ? <><div className="form-pair recording-range">
          <label>录音起点（秒）<input name="start_offset" inputMode="decimal" min="0" type="number" placeholder="0" /></label>
          <label>录音终点（秒）<input name="end_offset" inputMode="decimal" min="0" type="number" placeholder="3600" /></label>
        </div>
        <label className="recording-range">课堂总时长（秒）<input name="session_duration" inputMode="decimal" min="1" type="number" placeholder="6000" /></label>
        <label className="checkbox-row"><input defaultChecked name="auto_transcribe" type="checkbox" value="true" />保存后自动转写</label></> : null}
        <input type="hidden" name="coverage" value="1" />
        <input type="hidden" name="quality" value="1" />
        <input type="hidden" name="relevance" value="1" />
        {error ? <p className="form-error">{error}</p> : null}
        <button className="button primary" disabled={busy}>{busy ? "上传中…" : "上传到本地存储"}</button>
      </form>
      {mode === "media" ? <BrowserRecorder sessionId={sessionId} onSaved={() => router.refresh()} /> : null}
    </div>
  );
}

function TranscriptionControl({ sessionId, resource }: { sessionId: string; resource: Resource }) {
  const router = useRouter();
  const [state, setState] = useState<ResourceAutomation | undefined>(resource.automation);
  const [job, setJob] = useState<Job | null>(resource.active_transcription_job ?? null);
  const [manifest, setManifest] = useState<ConsentManifest | null>(null);
  const [confirmed, setConfirmed] = useState(false);
  const [cancelling, setCancelling] = useState(false);
  const [error, setError] = useState("");
  const status = state?.transcription_state ?? "saved";
  const running = ["queued", "preparing", "transcribing"].includes(status) || Boolean(job && ["queued", "running"].includes(job.status));

  useEffect(() => {
    if (!running) return;
    let disposed = false;
    let timer: ReturnType<typeof setTimeout> | undefined;

    async function poll() {
      try {
        const current = await loadTranscription(resource.id);
        if (disposed) return;
        setState(current.automation);
        setJob(current.active_job ?? null);
        if (terminalTranscriptionStates.includes(current.automation.transcription_state)) {
          router.refresh();
          return;
        }
        timer = setTimeout(() => void poll(), 1200);
      } catch (reason) {
        if (!disposed) setError(reason instanceof Error ? reason.message : "无法读取转写状态");
      }
    }

    void poll();
    return () => {
      disposed = true;
      if (timer) clearTimeout(timer);
    };
  }, [resource.id, router, running]);

  async function prepare() {
    setError("");
    try {
      const response = await fetch(`${publicApiUrl}/sessions/${sessionId}/consent-manifest?operation=transcription&resource_id=${resource.id}`);
      if (!response.ok) throw new Error("无法读取本次转写的数据清单");
      const next = (await response.json()) as ConsentManifest;
      if (next.confirmation_required) setManifest(next);
      else await start(false);
    } catch (reason) { setError(reason instanceof Error ? reason.message : "无法启动转写"); }
  }

  async function start(consent: boolean) {
    setManifest(null);
    setConfirmed(false);
    setError("");
    try {
      const created = await mutate<Job>(`/resources/${resource.id}/transcription-jobs`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ confirm_external_upload: consent }),
      });
      setJob(created);
      setState((current) => current ? { ...current, transcription_state: "queued", last_job_id: created.id } : current);
    } catch (reason) { setError(reason instanceof Error ? reason.message : "转写任务失败"); }
  }

  async function cancel() {
    if (!job || cancelling) return;
    setCancelling(true);
    setError("");
    try {
      const latest = await loadTranscription(resource.id);
      setState(latest.automation);
      setJob(latest.active_job ?? null);
      if (terminalTranscriptionStates.includes(latest.automation.transcription_state) || !latest.active_job) {
        router.refresh();
        return;
      }
      const result = await mutate<Job>(`/jobs/${latest.active_job.id}/cancel`, { method: "POST" });
      if (result.status === "cancelled") {
        setJob(null);
        setState((current) => current ? { ...current, transcription_state: "cancelled" } : current);
      } else {
        const current = await loadTranscription(resource.id);
        setState(current.automation);
        setJob(current.active_job ?? null);
      }
      router.refresh();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "无法取消转写任务");
    } finally {
      setCancelling(false);
    }
  }

  return <div className="transcription-control">
    <span className={`badge transcription-state state-${status}`}><i />{transcriptionLabels[status] ?? status}</span>
    {running ? <div className="mini-progress wide"><i style={{ width: `${job?.progress ?? 12}%` }} /></div> : null}
    {status !== "transcribed" && !running ? <button className="text-button" type="button" onClick={prepare}>{["failed", "partial", "cancelled"].includes(status) ? "重试" : "开始转写"}</button> : null}
    {running ? <button className="text-button danger-text" type="button" disabled={cancelling} onClick={cancel}>{cancelling ? "取消中…" : "取消"}</button> : null}
    {error ? <small className="resource-error">{error}</small> : null}
    {manifest ? <div className="modal-backdrop" role="presentation"><section className="consent-modal panel" role="dialog" aria-modal="true" aria-labelledby="transcription-consent-title"><span className="eyebrow">ONE-TIME EXTERNAL CONSENT</span><h2 id="transcription-consent-title">确认本次外部转写</h2>{manifest.providers?.map((provider) => <div className="provider-route" key={provider.name}><span>实际路由</span><strong>{provider.name}</strong><small>{provider.vendor} · {provider.model || "未设置模型"}</small></div>)}<div className="consent-columns"><div><strong>将发送</strong><ul>{manifest.will_send.map((item) => <li key={item}>{item}</li>)}</ul></div><div><strong>不会发送</strong><ul>{manifest.will_not_send.map((item) => <li key={item}>{item}</li>)}</ul></div></div><div className="consent-resources"><strong>具体资源</strong>{manifest.resources.map((item) => <span key={item.id}>{item.name} · {item.type}</span>)}</div><label className="consent-check"><input checked={confirmed} onChange={(event) => setConfirmed(event.target.checked)} type="checkbox" />我理解该媒体会发送给上方实际 Provider，并且只同意这一次。</label><div className="modal-actions"><button className="button secondary" type="button" onClick={() => setManifest(null)}>取消并保留为未转写</button><button className="button primary" type="button" disabled={!confirmed} onClick={() => start(true)}>仅同意本次</button></div></section></div> : null}
  </div>;
}

function TranscriptPreview({ resources }: { resources: Resource[] }) {
  const segments = resources.flatMap((resource) => resource.transcript_segments ?? []);
  if (!segments.length) return null;
  return <div className="transcript-preview"><div className="section-heading"><div><span className="eyebrow">TRANSCRIPT</span><h3>转写片段</h3></div><span className="count-chip">{segments.length}</span></div>{segments.slice(0, 12).map((segment) => <div className="transcript-line" key={segment.id}><time>{Math.floor(segment.global_start / 60)}:{String(Math.floor(segment.global_start % 60)).padStart(2, "0")}</time><p>{segment.text}</p></div>)}</div>;
}

function BrowserRecorder({ sessionId, onSaved }: { sessionId: string; onSaved: () => void }) {
  const recorder = useRef<MediaRecorder | null>(null);
  const chunks = useRef<Blob[]>([]);
  const startedAt = useRef(0);
  const timer = useRef<ReturnType<typeof setInterval> | null>(null);
  const [supported] = useState(
    () => typeof window !== "undefined" && "MediaRecorder" in window && Boolean(navigator.mediaDevices?.getUserMedia),
  );
  const [phase, setPhase] = useState<"idle" | "recording" | "saving" | "save_failed">("idle");
  const [seconds, setSeconds] = useState(0);
  const [error, setError] = useState("");
  const [startMinute, setStartMinute] = useState(0);
  const [sessionMinutes, setSessionMinutes] = useState(100);
  const pending = useRef<{ blob: Blob; duration: number; filename: string } | null>(null);

  useEffect(() => {
    return () => {
      if (timer.current) clearInterval(timer.current);
      recorder.current?.stream.getTracks().forEach((track) => track.stop());
    };
  }, []);

  async function savePending() {
    if (!pending.current) return;
    setPhase("saving");
    setError("");
    const { blob, duration, filename } = pending.current;
    const form = new FormData();
    form.append("file", blob, filename);
    form.append("resource_type", "audio");
    form.append("evidence_level", "classroom");
    form.append("duration_seconds", String(duration));
    form.append("start_offset", String(startMinute * 60));
    form.append("end_offset", String(startMinute * 60 + duration));
    form.append("session_duration", String(sessionMinutes * 60));
    form.append("coverage", "1");
    form.append("quality", "0.9");
    form.append("relevance", "1");
    form.append("auto_transcribe", "true");
    try {
      const response = await fetch(`${publicApiUrl}/sessions/${sessionId}/resources/upload`, { method: "POST", body: form });
      const body = (await response.json().catch(() => ({}))) as { detail?: string };
      if (!response.ok) throw new Error(body.detail ?? "录音保存失败");
      pending.current = null;
      setPhase("idle");
      onSaved();
    } catch (reason) {
      setPhase("save_failed");
      setError(reason instanceof Error ? reason.message : "保存失败；录音仍在内存中，可以重试");
    }
  }

  async function start() {
    if (phase !== "idle") return;
    setError("");
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      const next = new MediaRecorder(stream);
      chunks.current = [];
      next.ondataavailable = (event) => { if (event.data.size) chunks.current.push(event.data); };
      next.onstop = () => {
        const duration = Math.max(1, (Date.now() - startedAt.current) / 1000);
        const blob = new Blob(chunks.current, { type: next.mimeType || "audio/webm" });
        pending.current = { blob, duration, filename: `browser-${new Date().toISOString().replaceAll(":", "-")}.webm` };
        next.stream.getTracks().forEach((track) => track.stop());
        void savePending();
      };
      recorder.current = next;
      startedAt.current = Date.now();
      setSeconds(0);
      next.start(5000);
      timer.current = setInterval(() => setSeconds(Math.floor((Date.now() - startedAt.current) / 1000)), 1000);
      setPhase("recording");
    } catch {
      recorder.current?.stream.getTracks().forEach((track) => track.stop());
      setError("无法访问麦克风，请检查浏览器权限。桌面端更推荐先用系统录音后上传。 ");
    }
  }

  function stop() {
    if (phase !== "recording") return;
    if (timer.current) clearInterval(timer.current);
    setPhase("saving");
    recorder.current?.stop();
  }

  return (
    <section className="recorder-card">
      <span className="eyebrow">BROWSER RECORDER</span>
      <h3>{phase === "recording" ? `正在录音 ${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, "0")}` : phase === "saving" ? "正在可靠保存…" : phase === "save_failed" ? "保存失败，录音仍在内存" : "浏览器现场录音"}</h3>
      <p>停止后先保存原始媒体，再由后台继续转写。保存完成后可以安全离开页面。</p>
      {phase === "idle" ? <div className="form-pair recorder-fields"><label>当前课堂分钟<input type="number" min="0" value={startMinute} onChange={(event) => setStartMinute(Number(event.target.value))} /></label><label>课堂总分钟<input type="number" min="1" value={sessionMinutes} onChange={(event) => setSessionMinutes(Number(event.target.value))} /></label></div> : null}
      {error ? <p className="form-error" role="alert">{error}</p> : null}
      <button className={phase === "recording" ? "button danger" : "button secondary inverted"} disabled={!supported || phase === "saving" || (phase === "idle" && sessionMinutes <= startMinute)} onClick={phase === "recording" ? stop : phase === "save_failed" ? () => void savePending() : start} type="button">
        {phase === "recording" ? "停止并保存" : phase === "saving" ? "保存中…" : phase === "save_failed" ? "重新保存原录音" : supported ? "开始录音" : "当前浏览器不支持"}
      </button>
    </section>
  );
}
