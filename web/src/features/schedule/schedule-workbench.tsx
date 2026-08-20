"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useMemo, useState } from "react";

import { mutate, publicApiUrl } from "@/lib/client-api";
import type { ScheduleConnection, ScheduleOccurrence, SessionDetail } from "@/types/domain";

const weekdays = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"];

export function ScheduleWorkbench({
  initialOccurrences,
  connection,
}: {
  initialOccurrences: ScheduleOccurrence[];
  connection: ScheduleConnection | null;
}) {
  const router = useRouter();
  const [weekOffset, setWeekOffset] = useState(0);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const week = useMemo(() => weekDates(weekOffset), [weekOffset]);
  const byDate = useMemo(() => Object.groupBy(initialOccurrences, (item) => item.occurrence_date), [initialOccurrences]);

  async function importFixture(formData: FormData) {
    setBusy(true);
    setError("");
    setMessage("");
    try {
      const response = await fetch(`${publicApiUrl}/schedule/import-fixture`, { method: "POST", body: formData });
      const body = (await response.json().catch(() => ({}))) as { detail?: string; occurrence_count?: number };
      if (!response.ok) throw new Error(body.detail ?? "导入失败");
      setMessage(`已同步 ${body.occurrence_count ?? 0} 个课堂实例`);
      router.refresh();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "导入失败");
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
          <p>未来课程只保留 Occurrence；课堂发生、有证据或你主动打开时才建立 Session。</p>
        </div>
        <div className="connection-summary">
          <span className={`connection-orb state-${connection?.state ?? "disconnected"}`} />
          <span><strong>{connection?.display_name ?? "教务系统未连接"}</strong><small>{connection?.last_synced_at ? `上次同步 ${formatDateTime(connection.last_synced_at)}` : "尚未同步"}</small></span>
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
              <button className="button primary" disabled={busy}>{busy ? "同步中…" : "开始同步"}</button>
            </form>
          </details>
        </div>
      </section>

      {message ? <div className="notice success" role="status">{message}</div> : null}
      {error ? <div className="notice error" role="alert">{error}</div> : null}
      {connection?.capability && !connection.capability.live_login ? (
        <div className="notice info"><strong>实时连接仍需技术验证。</strong> {connection.capability.reason}</div>
      ) : null}

      <section className="weekly-grid" aria-label={`课表 ${formatRange(week[0], week[6])}`}>
        {week.map((date, index) => {
          const key = isoDate(date);
          const items = byDate[key] ?? [];
          return (
            <article className={key === isoDate(new Date()) ? "day-column today" : "day-column"} key={key}>
              <header><span>{weekdays[index]}</span><strong>{date.getDate()}</strong></header>
              <div className="day-events">
                {items.map((occurrence) => (
                  <OccurrenceCard occurrence={occurrence} key={occurrence.id} onOpen={() => openOccurrence(occurrence.id)} />
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

function OccurrenceCard({ occurrence, onOpen }: { occurrence: ScheduleOccurrence; onOpen: () => void }) {
  const content = (
    <>
      <time>{formatClock(occurrence.starts_at)}–{formatClock(occurrence.ends_at)}</time>
      <strong>{occurrence.rule.course_name}</strong>
      <small>{[occurrence.building, occurrence.room].filter(Boolean).join(" ") || "地点待同步"}</small>
      {occurrence.source_kind !== "regular" ? <span className="event-label">{occurrence.source_kind === "makeup" ? "补课" : "调课"}</span> : null}
    </>
  );
  if (occurrence.session_id) return <Link className={`occurrence-card ${occurrence.source_kind} ${occurrence.status}`} href={`/sessions/${occurrence.session_id}`}>{content}</Link>;
  return <button className={`occurrence-card ${occurrence.source_kind} ${occurrence.status}`} disabled={occurrence.status === "cancelled"} onClick={onOpen}>{content}<span className="open-hint">打开并建立 Session</span></button>;
}

function weekDates(offset: number) {
  const current = new Date();
  current.setHours(12, 0, 0, 0);
  const weekday = (current.getDay() + 6) % 7;
  current.setDate(current.getDate() - weekday + offset * 7);
  return Array.from({ length: 7 }, (_, index) => {
    const next = new Date(current);
    next.setDate(current.getDate() + index);
    return next;
  });
}

function isoDate(value: Date) { return `${value.getFullYear()}-${String(value.getMonth() + 1).padStart(2, "0")}-${String(value.getDate()).padStart(2, "0")}`; }
function formatRange(start: Date, end: Date) { return `${start.getFullYear()}年${start.getMonth() + 1}月${start.getDate()}日 – ${end.getMonth() + 1}月${end.getDate()}日`; }
function formatClock(value: string) { return new Intl.DateTimeFormat("zh-CN", { hour: "2-digit", minute: "2-digit", hour12: false }).format(new Date(value)); }
function formatDateTime(value: string) { return new Intl.DateTimeFormat("zh-CN", { month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit", hour12: false }).format(new Date(value)); }
function connectionState(state?: string) { return ({ connected: "已连接", syncing: "同步中", error: "同步异常", reauth_required: "需要重新登录", fixture_required: "等待 fixture" } as Record<string, string>)[state ?? ""] ?? "未连接"; }

