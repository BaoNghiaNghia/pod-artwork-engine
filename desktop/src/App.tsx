import { ChangeEvent, useEffect, useMemo, useRef, useState } from "react";
import DatasetOnboarding from "./DatasetOnboarding";
import AISettings from "./AISettings";
import AIChat from "./AIChat";
import AutoEnhance from "./AutoEnhance";
import { audit, systemRequest, type UpdateCheck } from "./system";
import { getVersion } from "@tauri-apps/api/app";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Progress } from "@/components/ui/progress";
import { Layers3, Images, MessageSquareText, Database, SlidersHorizontal, ImagePlus, Play, Sparkles, CheckCircle2, ChevronRight, Cpu } from "lucide-react";

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
  version?: string;
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
const MAX_UPLOAD_BYTES = 50 * 1024 * 1024;

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
  const [appVersion, setAppVersion] = useState("");
  const [engineIssue, setEngineIssue] = useState("");
  const [engineStatus, setEngineStatus] = useState<EngineStatus | null>(null);
  const [files, setFiles] = useState<File[]>([]);
  const [mode, setMode] = useState<QualityMode>("print_ready");
  const [advanced, setAdvanced] = useState(false);
  const [activeTab, setActiveTab] = useState<"artwork" | "chat" | "dataset" | "provider">("artwork");
  const [job, setJob] = useState<Job | null>(null);
  const [busy, setBusy] = useState(false);
  const [providerRefresh, setProviderRefresh] = useState(0);
  const [updateCheck, setUpdateCheck] = useState<UpdateCheck | null>(null);
  const updateCheckInFlight = useRef(false);
  const [candidateKind, setCandidateKind] = useState<"local_draft" | "ai_candidate" | null>(null);
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
  const mockupNotIsolated = Boolean(job?.failure_reason && (
    job.failure_reason.includes("semantic_provider_not_used")
    || job.failure_reason.includes("artwork_not_isolated_from_product_mockup")
  ));
  const canExport2D = Boolean(job?.state === "completed" && job?.result_path && !mockupNotIsolated);
  const hasAICandidate = job?.state === "review_required" && candidateKind === "ai_candidate";
  const requiresSemanticForMockup = files.length > 0 && !engineStatus?.remote_provider_configured;

  useEffect(() => {
    return () => {
      previews.forEach(({ url }) => URL.revokeObjectURL(url));
    };
  }, [previews]);

  useEffect(() => {
    getVersion().then(setAppVersion).catch(() => setAppVersion(""));
  }, []);

  useEffect(() => {
    let cancelled = false;
    const ping = async () => {
      try {
        const response = await fetch(`${ENGINE_URL}/status`);
        if (!response.ok) throw new Error("Engine status unavailable");
        const payload = (await response.json()) as EngineStatus;
        const versionMatches = !appVersion || payload.version === appVersion;
        if (!cancelled) {
          setEngineOnline(versionMatches);
          setEngineStatus(payload);
          setEngineIssue(
            versionMatches ? "" :
            `Engine v${payload.version ?? "unknown"} differs from desktop v${appVersion}. Close the old tool and restart.`
          );
        }
      } catch {
        if (!cancelled) {
          setEngineOnline(false);
          setEngineStatus(null);
          setEngineIssue("The desktop cannot access the local engine API. Check %LOCALAPPDATA%\\PODArtworkTool\\logs\\desktop-engine.log or restart the app.");
        }
      }
    };
    ping();
    const timer = window.setInterval(ping, 3000);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [appVersion, providerRefresh]);

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

  useEffect(() => {
    if (!job || job.state !== "review_required") {
      setCandidateKind(null);
      return;
    }
    let cancelled = false;
    fetch(`${ENGINE_URL}/jobs/${job.job_id}/preview-info`)
      .then((response) => response.ok ? response.json() : null)
      .then((info) => { if (!cancelled) setCandidateKind(info?.kind ?? null); })
      .catch(() => { if (!cancelled) setCandidateKind(null); });
    return () => { cancelled = true; };
  }, [job?.job_id, job?.state]);

  useEffect(() => {
    if (!engineOnline) return;
    let stopped = false;
    const checkForUpdates = async () => {
      if (updateCheckInFlight.current) return;
      updateCheckInFlight.current = true;
      try {
        const result = await systemRequest<UpdateCheck>("/system/check-updates", "POST");
        if (!stopped) setUpdateCheck(result);
      } catch {
        // Offline or concurrent checks must never interrupt artwork jobs.
      } finally {
        updateCheckInFlight.current = false;
      }
    };
    void checkForUpdates();
    // A long-running studio session also discovers new releases without a restart.
    const interval = window.setInterval(checkForUpdates, 6 * 60 * 60 * 1000);
    return () => { stopped = true; window.clearInterval(interval); };
  }, [engineOnline]);

  useEffect(() => {
    if (engineOnline) audit("tab_opened", activeTab);
  }, [engineOnline, activeTab]);

  const chooseFiles = (event: ChangeEvent<HTMLInputElement>) => {
    const selected = Array.from(event.target.files ?? []);
    event.target.value = "";
    const oversized = selected.find((file) => file.size > MAX_UPLOAD_BYTES);
    if (oversized) {
      setError(`Ảnh "${oversized.name}" vượt giới hạn 50 MB/ảnh. Vui lòng chọn ảnh nhỏ hơn.`);
      return;
    }
    setFiles(selected);
    if (selected.length) audit("references_selected", "artwork");
    setJob(null);
    setError("");
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
      const created = await response.json() as Job;
      setJob(created);
      audit("job_submitted", "artwork", created.job_id);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to create job");
    } finally {
      setBusy(false);
    }
  };

  const retryWithAI = async () => {
    if (!job || !engineOnline || !engineStatus?.remote_provider_available || busy) return;
    setBusy(true); setError("");
    try {
      const response = await fetch(`${ENGINE_URL}/jobs/${job.job_id}/retry-ai`, { method: "POST" });
      if (!response.ok) {
        const data = await response.json().catch(() => ({}));
        throw new Error(typeof data.detail === "string" ? data.detail : `HTTP ${response.status}`);
      }
      const retried = await response.json() as Job;
      setJob(retried);
      audit("job_retried", "artwork", retried.job_id);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Unable to retry with AI");
    } finally { setBusy(false); }
  };

  const providerLabel = !engineStatus?.remote_provider_configured
    ? "Local only"
    : engineStatus.remote_provider_available
      ? "AI rescue ready"
      : "AI rescue cooling down";

  return (

    <main className="app-shell">
      <header className="topbar">
        <div className="brand-identity">
          <span className="brand-mark"><Layers3 size={20} strokeWidth={2.1}/></span>
          <div className="brand-copy">
            <strong>POD Artwork <span className="app-version">v{appVersion || "…"}</span></strong>
            <small>AI PRINT STUDIO</small>
          </div>
        </div>
        <div className="engine-summary">
          {engineOnline && <span className="runtime-note">{providerLabel} · {engineStatus?.job_concurrency ?? 1} parallel</span>}
          <Badge variant="outline" className={`status ${engineOnline ? "online" : "offline"}`} role="status">
            <span />{engineOnline ? "Engine ready" : "Engine offline"}
          </Badge>
          {updateCheck?.update_available && <Badge variant="secondary" className="system-update-head">New v{updateCheck.latest_version}</Badge>}
          {engineIssue && <div className="engine-issue" role="alert">{engineIssue}</div>}
        </div>
      </header>
      <div className="app-body">
        <nav className="primary-tabs" aria-label="Tool sections">
          <div className="nav-label">WORKSPACE</div>
          <Button type="button" variant="ghost" className={activeTab === "artwork" ? "selected" : ""}
            aria-current={activeTab === "artwork" ? "page" : undefined} onClick={() => setActiveTab("artwork")}>
            <Images size={17}/> <span>Artwork</span><ChevronRight className="nav-chevron" size={14}/>
          </Button>
          <Button type="button" variant="ghost" className={activeTab === "chat" ? "selected" : ""}
            aria-current={activeTab === "chat" ? "page" : undefined} onClick={() => setActiveTab("chat")}>
            <MessageSquareText size={17}/> <span>AI Image Chat</span><ChevronRight className="nav-chevron" size={14}/>
          </Button>
          <Button type="button" variant="ghost" className={activeTab === "dataset" ? "selected" : ""}
            aria-current={activeTab === "dataset" ? "page" : undefined} onClick={() => setActiveTab("dataset")}>
            <Database size={17}/> <span>Datasets</span><ChevronRight className="nav-chevron" size={14}/>
          </Button>
          <div className="nav-label nav-label-settings">SYSTEM</div>
          <Button type="button" variant="ghost" className={activeTab === "provider" ? "selected" : ""}
            aria-current={activeTab === "provider" ? "page" : undefined} onClick={() => setActiveTab("provider")}>
            <SlidersHorizontal size={17}/> <span>AI Settings</span><ChevronRight className="nav-chevron" size={14}/>
          </Button>
          <div className="nav-footer">
            <Cpu size={15}/>
            <span>Local engine<br/><strong>{engineOnline ? "Connected" : "Disconnected"}</strong></span>
          </div>
        </nav>
        <div className="app-content">

      {activeTab === "artwork" ? (
      <>
      <div className="studio-page-heading"><div><span className="eyebrow">CREATE</span><h1>Artwork Reconstruction</h1><p>Turn product references into print-ready artwork.</p></div><span className="studio-page-tag"><Sparkles size={13}/> Local-first pipeline</span></div>
      <section className="workspace">
        <Card className={`upload-card gap-0 py-0 ${files.length ? "has-files" : ""}`}>
          {files.length === 0 ? (
            <>
              <div className="upload-icon" aria-hidden="true"><ImagePlus size={25}/></div>
              <h2>Add reference images</h2>
              <p>Drop images here or browse your files.</p>
              <p>PNG, JPG, WebP · tối đa 50 MB mỗi ảnh</p>
              <label className="primary-button">
                <ImagePlus size={15}/> Select images
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
                These images will be processed together as one artwork reference set · 50 MB per file.
              </div>
            </>
          )}
        </Card>

        <Card className="run-card gap-0 py-0">
          <div className="auto-row">
            <div>
              <div className="eyebrow">PROCESSING</div>
              <h3>Auto reconstruction</h3>
              <p>Local-first. AI is used only when the quality gates require rescue.</p>
            </div>
            <Badge variant="secondary" className="auto-badge"><CheckCircle2 size={13}/> Recommended</Badge>
          </div>

          <Button variant="link" size="sm" className="advanced-toggle" onClick={() => setAdvanced((value) => !value)}>
            {advanced ? "Hide advanced" : "Advanced"}
          </Button>

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

          {requiresSemanticForMockup && (
            <div className="provider-warning">
              AI reconstruction is not configured. Product mockups may require
              semantic reconstruction before a clean 2D PNG can be exported.
            </div>
          )}

          <div className="dataset-muted processing-hint">Transparent artwork is ideal. Product mockups may need AI reconstruction.</div>
          <Button type="button"
            className="run-button"
            disabled={!files.length || !engineOnline || busy || jobActive}
            onClick={createJob}
          >
            <Play size={15} fill="currentColor"/> {busy ? "Starting…" : jobActive ? "Processing…" : "Generate artwork"}
          </Button>
          {error && <div className="error">{error}</div>}
        </Card>
      </section>

      <AutoEnhance files={files} engineOnline={engineOnline} />

      <Card className={`result-card gap-0 py-0 ${!job ? "is-empty" : ""}`}>
        <div className="result-header">
          <div>
            <div className="eyebrow">CURRENT JOB</div>
            <h3>{job ? job.job_id.slice(0, 12) : "No job running"}</h3>
          </div>
          <strong>{progress}%</strong>
        </div>

        <div className="timeline">
          {TIMELINE.map((stage, index) => {
            const state = index < activeTimelineIndex ? "complete" : index === activeTimelineIndex && job ? "active" : "pending";
            return (
              <div className={`timeline-step ${state}`} key={stage.key}>
                <span className="timeline-dot">{state === "complete" ? "✓" : index + 1}</span>
                <span>{index === 4 && job?.state === "review_required" ? "Review needed" : stage.label}</span>
              </div>
            );
          })}
        </div>

        <Progress value={progress} aria-label="Job progress" className="progress-track" />

        <div className="stage-line">
          <strong>{hasAICandidate
            ? "AI candidate created — waiting for semantic quality review"
            : mockupNotIsolated
              ? "2D artwork not created — mockup separation requires AI"
              : job?.stage_message ?? "Your results and quality checks will appear here."}</strong>
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

        {job && (
          <div className="output-label" role="status">
            {canExport2D ? "Print-ready PNG available" :
              hasAICandidate ? "AI candidate preview — not print-ready" :
              mockupNotIsolated ? "Blocked — AI required · local draft only" :
              jobActive ? "Processing — QC pending" : "Review needed — no approved print output"}
          </div>
        )}
        {mockupNotIsolated && !hasAICandidate && (
          <div className="output-not-ready" role="alert">
            <strong>No isolated 2D artwork available</strong>
            <p>The file is only a crop of the original product photo, not a
              print-ready design. This job needs an AI reconstruction provider
              to remove the garment and reconstruct the artwork faithfully.
              No misleading Export PNG is offered.</p>
            <span>For local-only processing, use an already isolated artwork
              file with transparency rather than a shirt mockup.</span>
            <div className="recovery-actions">
              <button className="secondary-button" onClick={() => setActiveTab("provider")}>Configure AI</button>
              <button className="secondary-button" onClick={retryWithAI}
                disabled={!engineStatus?.remote_provider_available || busy}>
                {busy ? "Starting…" : "Retry with AI"}
              </button>
              <label className="secondary-button">Use better reference
                <input type="file" accept="image/*" multiple onChange={chooseFiles} hidden />
              </label>
            </div>
            {candidateKind === "local_draft" && (
              <div className="draft-preview">
                <div className="eyebrow">LOCAL DRAFT · NOT PRINT READY</div>
                <img src={`${ENGINE_URL}/jobs/${job?.job_id}/draft`} alt="Preview only — local crop not suitable for printing" />
                <p>Preview only. Garment pixels may remain; no print-ready export is available.</p>
              </div>
            )}
          </div>
        )}

        {hasAICandidate && job && (
          <div className="output-not-ready">
            <strong>AI candidate generated · QC approval required</strong>
            <p>The AI candidate can be inspected, but exact text, layout and detail fidelity
              have not passed all semantic checks. Print-ready export remains disabled.</p>
            <div className="draft-preview">
              <div className="eyebrow">AI CANDIDATE · PREVIEW ONLY</div>
              <img src={`${ENGINE_URL}/jobs/${job.job_id}/ai-candidate`}
                alt="AI-generated design candidate, not approved for printing" />
              <p>Watermarked preview. Use more references or a validated semantic judge before final export.</p>
            </div>
            <div className="recovery-actions">
              <button className="secondary-button" onClick={() => setActiveTab("provider")}>Configure AI</button>
              <button className="secondary-button" onClick={retryWithAI}
                disabled={!engineStatus?.remote_provider_available || busy}>Retry with AI</button>
            </div>
          </div>
        )}

        {canExport2D && job && (
          <div className="output-preview">
            <img
              src={`${ENGINE_URL}/jobs/${job.job_id}/output`}
              alt="Reconstructed artwork"
            />
            <div className="output-actions">
              <a className="primary-button" href={`${ENGINE_URL}/jobs/${job.job_id}/output`}
                download={`pod-artwork-${job.job_id.slice(0, 12)}.png`}>Export PNG</a>
              <button className="secondary-button" onClick={() => setActiveTab("chat")}>Chỉnh bằng AI Chat</button>
            </div>
          </div>
        )}
      </Card>
      </>
      ) : activeTab === "chat" ? (
        <AIChat engineOnline={engineOnline} inputFiles={files} artworkJob={job}
          onOpenSettings={() => setActiveTab("provider")}
          onPrintCheck={(checked) => { setJob(checked as Job); setActiveTab("artwork"); }} />
      ) : activeTab === "dataset" ? (
        <DatasetOnboarding engineOnline={engineOnline} />
      ) : (
        <AISettings engineOnline={engineOnline}
          onProviderChanged={() => setProviderRefresh((value) => value + 1)}
          latestCheck={updateCheck} onChecked={setUpdateCheck} />
      )}
        </div>
      </div>
    </main>
  );
}
