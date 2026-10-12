export const ENGINE_URL = "http://127.0.0.1:8765";

export type UpdateCheck = {
  enabled: boolean;
  update_available: boolean;
  current_version: string;
  latest_version: string | null;
  channel: string | null;
  reason: string;
  checked_at?: string;
};

export type SystemOverview = {
  current_version: string;
  update_state: {
    current_version?: string;
    staged_version?: string | null;
    previous_version?: string | null;
    last_error?: string | null;
    last_success_at?: string | null;
  };
  last_check: UpdateCheck | null;
  logs: {
    directory: string;
    rotation: string;
    job_retention_days: number;
    job_log_count: number;
    files: Array<{ name: string; present: boolean; bytes: number; modified_at: string | null }>;
  };
};

export type SafeDiagnosticReport = {
  schema_version: number;
  generated_at: string;
  app_version: string;
  update: { current: string; previous: string | null; staged: string | null;
    last_success_at: string | null; last_failure_at: string | null; has_last_error: boolean };
  logs: { job_retention_days: number; job_log_count: number;
    files: Array<{ name: string; present: boolean; bytes: number }> };
  recent_event_sample: { count: number; window_hours: number; max_scanned: number;
    error_or_failure_count: number; error_level_count: number };
  privacy: string;
};

export type SafeLogEntry = {
  timestamp?: string;
  level?: string;
  event?: string;
  route?: string;
  stage?: string;
  job_id?: string;
  duration_ms?: number;
};

export type Screen = "artwork" | "chat" | "dataset" | "provider" | "system";
export type UIEvent =
  | "tab_opened" | "references_selected" | "job_submitted" | "job_retried"
  | "chat_session_created" | "chat_edit_submitted" | "chat_print_check"
  | "image_ai_config_saved" | "provider_config_saved"
  | "dataset_preflight" | "dataset_import" | "update_restarted";

export function audit(event: UIEvent, screen: Screen, jobId?: string): void {
  const payload = { event, screen, ...(jobId && /^[a-f0-9]{32}$/.test(jobId) ? { job_id: jobId } : {}) };
  void fetch(`${ENGINE_URL}/system/events`, {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload), signal: AbortSignal.timeout(4000),
  }).catch(() => { /* Never let telemetry interrupt the user workflow. */ });
}

export async function systemRequest<T>(route: string, method = "GET"): Promise<T> {
  const response = await fetch(`${ENGINE_URL}${route}`, {
    method, signal: AbortSignal.timeout(method === "GET" ? 12000 : route.includes("stage-update") ? 180000 : 15000),
  });
  if (!response.ok) {
    const payload = await response.json().catch(() => ({}));
    throw new Error(typeof payload.detail === "string" ? payload.detail : `HTTP ${response.status}`);
  }
  return await response.json() as T;
}
