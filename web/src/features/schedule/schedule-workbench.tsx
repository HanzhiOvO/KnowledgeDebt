"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useMemo, useState } from "react";

import { mutate, publicApiUrl } from "@/lib/client-api";
import type { ScheduleConnection, ScheduleOccurrence, ScheduleSyncBatch, SessionDetail } from "@/types/domain";

const weekdays = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"];

export function ScheduleWorkbench({
  initialOccurrences,
  connection,
  timezone,
}: {
  initialOccurrences: ScheduleOccurrence[];
  connection: ScheduleConnection | null;
  timezone: string;
}) {
  const router = useRouter();
  const [weekOffset, setWeekOffset] = useState(0);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const [preview, setPreview] = useState<ScheduleSyncBatch | null>(null);
  const week = useMemo(() => weekDateKeys(weekOffset, timezone), [weekOffset, timezone]);
  const today = dateKeyInTimeZone(new Date(), timezone);
  const byDate = useMemo(() => Object.groupBy(initialOccurrences, (item) => item.occurrence_date), [initialOccurrences]);

  async function importFixture(formData: FormData) {
    setBusy(true);
    setError("");
    setMessage("");
    try {
      const response = await fetch(`${publicApiUrl}/schedule/sync-batches/preview`, { method: "POST", body: formData });
      const body = (await response.json().catch(() => ({}))) as ScheduleSyncBatch & { detail?: string };
      if (!response.ok) throw new Error(body.detail ?? "导入失败");
      setPreview(body);
      setMessage("快照已解析，请核对新增、修改、移除和冲突后再应用。 ");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "导入失败");
    } finally {
      setBusy(false);
    }
  }

  async function applyPreview() {
    if (!preview) return;
    setBusy(true);
    setError("");
    try {
      await mutate(`/schedule/sync-batches/${preview.id}/apply`, { method: "POST" });
      setMessage(`课表快照已原子应用：新增 ${preview.diff.summary.added}、修改 ${preview.diff.summary.modified}、移除 ${preview.diff.summary.removed}`);
      setPreview(null);
      router.refresh();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "课表快照应用失败，未改动现有课表");
    } finally {
      setBusy(false);
    }
  }

  async function beginLogin() {
    setBusy(true);
    setError("");
    try {
      const result = await mutate<{ message: string }>("/schedule/connection/login?mode=account", { method: "POST" });
      setMessage(result.message);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "连接失败");
    } finally {
      setBusy(false);
    }
  }

  async function openOccurrence(id: string) {
    setBusy(true);
    setError("");
    try {
      const session = await mutate<SessionDetail>(`/schedule/occurrences/${id}/materialize`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ reason: "opened" }),
      });
      router.push(`/sessions/${session.id}`);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "无法建立 Session");
      setBusy(false);
    }
  }

  return (
    <>
      <header className="page-header schedule-header">
        <div>
          <span className="eyebrow">ACADEMIC SCHEDULE</span>
          <h1>课表与课堂实例</h1>
          <p>未来课程只保留 Occurrence；课堂发生、有证据或你主动打开时才建立 Session。时间按 {timezone} 显示。</p>
        </div>
        <div className="connection-summary">
          <span className={`connection-orb state-${connection?.state ?? "disconnected"}`} />
          <span><strong>{connection?.display_name ?? "教务系统未连接"}</strong><small>{connection?.last_synced_at ? `上次同步 ${formatDateTime(connection.last_synced_at, timezone)}` : "尚未同步"}</small></span>
          <span className="badge">{connectionState(connection?.state)}</span>
        </div>
      </header>

      <section className="panel schedule-toolbar">
        <div className="week-switcher">
          <button aria-label="上一周" className="icon-button bordered" onClick={() => setWeekOffset((value) => value - 1)}>←</button>
          <div><strong>{formatRange(week[0], week[6])}</strong><small>{weekOffset === 0 ? "本周" : weekOffset > 0 ? `${weekOffset} 周后` : `${-weekOffset} 周前`}</small></div>
          <button aria-label="下一周" className="icon-button bordered" onClick={() => setWeekOffset((value) => value + 1)}>→</button>
          {weekOffset !== 0 ? <button className="text-button" onClick={() => setWeekOffset(0)}>回到本周</button> : null}
        </div>
        <div className="schedule-actions">
          <button className="button secondary" disabled={busy} onClick={beginLogin}>验证连接方式</button>
          <details className="import-menu">
            <summary className="button primary">导入脱敏课表 fixture</summary>
            <form action={importFixture} className="floating-form">
              <strong>导入已授权的脱敏 JSON</strong>
              <p>不会保存账号密码，也不会绕过验证码或 SSO。必须包含当前节次时间定义。</p>
              <input accept="application/json,.json" name="file" required type="file" />
              <button className="button primary" disabled={busy}>{busy ? "解析中…" : "读取并预览差异"}</button>
            </form>
          </details>
        </div>
      </section>

      {message ? <div className="notice success" role="status">{message}</div> : null}
      {error ? <div className="notice error" role="alert">{error}</div> : null}
      {connection?.capability && !connection.capability.live_login ? (
        <div className="notice info"><strong>实时连接仍需技术验证。</strong> {connection.capability.reason}</div>
      ) : null}

      {preview ? <div className="modal-backdrop" role="presentation"><section className="consent-modal panel schedule-preview" role="dialog" aria-modal="true" aria-labelledby="schedule-preview-title"><span className="eyebrow">AUTHORITATIVE SNAPSHOT</span><h2 id="schedule-preview-title">确认课表同步差异</h2><div className="sync-summary"><span><strong>{preview.diff.summary.added}</strong>新增</span><span><strong>{preview.diff.summary.modified}</strong>修改</span><span><strong>{preview.diff.summary.removed}</strong>移除</span><span><strong>{preview.diff.summary.conflicts}</strong>冲突</span></div><div className="sync-change-list">{preview.diff.added.slice(0, 4).map((item) => <p key={`add-${item.external_id}`}><b>新增</b>{item.course_name} · {item.occurrence_date}</p>)}{preview.diff.modified.slice(0, 4).map((item) => <p key={`change-${item.external_id}`}><b>修改</b>{item.course_name} · {item.occurrence_date}</p>)}{preview.diff.removed.slice(0, 4).map((item) => <p key={`remove-${item.external_id}`}><b>移除</b>{item.course_name} · {item.occurrence_date}</p>)}{preview.diff.conflicts.map((item) => <p className="conflict" key={`conflict-${item.external_id}`}><b>需注意</b>{item.reason}</p>)}</div><p className="muted">应用会在单个数据库事务中完成。新快照缺少的未来课堂只会标记为已移除，已有 Session、录音和笔记不会删除。</p><div className="modal-actions"><button className="button secondary" onClick={() => setPreview(null)} type="button">取消</button><button className="button primary" disabled={busy} onClick={() => void applyPreview()} type="button">{busy ? "应用中…" : "确认并应用快照"}</button></div></section></div> : null}

      <section className="weekly-grid" aria-label={`课表 ${formatRange(week[0], week[6])}`}>
        {week.map((date, index) => {
          const items = byDate[date] ?? [];
          return (
            <article className={date === today ? "day-column today" : "day-column"} key={date}>
              <header><span>{weekdays[index]}</span><strong>{Number(date.slice(8, 10))}</strong></header>
              <div className="day-events">
                {items.map((occurrence) => (
                  <OccurrenceCard occurrence={occurrence} key={occurrence.id} onOpen={() => openOccurrence(occurrence.id)} timezone={timezone} />
                ))}
                {!items.length ? <span className="day-empty">—</span> : null}
              </div>
            </article>
          );
        })}
      </section>

      <section className="legend-row" aria-label="课表图例">
        <span><i className="legend-dot regular" />正常课程</span>
        <span><i className="legend-dot adjustment" />调课</span>
        <span><i className="legend-dot makeup" />补课</span>
        <span><i className="legend-dot cancelled" />已取消</span>
        <span className="legend-note">取消项不会建立 Session，也不会产生知识债务。</span>
      </section>
    </>
  );
}

