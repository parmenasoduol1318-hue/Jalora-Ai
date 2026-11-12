export interface User {
  id: string;
  email: string;
  name?: string;
  prefs?: Record<string, unknown>;
}

export interface Conversation {
  id: string;
  title: string;
  project?: string | null;
  created_at?: string;
  updated_at?: string;
  archived?: boolean;
}

export interface SourceRef {
  url: string;
  title?: string;
  snippet?: string;
}

export interface ChatMessage {
  id?: string;
  role: "user" | "assistant" | "tool";
  content: string;
  tool_calls?: unknown[];
  created_at?: string;
}

export interface CitationRef {
  index: number;
  title: string;
  url: string;
  snippet?: string;
}

export interface DiagramRef {
  id: string;
  kind: "svg" | "canvas" | "image" | string;
  title?: string;
  content: string;
}

export interface PlanStep {
  description: string;
  status: "pending" | "active" | "done" | "failed" | "skipped" | string;
  tool?: string | null;
  outcome_status?: string | null;
  verification_status?: string | null;
  dependency_step_ids?: string[];
  dependencies?: number[];
}

export interface TaskStep {
  id: string;
  plan_step_id?: string | null;
  task_id: string;
  idx: number;
  description: string;
  status: string;
  detail?: string | null;
  dependencies: number[];
  dependency_step_ids: string[];
  tool_id?: string | null;
  capability?: string | null;
  tool_version?: string | null;
  outcome_status?: string | null;
  verification_status?: string | null;
  error_code?: string | null;
  approval_id?: string | null;
  approval_status?: string | null;
  safe_summary?: string | null;
  evidence: Array<Record<string, unknown>>;
  updated_at?: string | null;
}

export interface TaskApproval {
  id: string;
  run_id: string;
  task_step_id?: string | null;
  plan_step_id?: string | null;
  tool_name: string;
  arguments: Record<string, unknown>;
  level: number;
  kind: string;
  status: string;
  can_approve: boolean;
  can_edit: boolean;
  decision_status: string;
  claim_status: string;
  payload_version: number;
  created_at?: string | null;
  expires_at?: string | null;
}

export interface OutreachQueueEntry {
  position: number;
  plan_step_id: string;
  organization: string;
  recipient: string;
  status: string;
  research_status: string;
  approval_status: string;
  sending_status: string;
  verified_at?: string;
  source_url: string;
  source_title: string;
  source_evidence?: string;
  candidate_addresses?: string[];
  address_found_on_source: boolean;
  organization_match_on_source: boolean;
  error?: string;
  approval_id?: string | null;
  draft?: {
    to?: string | null;
    cc?: string | null;
    bcc?: string | null;
    subject?: string | null;
    body?: string | null;
    regards?: string | null;
    attachments?: Array<Record<string, unknown>> | null;
  } | null;
  draft_preview?: OutreachQueueEntry["draft"];
  provider?: string;
  provider_message_id?: string;
  provider_thread_id?: string;
  receipt_status?: string;
}

export interface Task {
  id: string;
  conversation_id?: string | null;
  run_id?: string | null;
  goal: string;
  status: string;
  resumable?: boolean;
  plan?: PlanStep[] | null;
  steps?: TaskStep[];
  approvals?: TaskApproval[];
  result?: string | null;
  outreach_queue?: OutreachQueueEntry[] | null;
  current_step: number;
  progress: number;
  error?: string | null;
  created_at: string;
}

export interface MemoryItem {
  id: string;
  content: string;
  kind: string;
  source: string;
  created_at: string;
}

export interface DocItem {
  id: string;
  filename: string;
  status: string;
  size_bytes: number;
  page_count?: number;
  progress?: number;
  mime_type?: string;
  error?: string | null;
}

export interface MediaItem {
  id: string;
  filename: string;
  mime_type: string;
  kind: "image" | "audio" | "video";
  size_bytes: number;
  url: string;
  created_at?: string;
}

export interface ActivityEntry {
  id?: string;
  tool?: string;
  agent?: string;
  success?: boolean;
  approval_status?: string | null;
  result_summary?: string;
  duration_ms?: number;
  created_at: string;
  meta?: Record<string, unknown>;
}

export interface ToolManifestEntry {
  name: string;
  category: string;
  permission: number;
  description: string;
  requires_internet: boolean;
}

/* ---- Phase H (§46-48): voice interface ----------------------------------- */
export interface VoiceSettings {
  enabled: boolean;
  auto_speak: boolean;
  continuous: boolean;
  voice: string;
}

