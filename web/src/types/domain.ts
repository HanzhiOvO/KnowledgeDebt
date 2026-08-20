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
  transcript_segments?: TranscriptSegment[];
  automation?: ResourceAutomation;
  active_transcription_job?: Job | null;
}

export interface TranscriptSegment {
  id: string;
  start_time: number;
  end_time: number;
  global_start: number;
  global_end: number;
  text: string;
}

export interface ResourceAutomation {
  resource_id: string;
  transcription_state:
    | "saving"
    | "saved"
    | "preparing"
    | "awaiting_consent"
    | "queued"
    | "transcribing"
    | "partial"
    | "transcribed"
    | "failed"
    | "cancelled";
  auto_transcribe: boolean;
  failure_reason?: string | null;
  last_job_id?: string | null;
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

export interface AssessmentQuestion {
  id: string;
  session_id: string;
  knowledge_point_ids: string[];
  prompt: string;
  level: string;
  question_type: string;
  expected_mastery: number;
  parent_question_id?: string | null;
  sources: SourceRef[];
}

export interface Job {
  id: string;
  kind: string;
  status: "queued" | "running" | "succeeded" | "failed" | "cancelled";
  stage: string;
  progress: number;
  error?: string | null;
  result?: Record<string, unknown> | null;
}

export interface ConsentManifest {
  operation: string;
  provider: string;
  external: boolean;
  providers?: Array<{
    id?: string | null;
    name: string;
    vendor?: string;
    model?: string;
    external: boolean;
  }>;
  resources: Array<{ id: string; name: string; type: string }>;
  will_send: string[];
  will_not_send: string[];
  confirmation_required: boolean;
}

export interface SessionDetail extends SessionSummary {
  notes: string;
  resources: Resource[];
  knowledge_points: KnowledgePoint[];
  debts: Debt[];
  learning_steps: LearningStep[];
  reconstruction?: Reconstruction | null;
  automation?: {
    occurrence_id?: string | null;
    materialization_reason?: string | null;
    title_locked: boolean;
    title_source: string;
    title_confidence: number;
  };
}

export interface HomePayload {
  sessions: SessionSummary[];
  open_debt_count: number;
  urgent_debt_count: number;
  pending_session_count: number;
  minimum_minutes: number;
  today_occurrences: ScheduleOccurrence[];
  pending_automation: Array<{
    kind: string;
    session_id: string;
    resource_id: string;
    state: string;
    name: string;
  }>;
  pending_review_count: number;
  jobs: Job[];
}

export interface AcademicTerm {
  id: string;
  name: string;
  starts_on: string;
  ends_on: string;
  timezone: string;
  current: boolean;
}

export interface ScheduleRule {
  id: string;
  course_name: string;
  course_code?: string | null;
  teacher?: string | null;
  campus?: string | null;
  building?: string | null;
  room?: string | null;
  weekday: number;
  start_period: number;
  end_period: number;
  weeks: number[];
  odd_even: "all" | "odd" | "even";
}

export interface ScheduleOccurrence {
  id: string;
  occurrence_date: string;
  starts_at: string;
  ends_at: string;
  status: "scheduled" | "cancelled";
  source_kind: "regular" | "adjustment" | "makeup";
  room?: string | null;
  building?: string | null;
  teacher?: string | null;
  session_id?: string | null;
  rule: ScheduleRule;
}

export interface ScheduleConnection {
  id: string;
  connector: string;
  display_name: string;
  state: string;
  sync_interval_minutes: number;
  last_synced_at?: string | null;
  last_error?: string | null;
  reauth_required: boolean;
  capability: { live_login: boolean; fixture_import: boolean; reason: string };
  base_url: string;
}

export interface ReviewItem {
  id: string;
  kind: "archive_match" | "session_topic" | "schedule_conflict" | "transcription_failure";
  status: string;
  subject_type: string;
  subject_id: string;
  title: string;
  proposed_value?: string | null;
  confidence: number;
  reasons: string[];
  navigation_path?: string | null;
  created_at: string;
}

export interface InboxItem {
  id: string;
  name: string;
  type: string;
  captured_at: string;
  matching_status: string;
  match_confidence: number;
  match_reasons: string[];
  suggested_session_id?: string | null;
  adopted_resource_id?: string | null;
  archived: boolean;
}

export interface ProviderProfile {
  id: string;
  name: string;
  vendor: string;
  adapter: string;
  base_url: string;
  region?: string | null;
  default_model: string;
  capabilities: string[];
  external: boolean;
  enabled: boolean;
  implementation_status: string;
  credential_reference?: string | null;
  credential_configured: boolean;
  last_test_status?: string | null;
  last_test_message?: string | null;
}

export interface LocalASRStatus {
  adapter: string;
  binary: string;
  binary_ready: boolean;
  binary_resolved?: string | null;
  model: string;
  model_dir?: string | null;
  model_ready: boolean;
  model_resolved?: string | null;
  model_bytes?: number | null;
  language: string;
  threads: number;
  timeout_seconds: number;
  ffmpeg_ready: boolean;
  ready: boolean;
}

export interface ProviderSettings {
  storage_provider: string;
  profiles: ProviderProfile[];
  defaults: Record<string, ProviderProfile>;
  secret_encryption_configured: boolean;
  local_asr?: LocalASRStatus | null;
}

export interface ProviderUsage {
  month: string;
  request_count: number;
  transcription_minutes: number;
  known_cost: number;
  unknown_cost_count: number;
  failure_count: number;
  items: Array<{
    id: string;
    operation: string;
    provider_name: string;
    model?: string | null;
    status: string;
    audio_minutes?: number | null;
    estimated_cost?: number | null;
    cost_known: boolean;
    created_at: string;
  }>;
}

export type ApiResult<T> =
  | { ok: true; data: T }
  | { ok: false; error: string };