function OccurrenceCard({ occurrence, onOpen, timezone }: { occurrence: ScheduleOccurrence; onOpen: () => void; timezone: string }) {
  const content = (
    <>
      <time>{formatClock(occurrence.starts_at, timezone)}–{formatClock(occurrence.ends_at, timezone)}</time>
      <strong>{occurrence.rule.course_name}</strong>
      <small>{[occurrence.building, occurrence.room].filter(Boolean).join(" ") || "地点待同步"}</small>
      {occurrence.source_kind !== "regular" ? <span className="event-label">{occurrence.source_kind === "makeup" ? "补课" : "调课"}</span> : null}
      {occurrence.sync_status === "removed" ? <span className="event-label">已从最新课表移除</span> : null}
    </>
  );
  if (occurrence.session_id) return <Link className={`occurrence-card ${occurrence.source_kind} ${occurrence.status}`} href={`/sessions/${occurrence.session_id}`}>{content}</Link>;
  return <button className={`occurrence-card ${occurrence.source_kind} ${occurrence.status}`} disabled={occurrence.status === "cancelled" || occurrence.sync_status === "removed"} onClick={onOpen}>{content}<span className="open-hint">打开并建立 Session</span></button>;
}

function weekDateKeys(offset: number, timezone: string) {
  const today = dateKeyInTimeZone(new Date(), timezone);
  const current = dateKeyToUtcNoon(today);
  const weekday = (current.getUTCDay() + 6) % 7;
  return Array.from({ length: 7 }, (_, index) => addDays(today, -weekday + offset * 7 + index));
}