export interface TranscribeResult {
  transcript: string;
  provider: string;
}

export interface VoiceConfig {
  stt: boolean;
  tts: string;
  provider: string;
  max_clip_bytes: number;
  /* Phase 1: live in-app call pipeline (server-streaming STT + TTS). */
  live?: boolean;
  stt_provider?: string;
  tts_provider?: string;
  sample_rate?: number;
  tts_sample_rate?: number;
  max_seconds?: number;
}

export interface AppSettings {
  autonomy: number;
  tool_policies: Record<string, string>;
  notifications_enabled: boolean;
  notification_sound: boolean;
  schedule_alerts: boolean;
  approval_alerts: boolean;
  memory_enabled: boolean;
  locale: string;
  voice: VoiceSettings;
  tools: ToolManifestEntry[];
  // Phase K (§27, §70-72): learned/explicit preferences surfaced to the UI.
  response_length?: "balanced" | "concise" | "detailed";
  tone?: "professional" | "friendly" | "casual" | "formal";
  timezone?: string;
  preferred_provider?: "auto" | "openai" | "anthropic" | "groq" | "google" | "deepseek" | "mistral" | "openrouter" | "lmstudio" | "jan" | "cohere" | "cerebras" | "perplexity" | "poolside" | "nvidia" | "ollama";
  // AI providers the user has disconnected for their account.
  disabled_providers?: string[];
  // Names of providers for which the user has stored a per-user API key.
  provider_keys?: string[];
  // LM Studio local provider configuration.
  lmstudio_base_url?: string;
  lmstudio_model?: string;
  local_provider_preference?: "lmstudio" | "jan" | "ollama";
  // Appearance & resources (spec §settings-audit).
  theme?: "dark" | "light";
  ram_safety_floor_gb?: number;
  privacy_data_export?: boolean;
  memory_persist?: boolean;
}

export interface ModelInfo {
  id: string;
  name: string;
  size?: number | null;
  format?: string | null;
  modified_at?: string | null;
}


export interface Artifact {
  url: string;
  kind: "screenshot" | "plot" | "plot-vector" | "download" | string;
  mime?: string;
  title?: string;
  size_bytes?: number;
  file_name?: string;
  download_url?: string;
}

export interface CredentialInfo {
  domain?: string;
}

export interface BrowserStatus {
  url?: string;
  title?: string;
  can_back?: boolean;
  can_forward?: boolean;
  session_open?: boolean;
}

export interface ApprovalInfo {
  approval_id?: string;
  id?: string;
  run_id?: string;
  tool_name: string;
  arguments: Record<string, unknown>;
  level: number | string;
  kind?: string;
  credential?: CredentialInfo | null;
  field?: string;
  standalone?: boolean;
  status?: string;
}

export interface ChatResponse {
  conversation_id: string;
  run_id: string;
  task_id?: string;
  reply: string;
  sources: SourceRef[];
  model: string;
  status: string;
  approval?: ApprovalInfo | null;
  intent?: string;
  plan?: unknown;
  artifacts?: Artifact[];
  citations?: CitationRef[];
  diagrams?: DiagramRef[];
}

export type ViewName =
  | "chat" | "dashboard" | "recents" | "tasks" | "memory" | "documents" | "settings"
  | "integrations" | "devices" | "email" | "schedules" | "workspace" | "calendar" | "history" | "creative" | "people";

export interface ProfessionalProfile {
  name: string;
  email: string;
  courses: string[];
  qualifications: string[];
  skills: string[];
  phone: string;
  discoverable: boolean;
  share_email: boolean;
  share_phone: boolean;
}

export interface MemberProfileResult {
  id: string;
  name: string;
  courses: string[];
  qualifications: string[];
  skills: string[];
  email: string;
  phone: string;
}

export interface PublicProfileResult {
  title: string;
  url: string;
  snippet: string;
  source: string;
}

/* ---- Phase E (§34-38, §76): integrations & email ------------------------ */
export interface IntegrationItem {
  id: string;
  provider: string;
  status: string;
  scopes: string[];
  config: Record<string, unknown>;
  error?: string | null;
  created_at?: string;
  updated_at?: string;
}

/* ---- Phase E extra (§custom): user-registered OAuth2 integrations -------- */
export interface CustomIntegrationOut {
  id: string;
  provider: string;
  name: string;
  status: string;
  scopes: string[];
  error?: string | null;
}

