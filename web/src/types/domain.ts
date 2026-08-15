export type DebtStatus = "unseen" | "partial" | "mastered";

export interface Course {
  id: string;
  name: string;
  description: string;
  semester: string;
  teacher?: string | null;
  schedule?: string | null;
  profile: Record<string, number>;
  sessions?: SessionSummary[];
}

export interface SessionSummary {
  id: string;
  course_id: string;
  course_name?: string;
  title: string;
  starts_at?: string | null;
  ends_at?: string | null;
  reconstruction_score: number;
  learning_coverage: number;
  status: "open" | "complete";
  open_debt_count?: number;
}

export interface SourceRef {
  resource_id: string;
  label: string;
  locator?: string | null;
  start_time?: number | null;
  end_time?: number | null;
  page?: number | null;
  slide?: number | null;
  chunk_id?: string | null;
}

export interface Resource {
  id: string;
  type: string;
  evidence_level: string;
  name: string;
  mime_type?: string | null;
  external_url?: string | null;
  duration_seconds?: number | null;
  start_offset?: number | null;
  end_offset?: number | null;
  session_duration?: number | null;
  capture_range?: number[];
  chunks?: Array<{
    id: string;
    locator_type: string;
    page?: number | null;
    slide?: number | null;
    content_kind: string;
  }>;
  coverage: number;
  quality: number;
  relevance: number;
  upload_state: string;
}

export interface KnowledgePoint {
  id: string;
  title: string;
  description: string;
  importance: number;
  expected_mastery: number;
  current_mastery: number;
  confidence: string;
  sources: SourceRef[];
}

export interface Debt {
  id: string;
  knowledge_point_id: string;
  session_id: string;
  title: string;
  description: string;
  current_mastery: number;
  target_mastery: number;
  status: DebtStatus;
  priority: number;
  estimated_minutes: number;
  blocks_next_session: boolean;
}

export interface TimelineItem {
  start_time?: number | null;
  end_time?: number | null;
  title: string;
  summary: string;
  confidence: string;
  sources: SourceRef[];
}

export interface Reconstruction {
  title: string;
  summary: string;
  topics: string[];
  timeline: TimelineItem[];
  teacher_emphasis: string[];
  examples: string[];
  confirmed: string[];
  inferred: string[];
}

export interface LearningStep {
  id: string;
  position: number;
  title: string;
  brief_explanation: string;
  full_explanation: string;
  estimated_minutes: number;
  completed: boolean;
  sources: SourceRef[];
}

export interface SessionDetail extends SessionSummary {
  notes: string;
  resources: Resource[];
  knowledge_points: KnowledgePoint[];
  debts: Debt[];
  learning_steps: LearningStep[];
  reconstruction?: Reconstruction | null;
}

export interface HomePayload {
  sessions: SessionSummary[];
  open_debt_count: number;
  urgent_debt_count: number;
  pending_session_count: number;
  minimum_minutes: number;
}

export type ApiResult<T> =
  | { ok: true; data: T }
  | { ok: false; error: string };
