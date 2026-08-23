"use client";

import Link from "next/link";
import { useEffect, useMemo, useState } from "react";

import type { HomePayload } from "@/types/domain";

const STORAGE_KEY = "knowledgedebt:onboarding:v0.2";

const steps = [
  { id: "schedule_ready", title: "导入课程表", detail: "先用脱敏样例预览并确认；实时教务连接仍会诚实显示能力状态。", href: "/schedule", action: "打开课表" },
  { id: "transcription_configured", title: "选择本地模型或配置 API", detail: "本地模型不会静默下载；外部 Provider 每次外发仍需确认。", href: "/settings#providers", action: "配置转写" },
  { id: "transcription_tested", title: "测试转写服务", detail: "连接测试会检查真实运行时、模型、鉴权和服务响应。", href: "/settings#providers", action: "测试连接" },
  { id: "session_created", title: "创建或打开第一节课", detail: "Session 可以先存在，录音和资料随后补充。", href: "/courses", action: "查看课程" },
  { id: "recording_started", title: "开始第一段录音", detail: "录音分片会先写入浏览器持久存储，再幂等上传到本地后端。", href: "/courses", action: "选择课堂" },
] as const;

type StepId = (typeof steps)[number]["id"];

export function FirstRunGuide({ state }: { state: HomePayload["onboarding"] }) {
  const [ready, setReady] = useState(false);
  const [skipped, setSkipped] = useState<StepId[]>([]);

  useEffect(() => {
    const timer = window.setTimeout(() => {
      try {
        const stored = JSON.parse(window.localStorage.getItem(STORAGE_KEY) ?? "[]") as unknown;
        if (Array.isArray(stored)) {
          setSkipped(stored.filter((item): item is StepId => steps.some((step) => step.id === item)));
        }
      } catch {
        window.localStorage.removeItem(STORAGE_KEY);
      } finally {
        setReady(true);
      }
    }, 0);
    return () => window.clearTimeout(timer);
  }, []);

  const visible = useMemo(
    () => steps.filter((step) => !state[step.id] && !skipped.includes(step.id)),
    [skipped, state],
  );
  const completed = steps.filter((step) => state[step.id] || skipped.includes(step.id)).length;

  function skip(id: StepId) {
    const next = [...new Set([...skipped, id])];
    setSkipped(next);
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify(next));
  }

  if (!ready || !visible.length) return null;

  return (
    <section aria-labelledby="first-run-title" className="panel first-run-guide">
      <header>
        <div>
          <span className="eyebrow">FIRST RUN · 可随时跳过</span>
          <h2 id="first-run-title">用五步接住第一节课</h2>
          <p>每一步都可以以后再做；跳过只隐藏这条引导，不会改动课程或 Provider。</p>
        </div>
        <strong aria-label={`已完成 ${completed} 步，共 5 步`}>{completed}/5</strong>
      </header>
      <ol>
        {visible.map((step) => (
          <li key={step.id}>
            <span className="guide-step-number">{steps.findIndex((item) => item.id === step.id) + 1}</span>
            <span className="guide-step-copy"><strong>{step.title}</strong><small>{step.detail}</small></span>
            <Link className="button secondary" href={step.href}>{step.action}</Link>
            <button aria-label={`暂时跳过：${step.title}`} className="button ghost" onClick={() => skip(step.id)} type="button">暂时跳过</button>
          </li>
        ))}
      </ol>
    </section>
  );
}