/* ---- Phase I (§63-68, §76): integration registry provider cards ---------- */
export interface ProviderCard {
  name: string;
  area: string;
  kind: string;
  provider: string;
  configured: boolean;
  connected: boolean;
  keyless: boolean;
  connectable: boolean;
  description: string;
  logo?: string;
}

export interface EmailMessageT {
  id: string;
  thread_id: string;
  from_addr: string;
  to_addr: string;
  subject: string;
  snippet: string;
  body: string;
  received_at?: string | null;
  read: boolean;
  attachments: unknown[];
}

export interface DocumentT {
  id: string;
  filename: string;
  mime_type: string;
  size_bytes: number;
  status: string;
  error?: string | null;
}

export interface EmailThreadT {
  thread_id: string;
  subject: string;
  snippet: string;
  from_addr: string;
  to_addr: string;
  message_count: number;
  last_received_at?: string | null;
}

export interface EmailDraftT {
  status: string;
  draft_id: string;
  to: string;
  subject: string;
  body: string;
  provider: string;
  attachments?: Array<Record<string, unknown>>;
}

export interface EmailReceiptT {
  status: string;
  provider: string;
  message_id: string;
  to: string;
  subject: string;
  thread_id: string | null;
  mock: boolean;
  attachments?: Array<Record<string, unknown>>;
}

export interface EmailSendResultT {
  status: string;
  sent: EmailReceiptT | null;
  approval: {
    id: string;
    tool_name: string;
    arguments: Record<string, unknown>;
    level: number;
    status: string;
    created_at?: string;
  } | null;
}

export interface AutoReplyRuleT {
  id: string;
  name: string;
  enabled: boolean;
  sender_pattern: string;
  topic_pattern: string;
  time_window_start: number;
  time_window_end: number;
  tone: string;
  template: string;
  max_per_hour: number;
  max_per_day: number;
  requires_approval: boolean;
  priority: number;
  created_at?: string;
  updated_at?: string;
}

/* ---- Phase F (§31-33, §57): phone calls ---------------------------------- */
export interface CallTurnT {
  role: "agent" | "person" | "user" | "tool" | string;
  kind: "say" | "speech" | "dtmf" | "system" | "ask" | string;
  text: string;
  ts: number;
}

export interface Call {
  id: string;
  user_id: string;
  run_id?: string | null;
  task_id?: string | null;
  conversation_id?: string | null;
  to_phone: string | null;
  from_phone?: string | null;
  provider: string;
  kind?: string;
  mock: boolean;
  purpose?: string;
  status: "dialing" | "in_progress" | "completed" | "failed" | "cancelled" | "no_answer" | string;
  answers: Record<string, string>;
  transcript: CallTurnT[];
  summary?: string | null;
  max_turns: number;
  max_duration_seconds: number;
  created_at?: string | null;
  started_at?: string | null;
  ended_at?: string | null;
}

/* Phase 3 (§31-33): call result emitted as an SSE event after make_call. */
export interface CallResult {
  call_id?: string;
  run_id?: string;
  to_phone?: string;
  from_phone?: string;
  purpose?: string;
  status?: string;
  duration_seconds?: number | null;
  summary?: string;
  transcript?: string;
}

/* ---- Phase G (§49-51, §61-62): schedules & notifications ---- */
export interface ScheduleT {
  id: string;
  kind: string; // once | cron | interval
  spec: Record<string, unknown>;
  schedule_expression: string;
  timezone: string;
  task_template: string;
  notify_only: boolean;
  enabled: boolean;
  next_run?: string | null;
  last_run?: string | null;
  created_at?: string | null;
  latest_occurrence?: {
    id: string;
    status: string;
    scheduled_for: string;
    result_summary?: string | null;
    task_id?: string | null;
    run_id?: string | null;
  } | null;
}

export interface NotificationT {
  id: string;
  text: string;
  kind: string;
  link?: string | null;
  run_id?: string | null;
  task_id?: string | null;
  schedule_id?: string | null;
  occurrence_id?: string | null;
  read: boolean;
  created_at?: string | null;
}

/* ---- Phase J (§53-54): coding agent workspace -------------------------- */
export interface WorkspaceFileEntry {
  path: string;
  size: number;
  ext: string;
}

export interface WorkspaceTree {
  files: WorkspaceFileEntry[];
}

export interface WorkspaceFileContent {
  path: string;
  content: string;
  size: number;
  truncated: boolean;
}

export interface WorkspaceDiffEntry {
  path: string;
  created: boolean;
  diff: string;
}

export interface WorkspaceDiff {
  edits: WorkspaceDiffEntry[];
}

