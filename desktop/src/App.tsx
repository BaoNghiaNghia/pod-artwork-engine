import { ChangeEvent, useEffect, useMemo, useState } from "react";

type QualityMode = "quick_2d" | "print_ready" | "max_fidelity";

type Job = {
  job_id: string;
  quality_mode: QualityMode;
  state: string;
  progress: number;
  stage_message: string;
  source_paths: string[];
  result_path?: string | null;
};

const ENGINE_URL = "http://127.0.0.1:8765";

export default function App() {
  const [engineOnline, setEngineOnline] = useState(false);
  const [files, setFiles] = useState<File[]>([]);
  const [mode, setMode] = useState<QualityMode>("print_ready");
  const [job, setJob] = useState<Job | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const progress = useMemo(() => Math.round((job?.progress ?? 0) * 100), [job]);

  useEffect(() => {
    let cancelled = false;
    const ping = async () => {
      try {
        const response = await fetch(`${ENGINE_URL}/health`);
        if (!cancelled) setEngineOnline(response.ok);
      } catch {
        if (!cancelled) setEngineOnline(false);
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
    if (!job || ["completed", "failed_final", "cancelled", "review_required"].includes(job.state)) {
      return;
    }
    const timer = window.setInterval(async () => {
      try {
        const response = await fetch(`${ENGINE_URL}/jobs/${job.job_id}`);
        if (response.ok) setJob(await response.json());
      } catch {
        // Connection badge handles transient engine disconnects.
      }
    }, 1000);
    return () => window.clearInterval(timer);
  }, [job?.job_id, job?.state]);

  const chooseFiles = (event: ChangeEvent<HTMLInputElement>) => {
    setFiles(Array.from(event.target.files ?? []));
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

  return (
    <main className="app-shell">
      <header className="topbar">
        <div>
          <div className="eyebrow">POD AI PRINT</div>
          <h1>Artwork Reconstruction</h1>
        </div>
        <div className={`status ${engineOnline ? "online" : "offline"}`}>
          <span />
          {engineOnline ? "Engine ready" : "Engine offline"}
        </div>
      </header>

      <section className="workspace">
        <div className="upload-card">
          <div className="upload-icon">＋</div>
          <h2>Drop product references here</h2>
          <p>Use one image or several views of the same artwork.</p>
          <label className="primary-button">
            Select images
            <input type="file" accept="image/*" multiple onChange={chooseFiles} hidden />
          </label>
          {files.length > 0 && (
            <div className="file-summary">
              {files.length} image{files.length > 1 ? "s" : ""} selected
            </div>
          )}
        </div>

        <div className="mode-card">
          <h3>Processing mode</h3>
          <div className="mode-grid">
            {[
              ["quick_2d", "Quick 2D", "Fast reviewable draft"],
              ["print_ready", "Print Ready", "Default fidelity + technical QC"],
              ["max_fidelity", "Max Fidelity", "Stricter analysis and rescue"]
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
          <button className="run-button" disabled={!files.length || !engineOnline || busy} onClick={createJob}>
            {busy ? "Creating job…" : "Process artwork"}
          </button>
          {error && <div className="error">{error}</div>}
        </div>
      </section>

      <section className="result-card">
        <div className="result-header">
          <div>
            <div className="eyebrow">CURRENT JOB</div>
            <h3>{job ? job.job_id.slice(0, 12) : "No job yet"}</h3>
          </div>
          <strong>{progress}%</strong>
        </div>
        <div className="progress-track">
          <div className="progress-value" style={{ width: `${progress}%` }} />
        </div>
        <p>{job?.stage_message ?? "Select references and start processing."}</p>
        {job && <div className="job-state">{job.state.replaceAll("_", " ")}</div>}
      </section>
    </main>
  );
}
