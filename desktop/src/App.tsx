import { ChangeEvent, useEffect, useMemo, useState } from "react";
import DatasetOnboarding from "./DatasetOnboarding";

type QualityMode = "quick_2d" | "print_ready" | "max_fidelity";

type Job = {
  job_id: string;
  quality_mode: QualityMode;
  state: string;
  progress: number;
  stage_message: string;
  source_paths: string[];
  result_path?: string | null;
  failure_reason?: string | null;
};

type EngineStatus = {
  job_concurrency?: number;
  remote_provider_configured?: boolean;
  remote_provider_available?: boolean;
  remote_provider_state?: {
    failure_count?: number;
    cooldown_remaining_seconds?: number;
  };
  performance?: Record<
    string,
    {
      count: number;
      p50_ms: number;
      p95_ms: number;
      max_ms: number;
      cache_hits: number;
      cache_hit_rate: number;
    }
  >;
};

const ENGINE_URL = "http://127.0.0.1:8765";

const TIMELINE = [
  { key: "checking", label: "Checking" },
  { key: "analyzing", label: "Analyzing" },
  { key: "reconstructing", label: "Reconstructing" },
  { key: "quality", label: "Quality Check" },
  { key: "done", label: "Done" }
] as const;

function timelineIndex(state?: string) {
  if (!state || state === "queued" || state === "preflight" || state === "resuming" || state === "waiting_compute") return 0;
  if (["analyzing", "waiting_provider"].includes(state)) return 1;
  if (["reconstructing", "region_rescue", "precision_finishing"].includes(state)) return 2;
  if (state === "qc") return 3;
  if (["completed", "review_required", "failed_final", "cancelled", "blocked_budget"].includes(state)) return 4;
  return 0;
}

