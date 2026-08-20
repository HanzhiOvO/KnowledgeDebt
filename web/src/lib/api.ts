import "server-only";

import type {
  ApiResult,
  AssessmentQuestion,
  Course,
  Debt,
  HomePayload,
  InboxItem,
  ProviderSettings,
  ProviderUsage,
  ReviewItem,
  ScheduleConnection,
  ScheduleOccurrence,
  SessionDetail,
} from "@/types/domain";

const API_URL = process.env.KNOWLEDGEDEBT_API_URL ?? "http://127.0.0.1:8123";

async function request<T>(path: string): Promise<ApiResult<T>> {
  try {
    const response = await fetch(`${API_URL}${path}`, {
      cache: "no-store",
      headers: process.env.KNOWLEDGEDEBT_ACCESS_TOKEN
        ? { Authorization: `Bearer ${process.env.KNOWLEDGEDEBT_ACCESS_TOKEN}` }
        : undefined,
    });
    if (!response.ok) {
      return { ok: false, error: `API ${response.status}: ${response.statusText}` };
    }
    return { ok: true, data: (await response.json()) as T };
  } catch {
    return {
      ok: false,
      error: `无法连接 ${API_URL}。运行 make dev 后刷新页面。`,
    };
  }
}

export function getHome() {
  return request<HomePayload>("/home");
}

export function getCourses() {
  return request<Course[]>("/courses");
}

export function getCourse(courseId: string) {
  return request<Course>(`/courses/${courseId}`);
}

export function getSession(sessionId: string) {
  return request<SessionDetail>(`/sessions/${sessionId}`);
}

export function getAssessment(sessionId: string) {
  return request<AssessmentQuestion[]>(`/sessions/${sessionId}/assessment`);
}

export function getDebts() {
  return request<Debt[]>("/debts");
}

export function getProviderSettings() {
  return request<ProviderSettings>("/settings/provider");
}

export function getProviderUsage() {
  return request<ProviderUsage>("/settings/provider-usage");
}

export function getSchedule() {
  return request<ScheduleOccurrence[]>("/schedule/occurrences");
}

export function getScheduleConnection() {
  return request<ScheduleConnection>("/schedule/connection");
}

export function getReviews() {
  return request<ReviewItem[]>("/reviews");
}

export function getInbox() {
  return request<InboxItem[]>("/inbox");
}
