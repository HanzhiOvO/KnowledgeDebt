"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { publicApiUrl } from "@/lib/client-api";
import {
  getLocalRecording,
  listLocalChunks,
  listLocalRecordings,
  markLocalChunkUploaded,
  removeLocalRecording,
  saveLocalChunk,
  saveLocalRecording,
  sha256,
  type LocalRecording,
  type LocalRecordingChunk,
} from "@/lib/recording-store";
import type { RecordingSnapshot } from "@/types/domain";

type Phase = "idle" | "recording" | "saving" | "save_failed";

async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${publicApiUrl}${path}`, init);
  const body = (await response.json().catch(() => ({}))) as {
    detail?: string | { message?: string; missing_sequences?: number[] };
  };
  if (!response.ok) {
    const detail = typeof body.detail === "string" ? body.detail : body.detail?.message;
    throw new Error(detail ?? `请求失败 (${response.status})`);
  }
  return body as T;
}

function filenameNow() {
  return `browser-${new Date().toISOString().replaceAll(":", "-")}.webm`;
}

function remoteAsLocal(remote: RecordingSnapshot): LocalRecording {
  const startedAt = Date.parse(remote.created_at);
  const updatedAt = Date.parse(remote.updated_at);
  return {
    id: remote.id,
    sessionId: remote.session_id,
    mimeType: remote.mime_type,
    filename: remote.filename,
    startOffset: remote.start_offset,
    sessionDuration: remote.session_duration ?? 100 * 60,
    startedAt,
    updatedAt,
    durationSeconds: remote.duration_seconds ?? Math.max(1, (updatedAt - startedAt) / 1000),
    nextSequence: remote.next_sequence,
    nextStreamIndex: remote.next_stream_index,
    status: remote.status === "failed" ? "save_failed" : "interrupted",
  };
}

export function BrowserRecorder({ sessionId, onSaved }: { sessionId: string; onSaved: () => void }) {
  const recorder = useRef<MediaRecorder | null>(null);
  const activeRecording = useRef<LocalRecording | null>(null);
  const nextSequence = useRef(0);
  const currentStreamId = useRef("");
  const currentStreamIndex = useRef(0);
  const resumedAt = useRef(0);
  const elapsedBeforeResume = useRef(0);
  const timer = useRef<ReturnType<typeof setInterval> | null>(null);
  const uploadChain = useRef<Promise<void>>(Promise.resolve());
  const finalizeOnStop = useRef(false);
  const haltedByStorageFailure = useRef(false);
  const mounted = useRef(true);
  const [supported] = useState(
    () => typeof window !== "undefined" && "MediaRecorder" in window && Boolean(navigator.mediaDevices?.getUserMedia),
  );
  const [phase, setPhase] = useState<Phase>("idle");
  const [seconds, setSeconds] = useState(0);
  const [error, setError] = useState("");
  const [startMinute, setStartMinute] = useState(0);
  const [sessionMinutes, setSessionMinutes] = useState(100);
  const [recoveries, setRecoveries] = useState<LocalRecording[]>([]);

  const refreshRecoveries = useCallback(async () => {
    try {
      const local = await listLocalRecordings(sessionId);
      const remote = await api<RecordingSnapshot[]>(`/sessions/${sessionId}/recordings/incomplete`).catch(() => []);
      const combined = new Map(local.map((item) => [item.id, item]));
      for (const item of remote) {
        const existing = combined.get(item.id);
        combined.set(item.id, existing ? {
          ...existing,
          nextSequence: Math.max(existing.nextSequence, item.next_sequence),
          nextStreamIndex: Math.max(existing.nextStreamIndex ?? 0, item.next_stream_index),
        } : remoteAsLocal(item));
      }
      if (mounted.current) setRecoveries([...combined.values()].sort((a, b) => b.updatedAt - a.updatedAt));
    } catch (reason) {
      if (mounted.current) setError(reason instanceof Error ? reason.message : "无法读取未完成录音");
    }
  }, [sessionId]);

  useEffect(() => {
    mounted.current = true;
    const initialLoad = window.setTimeout(() => void refreshRecoveries(), 0);
    const beforeUnload = () => {
      finalizeOnStop.current = false;
      if (recorder.current?.state === "recording") recorder.current.requestData();
    };
    window.addEventListener("beforeunload", beforeUnload);
    return () => {
      mounted.current = false;
      window.clearTimeout(initialLoad);
      window.removeEventListener("beforeunload", beforeUnload);
      finalizeOnStop.current = false;
      if (timer.current) clearInterval(timer.current);
      if (recorder.current?.state === "recording") recorder.current.stop();
      else recorder.current?.stream.getTracks().forEach((track) => track.stop());
    };
  }, [refreshRecoveries]);

  async function ensureRemote(local: LocalRecording): Promise<RecordingSnapshot> {
    return api<RecordingSnapshot>(`/sessions/${sessionId}/recordings`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        recording_id: local.id,
        mime_type: local.mimeType,
        filename: local.filename,
        start_offset: local.startOffset,
        session_duration: local.sessionDuration,
        auto_transcribe: null,
      }),
    });
  }

  async function uploadChunk(local: LocalRecording, chunk: LocalRecordingChunk) {
    await ensureRemote(local);
    const form = new FormData();
    form.append("file", chunk.blob, `${chunk.sequence}.chunk`);
    await api(`/recordings/${local.id}/chunks/${chunk.sequence}`, {
      method: "PUT",
      headers: {
        "X-Chunk-SHA256": chunk.checksum,
        "X-Recording-Stream-ID": chunk.streamId,
        "X-Recording-Stream-Index": String(chunk.streamIndex),
      },
      body: form,
    });
    await markLocalChunkUploaded(local.id, chunk.sequence);
  }

  async function uploadPendingChunks(local: LocalRecording) {
    const chunks = await listLocalChunks(local.id);
    for (const chunk of chunks) {
      if (!chunk.uploaded) await uploadChunk(activeRecording.current ?? local, chunk);
    }
  }

  function stopAfterPersistenceFailure(message: string) {
    finalizeOnStop.current = false;
    haltedByStorageFailure.current = true;
    if (timer.current) clearInterval(timer.current);
    if (mounted.current) {
      setPhase("save_failed");
      setError(`${message}；已立即停止录音，之前成功保存的分片仍可恢复。`);
    }
    if (recorder.current?.state === "recording") recorder.current.stop();
  }

  function queueChunk(
    local: LocalRecording,
    blob: Blob,
    sequence: number,
    streamId: string,
    streamIndex: number,
  ) {
    let persistenceFailed = false;
    const persisted = (async () => {
      const checksum = await sha256(blob);
      const chunk: LocalRecordingChunk = {
        recordingId: local.id,
        sequence,
        blob,
        mimeType: blob.type || local.mimeType,
        streamId,
        streamIndex,
        checksum,
        uploaded: false,
        createdAt: Date.now(),
      };
      await saveLocalChunk(chunk);
      const latest = (await getLocalRecording(local.id)) ?? local;
      const updated: LocalRecording = {
        ...latest,
        nextSequence: Math.max(latest.nextSequence, sequence + 1),
        durationSeconds: elapsedBeforeResume.current + Math.max(0, (Date.now() - resumedAt.current) / 1000),
        updatedAt: Date.now(),
      };
      await saveLocalRecording(updated);
      activeRecording.current = updated;
      return chunk;
    })().catch((reason) => {
      persistenceFailed = true;
      stopAfterPersistenceFailure(
        reason instanceof Error ? reason.message : "浏览器持久存储写入失败",
      );
      throw reason;
    });
    uploadChain.current = uploadChain.current
      .catch(() => undefined)
      .then(async () => {
        await persisted;
        await uploadPendingChunks(activeRecording.current ?? local);
      })
      .catch((reason) => {
        if (mounted.current && !persistenceFailed) {
          setError(`${reason instanceof Error ? reason.message : "分片上传失败"}；录音已保存在本机，将自动重试。`);
        }
      });
  }

  async function start(existing?: LocalRecording) {
    if (phase !== "idle") return;
    setError("");
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      const preferred = existing?.mimeType && MediaRecorder.isTypeSupported(existing.mimeType)
        ? existing.mimeType
        : MediaRecorder.isTypeSupported("audio/webm;codecs=opus")
          ? "audio/webm;codecs=opus"
          : undefined;
      const next = new MediaRecorder(stream, preferred ? { mimeType: preferred } : undefined);
      const now = Date.now();
      const streamIndex = existing?.nextStreamIndex ?? 0;
      const streamId = crypto.randomUUID();
      const local: LocalRecording = existing ? {
        ...existing,
        status: "recording",
        nextStreamIndex: streamIndex + 1,
        updatedAt: now,
      } : {
        id: crypto.randomUUID(),
        sessionId,
        mimeType: next.mimeType || "audio/webm",
        filename: filenameNow(),
        startOffset: startMinute * 60,
        sessionDuration: sessionMinutes * 60,
        startedAt: now,
        updatedAt: now,
        durationSeconds: 0,
        nextSequence: 0,
        nextStreamIndex: 1,
        status: "recording",
      };
      await saveLocalRecording(local);
      const remote = await ensureRemote(local).catch((reason) => {
        if (mounted.current) setError(`${reason instanceof Error ? reason.message : "后端暂时离线"}；仍会先安全保存到本机。`);
        return null;
      });
      if (remote?.status === "completed") {
        next.stream.getTracks().forEach((track) => track.stop());
        await removeLocalRecording(local.id);
        await refreshRecoveries();
        onSaved();
        return;
      }
      activeRecording.current = local;
      nextSequence.current = local.nextSequence;
      currentStreamId.current = streamId;
      currentStreamIndex.current = streamIndex;
      elapsedBeforeResume.current = local.durationSeconds;
      resumedAt.current = now;
      uploadChain.current = uploadPendingChunks(local).catch((reason) => {
        if (mounted.current) {
          setError(`${reason instanceof Error ? reason.message : "分片补传失败"}；将继续保存在本机并稍后重试。`);
        }
      });
      finalizeOnStop.current = false;
      haltedByStorageFailure.current = false;
      next.ondataavailable = (event) => {
        if (!event.data.size) return;
        const sequence = nextSequence.current++;
        queueChunk(
          activeRecording.current ?? local,
          event.data,
          sequence,
          currentStreamId.current,
          currentStreamIndex.current,
        );
      };
      next.onstop = () => {
        next.stream.getTracks().forEach((track) => track.stop());
        void (async () => {
          await uploadChain.current;
          const latest = (await getLocalRecording(local.id)) ?? activeRecording.current ?? local;
          if (finalizeOnStop.current) await finalize(latest);
          else await saveLocalRecording({
            ...latest,
            status: haltedByStorageFailure.current ? "save_failed" : "interrupted",
            updatedAt: Date.now(),
          });
          if (!finalizeOnStop.current && mounted.current) {
            setPhase("idle");
            await refreshRecoveries();
          }
        })();
      };
      recorder.current = next;
      setSeconds(Math.floor(local.durationSeconds));
      next.start(5000);
      timer.current = setInterval(() => {
        setSeconds(Math.floor(elapsedBeforeResume.current + (Date.now() - resumedAt.current) / 1000));
      }, 1000);
      setPhase("recording");
      setRecoveries((items) => items.filter((item) => item.id !== local.id));
    } catch (reason) {
      recorder.current?.stream.getTracks().forEach((track) => track.stop());
      setError(reason instanceof Error ? reason.message : "无法访问麦克风，请检查浏览器权限。");
    }
  }

  function stop() {
    if (phase !== "recording" || recorder.current?.state !== "recording") return;
    if (timer.current) clearInterval(timer.current);
    finalizeOnStop.current = true;
    setPhase("saving");
    recorder.current.stop();
  }

  async function finalize(local: LocalRecording) {
    if (mounted.current) {
      setPhase("saving");
      setError("");
    }
    try {
      const remote = await ensureRemote(local);
      if (remote.status === "completed") {
        await removeLocalRecording(local.id);
        activeRecording.current = null;
        if (mounted.current) {
          setPhase("idle");
          setSeconds(0);
          await refreshRecoveries();
          onSaved();
        }
        return;
      }
      const chunks = await listLocalChunks(local.id);
      for (const chunk of chunks) await uploadChunk(local, chunk);
      const lastSequence = Math.max(remote.next_sequence - 1, local.nextSequence - 1, ...chunks.map((item) => item.sequence));
      if (lastSequence < 0) throw new Error("还没有可保存的录音分片");
      await api(`/recordings/${local.id}/finalize`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          last_sequence: lastSequence,
          duration_seconds: Math.max(1, local.durationSeconds),
        }),
      });
      await removeLocalRecording(local.id);
      activeRecording.current = null;
      if (mounted.current) {
        setPhase("idle");
        setSeconds(0);
        await refreshRecoveries();
        onSaved();
      }
    } catch (reason) {
      await saveLocalRecording({ ...local, status: "save_failed", updatedAt: Date.now() });
      if (mounted.current) {
        setPhase("idle");
        setError(`${reason instanceof Error ? reason.message : "保存失败"}；原始分片仍保留在本机和已上传后端。`);
        await refreshRecoveries();
      }
    }
  }

  async function abandon(local: LocalRecording) {
    if (!window.confirm("确定放弃这段未完成录音吗？后端原始分片仍会暂时保留用于安全审计。")) return;
    setError("");
    try {
      await api(`/recordings/${local.id}/abandon`, { method: "POST" });
      await removeLocalRecording(local.id);
      await refreshRecoveries();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "无法放弃录音");
    }
  }

  return (
    <section className="recorder-card">
      <span className="eyebrow">DURABLE BROWSER RECORDER</span>
      <h3>{phase === "recording" ? `正在录音 ${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, "0")}` : phase === "saving" ? "正在核对并保存所有分片…" : phase === "save_failed" ? "保存未完成，可以继续恢复" : "浏览器现场录音"}</h3>
      <p>每 5 秒先写入浏览器持久存储，再增量上传本地后端；刷新或断网不会丢掉整节课。</p>
      {phase === "idle" ? <div className="form-pair recorder-fields"><label>当前课堂分钟<input type="number" min="0" value={startMinute} onChange={(event) => setStartMinute(Number(event.target.value))} /></label><label>课堂总分钟<input type="number" min="1" value={sessionMinutes} onChange={(event) => setSessionMinutes(Number(event.target.value))} /></label></div> : null}
      {error ? <p className="form-error" role="alert">{error}</p> : null}
      <button className={phase === "recording" ? "button danger" : "button secondary inverted"} disabled={!supported || phase === "saving" || phase === "save_failed" || (phase === "idle" && sessionMinutes <= startMinute)} onClick={phase === "recording" ? stop : () => void start()} type="button">
        {phase === "recording" ? "停止并安全保存" : phase === "saving" ? "保存中…" : supported ? "开始录音" : "当前浏览器不支持"}
      </button>
      {recoveries.length ? <div className="recording-recovery"><strong>发现 {recoveries.length} 段未完成录音</strong>{recoveries.map((item) => <article key={item.id}><span><b>{item.filename}</b><small>{Math.max(1, Math.round(item.durationSeconds))} 秒 · 已记录 {item.nextSequence} 个分片</small></span><div><button className="text-button" disabled={phase !== "idle"} onClick={() => void start(item)} type="button">继续恢复</button><button className="text-button" disabled={phase !== "idle"} onClick={() => void finalize(item)} type="button">保存已有内容</button><button className="text-button danger-text" disabled={phase !== "idle"} onClick={() => void abandon(item)} type="button">放弃录音</button></div></article>)}</div> : null}
    </section>
  );
}