export default function App() {
  const [engineOnline, setEngineOnline] = useState(false);
  const [engineStatus, setEngineStatus] = useState<EngineStatus | null>(null);
  const [files, setFiles] = useState<File[]>([]);
  const [mode, setMode] = useState<QualityMode>("print_ready");
  const [advanced, setAdvanced] = useState(false);
  const [activeTab, setActiveTab] = useState<"artwork" | "dataset">("artwork");
  const [job, setJob] = useState<Job | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const progress = useMemo(() => Math.round((job?.progress ?? 0) * 100), [job]);
  const previews = useMemo(
    () => files.map((file) => ({ file, url: URL.createObjectURL(file) })),
    [files]
  );
  const jobActive = Boolean(
    job && !["completed", "review_required", "failed_final", "cancelled", "blocked_budget"].includes(job.state)
  );
  const activeTimelineIndex = timelineIndex(job?.state);

  useEffect(() => {
    return () => {
      previews.forEach(({ url }) => URL.revokeObjectURL(url));
    };
  }, [previews]);

  useEffect(() => {
    let cancelled = false;
    const ping = async () => {
      try {
        const response = await fetch(`${ENGINE_URL}/status`);
        if (!response.ok) throw new Error("Engine status unavailable");
        const payload = (await response.json()) as EngineStatus;
        if (!cancelled) {
          setEngineOnline(true);
          setEngineStatus(payload);
        }
      } catch {
        if (!cancelled) {
          setEngineOnline(false);
          setEngineStatus(null);
        }
      }
    };
    ping();
    const timer = window.setInterval(ping, 3000);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, []);

  useEffect(() => {
    if (!job || ["completed", "failed_final", "cancelled", "review_required", "blocked_budget"].includes(job.state)) {
      return;
    }
    const timer = window.setInterval(async () => {
      try {
        const response = await fetch(`${ENGINE_URL}/jobs/${job.job_id}`);
        if (response.ok) setJob(await response.json());
      } catch {
        // Engine badge handles transient disconnects.
      }
    }, 750);
    return () => window.clearInterval(timer);
  }, [job?.job_id, job?.state]);

  const chooseFiles = (event: ChangeEvent<HTMLInputElement>) => {
    setFiles(Array.from(event.target.files ?? []));
    setJob(null);
    setError("");
    event.target.value = "";
  };

  const removeFile = (index: number) => {
    setFiles((current) => current.filter((_, fileIndex) => fileIndex !== index));
    setJob(null);
    setError("");
  };

  const createJob = async () => {
    if (!files.length || !engineOnline) return;
    setBusy(true);
    setError("");
    try {
      const body = new FormData();
      files.forEach((file) => body.append("files", file));
      const response = await fetch(`${ENGINE_URL}/jobs?quality_mode=${mode}`, {
        method: "POST",
        body
      });
      if (!response.ok) throw new Error(await response.text());
      setJob(await response.json());
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to create job");
    } finally {
      setBusy(false);
    }
  };

  const providerLabel = !engineStatus?.remote_provider_configured
    ? "Local only"
    : engineStatus.remote_provider_available
      ? "AI rescue ready"
      : "AI rescue cooling down";

  return (
    <main className="app-shell">
      <header className="topbar">
        <div>
          <div className="eyebrow">POD AI PRINT</div>
          <h1>Artwork Reconstruction</h1>
          <p className="subtitle">Drop references, press Run, get a print-ready artwork.</p>
        </div>
        <div className="engine-summary">
          <div className={`status ${engineOnline ? "online" : "offline"}`}>
            <span />
            {engineOnline ? "Engine ready" : "Engine offline"}
          </div>
          {engineOnline && (
            <div className="runtime-note">
              {providerLabel} · {engineStatus?.job_concurrency ?? 1} job{(engineStatus?.job_concurrency ?? 1) > 1 ? "s" : ""} parallel
            </div>
          )}
        </div>
      </header>

      <nav className="primary-tabs" aria-label="Tool sections">
        <button
          className={activeTab === "artwork" ? "selected" : ""}
          onClick={() => setActiveTab("artwork")}
        >Artwork</button>
        <button
          className={activeTab === "dataset" ? "selected" : ""}
          onClick={() => setActiveTab("dataset")}
        >Historical Dataset</button>
      </nav>

      {activeTab === "artwork" ? (
      <>
      <section className="workspace">
        <div className={`upload-card ${files.length ? "has-files" : ""}`}>
          {files.length === 0 ? (
            <>
              <div className="upload-icon" aria-hidden="true">+</div>
              <h2>Choose product references</h2>
              <p>One image or several views of the same artwork.</p>
              <label className="primary-button">
                Select images
                <input type="file" accept="image/*" multiple onChange={chooseFiles} hidden />
              </label>
            </>
          ) : (
            <>
              <div className="upload-preview-header">
                <div>
                  <div className="eyebrow">REFERENCES</div>
                  <h2>{files.length} image{files.length > 1 ? "s" : ""} ready</h2>
                </div>
                <label className="secondary-button">
                  Change images
                  <input type="file" accept="image/*" multiple onChange={chooseFiles} hidden />
                </label>
              </div>
              <div className="reference-grid">
                {previews.map(({ file, url }, index) => (
                  <div className="reference-card" key={`${file.name}-${file.lastModified}-${index}`}>
                    <img src={url} alt={file.name} title={file.name} />
                    <button
                      className="reference-remove"
                      type="button"
                      aria-label={`Remove ${file.name}`}
                      title="Remove image"
                      onClick={() => removeFile(index)}
                      disabled={jobActive || busy}
                    >
                      ×
                    </button>
                    <div className="reference-name" title={file.name}>{file.name}</div>
                  </div>
                ))}
              </div>
              <div className="file-summary">
                These images will be processed together as one artwork reference set.
              </div>
            </>
          )}
        </div>

        <div className="run-card">
          <div className="auto-row">
            <div>
              <div className="eyebrow">PROCESSING</div>
              <h3>Auto Mode</h3>
              <p>Local-first. AI is used only when the quality gates require rescue.</p>
            </div>
            <span className="auto-badge">Recommended</span>
          </div>

          <button className="advanced-toggle" onClick={() => setAdvanced((value) => !value)}>
            {advanced ? "Hide advanced" : "Advanced"}
          </button>

          {advanced && (
            <div className="mode-grid">
              {[
                ["quick_2d", "Quick 2D", "Fast draft; policy may use remote analysis sooner."],
                ["print_ready", "Print Ready", "Balanced local-first default."],
                ["max_fidelity", "Max Fidelity", "Strictest analysis and rescue path."]
              ].map(([value, title, description]) => (
                <button
                  key={value}
                  className={`mode-option ${mode === value ? "selected" : ""}`}
                  onClick={() => setMode(value as QualityMode)}
                >
                  <strong>{title}</strong>
                  <span>{description}</span>
                </button>
              ))}
            </div>
          )}

          <button
            className="run-button"
            disabled={!files.length || !engineOnline || busy || jobActive}
            onClick={createJob}
          >
            {busy ? "Starting…" : jobActive ? "Processing…" : "Run"}
          </button>
          {error && <div className="error">{error}</div>}
        </div>
      </section>

      <section className="result-card">
        <div className="result-header">
          <div>
            <div className="eyebrow">CURRENT JOB</div>
            <h3>{job ? job.job_id.slice(0, 12) : "Ready for a job"}</h3>
          </div>
          <strong>{progress}%</strong>
        </div>

        <div className="timeline">
          {TIMELINE.map((stage, index) => {
            const state = index < activeTimelineIndex ? "complete" : index === activeTimelineIndex && job ? "active" : "pending";
            return (
              <div className={`timeline-step ${state}`} key={stage.key}>
                <span className="timeline-dot">{state === "complete" ? "✓" : index + 1}</span>
                <span>{stage.label}</span>
              </div>
            );
          })}
        </div>

        <div className="progress-track">
          <div className="progress-value" style={{ width: `${progress}%` }} />
        </div>

        <div className="stage-line">
          <strong>{job?.stage_message ?? "Select references and press Run."}</strong>
          {job && <span>{job.state.replaceAll("_", " ")}</span>}
        </div>

        {job?.failure_reason && (
          <div className={job.state === "review_required" ? "review-note" : "error-note"}>
            {job.state === "review_required" ? "Review needed: " : "Stopped: "}
            {job.failure_reason}
          </div>
        )}

        {advanced && engineStatus?.performance && (
          <div className="performance-panel">
            <div className="eyebrow">RUNTIME DETAILS</div>
            <div className="performance-grid">
              {Object.entries(engineStatus.performance)
                .slice(0, 6)
                .map(([stage, metric]) => (
                  <div key={stage}>
                    <strong>{stage.replaceAll("_", " ")}</strong>
                    <span>p50 {metric.p50_ms} ms · p95 {metric.p95_ms} ms</span>
                    <span>{Math.round(metric.cache_hit_rate * 100)}% cache hit</span>
                  </div>
                ))}
            </div>
          </div>
        )}

        {job?.result_path && (
          <div className="output-preview">
            <img
              src={`${ENGINE_URL}/jobs/${job.job_id}/output`}
              alt="Reconstructed artwork"
            />
            <a
              className="primary-button"
              href={`${ENGINE_URL}/jobs/${job.job_id}/output`}
              download={`pod-artwork-${job.job_id.slice(0, 12)}.png`}
            >
              Export PNG
            </a>
          </div>
        )}
      </section>
      </>
      ) : (
        <DatasetOnboarding engineOnline={engineOnline} />
      )}
    </main>
  );
}
