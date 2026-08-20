"""Portable v0.2 automation-workbench schema shared by direct setup and Alembic."""

AUTOMATION_SCHEMA = """
CREATE TABLE IF NOT EXISTS provider_profiles (
  id TEXT PRIMARY KEY, name TEXT NOT NULL, vendor TEXT NOT NULL, adapter TEXT NOT NULL,
  base_url TEXT NOT NULL DEFAULT '', region TEXT, credential_ciphertext TEXT,
  credential_reference TEXT, default_model TEXT NOT NULL DEFAULT '',
  capabilities_json TEXT NOT NULL DEFAULT '[]', external INTEGER NOT NULL DEFAULT 1,
  enabled INTEGER NOT NULL DEFAULT 1, implementation_status TEXT NOT NULL DEFAULT 'available',
  last_test_status TEXT, last_test_message TEXT, last_tested_at TEXT,
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS provider_defaults (
  provider_group TEXT PRIMARY KEY, profile_id TEXT NOT NULL
    REFERENCES provider_profiles(id) ON DELETE CASCADE,
  updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS academic_terms (
  id TEXT PRIMARY KEY, name TEXT NOT NULL, starts_on TEXT NOT NULL, ends_on TEXT NOT NULL,
  timezone TEXT NOT NULL DEFAULT 'Asia/Shanghai', current INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS schedule_connections (
  id TEXT PRIMARY KEY, connector TEXT NOT NULL UNIQUE, display_name TEXT NOT NULL,
  state TEXT NOT NULL DEFAULT 'disconnected', session_ciphertext TEXT,
  sync_interval_minutes INTEGER NOT NULL DEFAULT 360, last_synced_at TEXT,
  last_error TEXT, reauth_required INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS schedule_rules (
  id TEXT PRIMARY KEY, term_id TEXT NOT NULL REFERENCES academic_terms(id) ON DELETE CASCADE,
  course_id TEXT REFERENCES courses(id) ON DELETE SET NULL,
  course_name TEXT NOT NULL, course_code TEXT, class_name TEXT, teacher TEXT,
  campus TEXT, building TEXT, room TEXT, weekday INTEGER NOT NULL,
  start_period INTEGER NOT NULL, end_period INTEGER NOT NULL,
  weeks_json TEXT NOT NULL, odd_even TEXT NOT NULL DEFAULT 'all', notes TEXT NOT NULL DEFAULT '',
  external_id TEXT NOT NULL, aliases_json TEXT NOT NULL DEFAULT '[]',
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
  UNIQUE(term_id, external_id)
);
CREATE TABLE IF NOT EXISTS schedule_occurrences (
  id TEXT PRIMARY KEY, rule_id TEXT NOT NULL REFERENCES schedule_rules(id) ON DELETE CASCADE,
  course_id TEXT REFERENCES courses(id) ON DELETE SET NULL,
  occurrence_date TEXT NOT NULL, starts_at TEXT NOT NULL, ends_at TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'scheduled', source_kind TEXT NOT NULL DEFAULT 'regular',
  campus TEXT, building TEXT, room TEXT, teacher TEXT, notes TEXT NOT NULL DEFAULT '',
  external_id TEXT NOT NULL, adjustment_of_id TEXT REFERENCES schedule_occurrences(id) ON DELETE SET NULL,
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
  UNIQUE(rule_id, occurrence_date, starts_at, ends_at)
);
CREATE TABLE IF NOT EXISTS session_automation (
  session_id TEXT PRIMARY KEY REFERENCES sessions(id) ON DELETE CASCADE,
  occurrence_id TEXT UNIQUE REFERENCES schedule_occurrences(id) ON DELETE SET NULL,
  materialization_reason TEXT, title_locked INTEGER NOT NULL DEFAULT 0,
  title_source TEXT NOT NULL DEFAULT 'course_name', title_confidence REAL NOT NULL DEFAULT 0,
  topic_candidate TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS resource_automation (
  resource_id TEXT PRIMARY KEY REFERENCES resources(id) ON DELETE CASCADE,
  transcription_state TEXT NOT NULL DEFAULT 'saved', auto_transcribe INTEGER NOT NULL DEFAULT 1,
  failure_reason TEXT, last_job_id TEXT REFERENCES jobs(id) ON DELETE SET NULL,
  original_created_at TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS transcription_chunks (
  id TEXT PRIMARY KEY, resource_id TEXT NOT NULL REFERENCES resources(id) ON DELETE CASCADE,
  position INTEGER NOT NULL, start_seconds REAL NOT NULL, end_seconds REAL NOT NULL,
  media_path TEXT, status TEXT NOT NULL DEFAULT 'pending', attempt_count INTEGER NOT NULL DEFAULT 0,
  segment_count INTEGER NOT NULL DEFAULT 0, segments_json TEXT NOT NULL DEFAULT '[]',
  error TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
  UNIQUE(resource_id, position)
);
CREATE TABLE IF NOT EXISTS inbox_items (
  id TEXT PRIMARY KEY, name TEXT NOT NULL, mime_type TEXT, type TEXT NOT NULL,
  storage_provider TEXT NOT NULL, storage_key TEXT NOT NULL, local_path TEXT,
  captured_at TEXT NOT NULL, original_file_time TEXT, extracted_text TEXT NOT NULL DEFAULT '',
  source TEXT NOT NULL DEFAULT 'global_upload', matching_status TEXT NOT NULL DEFAULT 'pending',
  match_confidence REAL NOT NULL DEFAULT 0, match_reasons_json TEXT NOT NULL DEFAULT '[]',
  suggested_session_id TEXT REFERENCES sessions(id) ON DELETE SET NULL,
  adopted_resource_id TEXT REFERENCES resources(id) ON DELETE SET NULL,
  locked INTEGER NOT NULL DEFAULT 0, archived INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS review_items (
  id TEXT PRIMARY KEY, kind TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'pending',
  subject_type TEXT NOT NULL, subject_id TEXT NOT NULL, title TEXT NOT NULL,
  proposed_value TEXT, confidence REAL NOT NULL DEFAULT 0, reasons_json TEXT NOT NULL DEFAULT '[]',
  navigation_path TEXT, decision_reason TEXT, decided_at TEXT,
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS provider_call_logs (
  id TEXT PRIMARY KEY, operation TEXT NOT NULL,
  provider_profile_id TEXT REFERENCES provider_profiles(id) ON DELETE SET NULL,
  provider_name TEXT NOT NULL, model TEXT, job_id TEXT REFERENCES jobs(id) ON DELETE SET NULL,
  resource_id TEXT REFERENCES resources(id) ON DELETE SET NULL,
  session_id TEXT REFERENCES sessions(id) ON DELETE SET NULL,
  status TEXT NOT NULL, duration_ms INTEGER NOT NULL DEFAULT 0, request_count INTEGER NOT NULL DEFAULT 1,
  audio_minutes REAL, input_tokens INTEGER, output_tokens INTEGER,
  estimated_cost REAL, cost_currency TEXT, cost_known INTEGER NOT NULL DEFAULT 0,
  error_type TEXT, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS audit_log (
  id TEXT PRIMARY KEY, action TEXT NOT NULL, subject_type TEXT NOT NULL, subject_id TEXT NOT NULL,
  payload_json TEXT NOT NULL DEFAULT '{}', created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_occurrences_date ON schedule_occurrences(occurrence_date, starts_at);
CREATE INDEX IF NOT EXISTS idx_review_pending ON review_items(status, created_at);
CREATE INDEX IF NOT EXISTS idx_inbox_matching ON inbox_items(matching_status, captured_at);
CREATE INDEX IF NOT EXISTS idx_provider_calls_created ON provider_call_logs(created_at, provider_profile_id);
CREATE UNIQUE INDEX IF NOT EXISTS idx_one_active_transcription_per_resource
  ON jobs(resource_id) WHERE kind='transcription' AND status IN ('queued', 'running');
"""
