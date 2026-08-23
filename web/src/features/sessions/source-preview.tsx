"use client";

import Image from "next/image";
import { useState } from "react";

import { publicApiUrl } from "@/lib/client-api";
import type { ChunkDetail, Resource, ResourceChunk, SourceRef } from "@/types/domain";

interface PreviewState {
  title: string;
  locator: string;
  text: string;
  imageUrl?: string | null;
  resourceId: string;
  resourceName: string;
  resourceType: string;
}

async function readJson<T>(url: string): Promise<T> {
  const response = await fetch(url);
  if (!response.ok) {
    const body = (await response.json().catch(() => ({}))) as { detail?: string };
    throw new Error(body.detail ?? `读取来源失败 (${response.status})`);
  }
  return (await response.json()) as T;
}

function findSegment(resource: Resource, source: SourceRef) {
  if (!resource.transcript_segments?.length) return null;
  const start = source.start_time;
  const end = source.end_time;
  return resource.transcript_segments.find((segment) => {
    const segmentStart = segment.global_start ?? segment.start_time;
    const segmentEnd = segment.global_end ?? segment.end_time;
    if (start == null || end == null) return false;
    return Math.abs(segmentStart - start) < 0.5 && Math.abs(segmentEnd - end) < 0.5;
  });
}

function findChunk(resource: Resource, source: SourceRef) {
  if (!resource.chunks?.length) return null;
  if (source.chunk_id) {
    return resource.chunks.find((chunk) => chunk.id === source.chunk_id) ?? null;
  }
  if (source.page) {
    return resource.chunks.find((chunk) => chunk.page === source.page) ?? null;
  }
  if (source.slide) {
    return resource.chunks.find((chunk) => chunk.slide === source.slide) ?? null;
  }
  return null;
}

export function SourcePreviewButton({ source }: { source: SourceRef }) {
  const [preview, setPreview] = useState<PreviewState | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  async function open() {
    setBusy(true);
    setError("");
    try {
      const resource = await readJson<Resource>(`${publicApiUrl}/resources/${source.resource_id}`);
      let text = "";
      let imageUrl: string | null = null;
      let chunk: ResourceChunk | null = null;

      if (source.locator_type === "transcript") {
        const segment = findSegment(resource, source);
        text = segment?.text ?? "没有找到对应的转写片段。";
      } else {
        chunk = findChunk(resource, source);
        if (source.chunk_id) {
          const detail = await readJson<ChunkDetail>(`${publicApiUrl}/chunks/${source.chunk_id}`);
          text = detail.text ?? chunk?.text ?? "";
          imageUrl = detail.preview_url ? `${publicApiUrl}${detail.preview_url}` : null;
        } else if (chunk) {
          const detail = await readJson<ChunkDetail>(`${publicApiUrl}/chunks/${chunk.id}`);
          text = detail.text ?? chunk.text ?? "";
          imageUrl = detail.preview_url ? `${publicApiUrl}${detail.preview_url}` : null;
        } else if (resource.extracted_text) {
          text = resource.extracted_text.slice(0, 1600);
        }
      }
      if (!text.trim()) text = "该来源没有可显示的文本，请打开原始文件查看。";
      setPreview({
        title: resource.name || source.label,
        locator: source.locator || source.locator_type || "source",
        text,
        imageUrl,
        resourceId: resource.id,
        resourceName: resource.name || source.label,
        resourceType: resource.type,
      });
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "读取来源失败");
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <button className="source-chip" type="button" onClick={open} disabled={busy}>
        {busy ? "读取中…" : source.label}{source.locator ? ` · ${source.locator}` : ""}
      </button>
      {error ? <span className="form-error">{error}</span> : null}
      {preview ? (
        <div className="modal-backdrop" role="presentation">
          <section className="source-modal panel" role="dialog" aria-modal="true" aria-labelledby="source-preview-title">
            <div className="source-modal-head">
              <div>
                <span className="eyebrow">EVIDENCE SOURCE · {preview.resourceType.toUpperCase()}</span>
                <h2 id="source-preview-title">{preview.title}</h2>
                <p>{preview.locator}</p>
              </div>
              <button className="icon-button" type="button" onClick={() => setPreview(null)} aria-label="关闭">×</button>
            </div>
            {preview.imageUrl ? (
              <Image className="source-visual" src={preview.imageUrl} alt={`${preview.resourceName} 来源预览`} width={1200} height={800} unoptimized />
            ) : null}
            <pre className="source-text">{preview.text}</pre>
            <div className="modal-actions">
              <a className="button secondary" href={`${publicApiUrl}/resources/${preview.resourceId}/raw`} target="_blank" rel="noreferrer">打开原始文件</a>
              <button className="button primary" type="button" onClick={() => setPreview(null)}>关闭</button>
            </div>
          </section>
        </div>
      ) : null}
    </>
  );
}

export function SourceList({ sources }: { sources: SourceRef[] }) {
  return (
    <div className="source-list">
      {sources.map((source, index) => (
        <SourcePreviewButton key={`${source.resource_id}-${source.locator ?? ""}-${index}`} source={source} />
      ))}
    </div>
  );
}
