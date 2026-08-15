"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";

import { mutate, publicApiUrl } from "@/lib/client-api";
import type { ConsentManifest, Job } from "@/types/domain";

export function JobAction({
  sessionId,
  kind,
  label,
}: {
  sessionId: string;
  kind: "analysis" | "assessment";
  label: string;
}) {
  const router = useRouter();
  const [manifest, setManifest] = useState<ConsentManifest | null>(null);
  const [confirmed, setConfirmed] = useState(false);
  const [job, setJob] = useState<Job | null>(null);
  const [error, setError] = useState("");

  async function prepare() {
    setError("");
    try {
      const response = await fetch(`${publicApiUrl}/sessions/${sessionId}/consent-manifest?operation=${kind}`);
      if (!response.ok) throw new Error("无法读取隐私清单");
      const next = (await response.json()) as ConsentManifest;
      if (next.confirmation_required) setManifest(next);
      else await start(false);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "操作失败");
    }
  }

  async function start(consent: boolean) {
    setManifest(null);
    setError("");
    try {
      const created = await mutate<Job>(`/sessions/${sessionId}/jobs`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ kind, confirm_external_upload: consent }),
      });
      setJob(created);
      await poll(created.id);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "任务启动失败");
    }
  }

  async function poll(jobId: string) {
    for (let attempt = 0; attempt < 180; attempt += 1) {
      const response = await fetch(`${publicApiUrl}/jobs/${jobId}`);
      if (!response.ok) throw new Error("无法读取任务状态");
      const current = (await response.json()) as Job;
      setJob(current);
      if (["succeeded", "failed", "cancelled"].includes(current.status)) {
        if (current.status === "succeeded") router.refresh();
        return;
      }
      await new Promise((resolve) => setTimeout(resolve, 1000));
    }
    throw new Error("任务仍在运行，可稍后刷新查看");
  }

  const running = job && ["queued", "running"].includes(job.status);
  return (
    <div className="job-action">
      <button className="button primary" disabled={Boolean(running)} onClick={prepare}>
        {running ? `${job.stage} · ${job.progress}%` : label}
      </button>
      {job ? <div className="job-progress" aria-label={`任务进度 ${job.progress}%`}><span style={{ width: `${job.progress}%` }} /></div> : null}
      {job?.status === "failed" ? <p className="form-error">{job.error || "任务失败"}</p> : null}
      {error ? <p className="form-error">{error}</p> : null}
      {manifest ? (
        <div className="modal-backdrop" role="presentation">
          <section className="consent-modal panel" role="dialog" aria-modal="true" aria-labelledby="consent-title">
            <span className="eyebrow">EXPLICIT DATA CONSENT</span>
            <h2 id="consent-title">确认发送给 {manifest.provider}</h2>
            <div className="consent-columns">
              <div><strong>将发送</strong><ul>{manifest.will_send.map((item) => <li key={item}>{item}</li>)}</ul></div>
              <div><strong>不会发送</strong><ul>{manifest.will_not_send.map((item) => <li key={item}>{item}</li>)}</ul></div>
            </div>
            <div className="consent-resources"><strong>涉及的具体资源</strong>{manifest.resources.map((resource) => <span key={resource.id}>{resource.name} · {resource.type}</span>)}</div>
            <label className="consent-check"><input type="checkbox" checked={confirmed} onChange={(event) => setConfirmed(event.target.checked)} />我理解这些派生内容将离开自托管服务器，并同意本次操作。</label>
            <div className="modal-actions"><button className="button secondary" onClick={() => setManifest(null)}>取消</button><button className="button primary" disabled={!confirmed} onClick={() => start(true)}>仅同意这一次</button></div>
          </section>
        </div>
      ) : null}
    </div>
  );
}
