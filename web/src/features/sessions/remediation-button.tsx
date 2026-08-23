"use client";

import { useState } from "react";

import { mutate, publicApiUrl } from "@/lib/client-api";
import type { ConsentManifest, SourceRef } from "@/types/domain";

interface RemediationPayload {
  knowledge_point_title: string;
  diagnosis: string;
  simpler_explanation: string;
  analogy: string;
  worked_example: string;
  quick_check: string;
  sources: SourceRef[];
}

interface RemediationResult {
  payload: RemediationPayload;
}

export function RemediationButton({
  sessionId,
  pointId,
  title,
  compact = false,
}: {
  sessionId: string;
  pointId: string;
  title: string;
  compact?: boolean;
}) {
  const [open, setOpen] = useState(false);
  const [reason, setReason] = useState("我还没理解，请换个方式再讲一遍。");
  const [manifest, setManifest] = useState<ConsentManifest | null>(null);
  const [confirmed, setConfirmed] = useState(false);
  const [result, setResult] = useState<RemediationResult | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  async function prepare() {
    setError("");
    try {
      const response = await fetch(`${publicApiUrl}/sessions/${sessionId}/consent-manifest?operation=remediation`);
      if (!response.ok) throw new Error("无法读取隐私清单");
      const next = (await response.json()) as ConsentManifest;
      if (next.confirmation_required) {
        setManifest(next);
      } else {
        await send(false);
      }
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "补课失败");
    }
  }

  async function send(consent: boolean) {
    setManifest(null);
    setBusy(true);
    setError("");
    try {
      const next = await mutate<RemediationResult>(`/knowledge-points/${pointId}/remediation`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ reason, confirm_external_upload: consent }),
      });
      setResult(next);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "补课失败");
    } finally {
      setBusy(false);
    }
  }

  function close() {
    setOpen(false);
    setManifest(null);
    setResult(null);
    setConfirmed(false);
    setError("");
  }

  return (
    <>
      <button className={compact ? "text-button" : "button secondary"} type="button" onClick={() => setOpen(true)}>
        针对性补课
      </button>
      {open ? (
        <div className="modal-backdrop" role="presentation">
          <section className="remediation-modal panel" role="dialog" aria-modal="true" aria-labelledby="remediation-title">
            <div className="source-modal-head">
              <div>
                <span className="eyebrow">TARGETED REMEDIATION</span>
                <h2 id="remediation-title">补上「{title}」</h2>
              </div>
              <button className="icon-button" type="button" onClick={close} aria-label="关闭">×</button>
            </div>
            {!result ? (
              <>
                <label>
                  你卡在哪里？
                  <textarea value={reason} onChange={(event) => setReason(event.target.value)} rows={3} />
                </label>
                {error ? <p className="form-error">{error}</p> : null}
                {manifest ? (
                  <section className="inline-consent">
                    <span className="eyebrow">SEND TO {manifest.provider}</span>
                    <p>将发送：{manifest.will_send.join("、")}。不会发送：{manifest.will_not_send.join("、")}。</p>
                    <label><input type="checkbox" checked={confirmed} onChange={(event) => setConfirmed(event.target.checked)} />仅同意本次补课请求</label>
                    <div><button className="button secondary" onClick={() => setManifest(null)}>取消</button><button className="button primary" disabled={!confirmed} onClick={() => send(true)}>确认并生成</button></div>
                  </section>
                ) : (
                  <div className="modal-actions">
                    <button className="button secondary" type="button" onClick={close}>取消</button>
                    <button className="button primary" type="button" disabled={busy} onClick={prepare}>{busy ? "生成中…" : "生成补课内容"}</button>
                  </div>
                )}
              </>
            ) : (
              <div className="remediation-result">
                <span className="eyebrow">DIAGNOSIS</span>
                <p>{result.payload.diagnosis}</p>
                <span className="eyebrow">SIMPLER EXPLANATION</span>
                <p>{result.payload.simpler_explanation}</p>
                <span className="eyebrow">ANALOGY</span>
                <p>{result.payload.analogy}</p>
                <span className="eyebrow">WORKED EXAMPLE</span>
                <p>{result.payload.worked_example}</p>
                <span className="eyebrow">QUICK SELF-CHECK</span>
                <p>{result.payload.quick_check}</p>
                <div className="modal-actions"><button className="button primary" type="button" onClick={close}>回到验收</button></div>
              </div>
            )}
          </section>
        </div>
      ) : null}
    </>
  );
}