export interface WorkspaceRollbackResult {
  restored: string[];
}

export interface WorkspaceActionResponse {
  status?: string;
  task_id?: string;
  run_id?: string;
  approval?: ApprovalInfo | null;
}

export interface WorkspaceWriteResult {
  path: string;
  size: number;
  created: boolean;
}

export interface WorkspaceUploadError {
  filename: string;
  detail: string;
}

export interface WorkspaceUploadOut {
  results: WorkspaceWriteResult[];
  rejected: WorkspaceUploadError[];
}

/* A workspace *project* is a Conversation with project === "workspace". */
export interface WorkspaceSession {
  id: string;
  title: string;
  project?: string | null;
  created_at?: string;
  updated_at?: string;
}

export interface WorkspaceRenameResult {
  path: string;
  from_path: string;
  kind: string;
}

export interface WorkspaceDeleteResult {
  path: string;
  kind: string;
}

export interface WorkspaceExecOut {
  command: string;
  exit_code: number | null;
  ok: boolean;
  timed_out: boolean;
  stdout: string;
  stderr: string;
  truncated: boolean;
  duration_ms: number;
}

export interface WorkspaceTerminalEntry {
  command: string;
  output: string;
  ok: boolean;
  source?: "user" | "agent";
}

export interface RunTestResult {
  kind: string;
  ok: boolean;
  exit_code: number | null;
  stdout: string;
  stderr: string;
  timed_out: boolean;
}

/* ---- Phase K (§27, §70-72): learning & preferences ---------------------- */
export interface PreferencePatch {
  response_length?: "balanced" | "concise" | "detailed";
  tone?: "professional" | "friendly" | "casual" | "formal";
  timezone?: string;
  preferred_provider?: "auto" | "openai" | "anthropic" | "groq" | "google" | "deepseek" | "mistral" | "openrouter" | "lmstudio" | "jan" | "cohere" | "cerebras" | "perplexity" | "poolside" | "nvidia" | "ollama";
  disabled_providers?: string[];
}

export interface SettingsOut {
  autonomy: number;
  tool_policies: Record<string, string>;
  memory_enabled: boolean;
  memory_retention_days: number | null;
  locale: string;
  voice: VoiceSettings;
  tools: ToolManifestEntry[];
  response_length: string;
  tone: string;
  timezone: string;
  preferred_provider: string;
  disabled_providers?: string[];
  provider_keys?: string[];
}

export interface FeedbackEntry {
  id: string;
  task_id?: string | null;
  sentiment: string;
  content: string;
  created_at?: string;
}

export interface ActivityEntry {
  id?: string;
  tool?: string;
  agent?: string;
  success?: boolean;
  approval_status?: string | null;
  result_summary?: string;
  duration_ms?: number;
  created_at: string;
  meta?: Record<string, unknown>;
  kind?: "tool" | "feedback" | "preference" | string;
  content?: string;
}

/* ---- System health & Ollama memory (right-sidebar / dashboard) ------------ */
export interface ProviderStatus {
  model: string;
  configured: boolean;
  keyless: boolean;
  disabled: boolean;
  usable: boolean;
  logo?: string;
}

export interface CalendarAttendee {
  name?: string;
  email?: string;
  phone?: string;
}

export interface CalendarEventT {
  id: string;
  title: string;
  start: string | null;
  end?: string | null;
  timezone?: string | null;
  location?: string | null;
  description?: string | null;
  attendees?: CalendarAttendee[];
  provider?: string | null;
  status?: string | null;
  external_id?: string | null;
}

export interface HealthResponse {
  status: string;
  version: string;
  env: string;
  database: string;
  providers: Record<string, ProviderStatus>;
  warnings: string[];
  timestamp: string;
  capabilities: Record<string, unknown>;
}

export interface OllamaRouteEvent {
  model: string;
  tier: string;
  tier_requirement_gb: number;
  reason: string;      // "memory-ok" | "low-memory" | "low-memory-forced" | "single-model" | "override"
  free_memory_gb: number;
  keep_alive_s: number;
}

export interface OllamaMemorySnapshot {
  enabled: boolean;
  healthy: boolean;
  last_check: number;
  free_gb: number;
  heavy_model: string;
  mid_model: string;
  light_model: string;
  survival_model: string;
  loaded: Record<string, number>;
  installed: string[];
  last_unloaded: string[];
  events: string[];
}

export interface UserPlan {
  tier: string;
  label: string;
}

export type ThemeName = "dark" | "light";