function dateKeyInTimeZone(value: Date, timezone: string) {
  const parts = new Intl.DateTimeFormat("en-CA", {
    timeZone: timezone,
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).formatToParts(value);
  const part = (type: Intl.DateTimeFormatPartTypes) => parts.find((item) => item.type === type)?.value ?? "";
  return `${part("year")}-${part("month")}-${part("day")}`;
}

function dateKeyToUtcNoon(value: string) {
  const [year, month, day] = value.split("-").map(Number);
  return new Date(Date.UTC(year, month - 1, day, 12));
}

function addDays(value: string, amount: number) {
  const date = dateKeyToUtcNoon(value);
  date.setUTCDate(date.getUTCDate() + amount);
  return `${date.getUTCFullYear()}-${String(date.getUTCMonth() + 1).padStart(2, "0")}-${String(date.getUTCDate()).padStart(2, "0")}`;
}

function formatRange(start: string, end: string) {
  const [startYear, startMonth, startDay] = start.split("-").map(Number);
  const [endYear, endMonth, endDay] = end.split("-").map(Number);
  const endLabel = startYear === endYear
    ? `${endMonth}月${endDay}日`
    : `${endYear}年${endMonth}月${endDay}日`;
  return `${startYear}年${startMonth}月${startDay}日 – ${endLabel}`;
}

function formatClock(value: string, timezone: string) { return new Intl.DateTimeFormat("zh-CN", { timeZone: timezone, hour: "2-digit", minute: "2-digit", hour12: false }).format(new Date(value)); }
function formatDateTime(value: string, timezone: string) { return new Intl.DateTimeFormat("zh-CN", { timeZone: timezone, month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit", hour12: false }).format(new Date(value)); }
function connectionState(state?: string) { return ({ connected: "已连接", syncing: "同步中", error: "同步异常", reauth_required: "需要重新登录", fixture_required: "等待 fixture" } as Record<string, string>)[state ?? ""] ?? "未连接"; }
