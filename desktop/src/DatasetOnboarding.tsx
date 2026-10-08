import { useState } from "react";
import { open } from "@tauri-apps/plugin-dialog";
import GoldenReadiness from "./GoldenReadiness";

const ENGINE_URL = "http://127.0.0.1:8765";

type Mode = "folders" | "manifest";
type FormState = {
  mode: Mode;
  source_dir: string;
  target_dir: string;
  manifest_path: string;
  dataset_name: string;
  seed: string;
  minimum_golden_cases: number;
  allow_visual_fallback: boolean;
  id_regex: string;
};

type OnboardingReport = {
  ready_to_import: boolean;
  status: string;
  source_count: number;
  target_count: number;
  pair_count: number;
  projected_golden_case_count: number;
  minimum_golden_cases: number;
  projected_artwork_split_counts: Record<string, number>;
  blockers: string[];
  warnings: string[];
  unmatched_sources: string[];
  unmatched_targets: string[];
  conflicts: string[];
  invalid_images: string[];
  mutates_registry: boolean;
  production_execution_enabled: boolean;
};

type PreviewResponse = {
  report: OnboardingReport;
  snapshot_id: string | null;
  production_execution_enabled: boolean;
};

type ImportResponse = {
  imported_pair_ids: string[];
  dataset: { dataset_id: string; name: string } | null;
  production_execution_enabled: boolean;
};

const reasonLabels: Record<string, string> = {
  historical_onboarding_no_source_images: "No source images found",
  historical_onboarding_no_target_images: "No final artwork images found",
  historical_onboarding_invalid_images: "Unreadable or unsupported images",
  historical_onboarding_pair_conflicts: "Multiple final images conflict for one design",
  historical_onboarding_unmatched_sources: "Source images without a matching final artwork",
  historical_onboarding_unmatched_targets: "Final artwork images without source images",
  historical_onboarding_no_importable_pairs: "No valid source → final pairs",
  historical_onboarding_insufficient_projected_golden_cases: "Not enough Golden Holdout cases",
  historical_onboarding_equivalent_target_duplicates: "Equivalent final images detected",
  historical_onboarding_exact_duplicate_sources: "Repeated identical source images",
  historical_onboarding_exact_duplicate_targets: "Repeated identical final images",
  historical_onboarding_visual_fallback_used: "Some images matched through visual fallback",
  historical_onboarding_projected_split_not_persisted: "Split counts are estimates until import"
};

async function decodeError(response: Response): Promise<string> {
  try {
    const body = await response.json();
    const detail = body.detail;
    if (typeof detail === "string") return detail;
    if (detail && typeof detail === "object" && Array.isArray(detail.blockers)) {
      return detail.blockers.map((reason: string) => reasonLabels[reason] ?? reason).join("; ");
    }
  } catch {
    // Use HTTP status as a fallback.
  }
  return `Request failed (HTTP ${response.status})`;
}

const initialForm: FormState = {
  mode: "folders",
  source_dir: "",
  target_dir: "",
  manifest_path: "",
  dataset_name: "historical-artwork",
  seed: "foundation-v1",
  minimum_golden_cases: 3,
  allow_visual_fallback: true,
  id_regex: ""
};

export default function DatasetOnboarding({ engineOnline }: { engineOnline: boolean }) {
  const [form, setForm] = useState<FormState>(initialForm);
  const [preview, setPreview] = useState<PreviewResponse | null>(null);
  const [imported, setImported] = useState<ImportResponse | null>(null);
  const [confirmed, setConfirmed] = useState(false);
  const [showDetails, setShowDetails] = useState(false);
  const [busy, setBusy] = useState<"preflight" | "import" | null>(null);
  const [error, setError] = useState("");

  const update = <K extends keyof FormState>(key: K, value: FormState[K]) => {
    setForm((current) => ({ ...current, [key]: value }));
    setPreview(null);
    setImported(null);
    setConfirmed(false);
    setError("");
  };

  const choosePath = async (field: "source_dir" | "target_dir" | "manifest_path") => {
    try {
      const result = await open(
        field === "manifest_path"
          ? { multiple: false, directory: false, title: "Choose pairs manifest", filters: [{ name: "JSON", extensions: ["json"] }] }
          : { multiple: false, directory: true, title: field === "source_dir" ? "Choose source images folder" : "Choose final artworks folder" }
      );
      if (typeof result === "string") update(field, result);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Unable to open folder picker");
    }
  };

  const requestPayload = () => ({
    mode: form.mode,
    source_dir: form.mode === "folders" ? form.source_dir.trim() : null,
    target_dir: form.mode === "folders" ? form.target_dir.trim() : null,
    manifest_path: form.mode === "manifest" ? form.manifest_path.trim() : null,
    id_regex: form.mode === "folders" ? form.id_regex.trim() || null : null,
    seed: form.seed.trim(),
    minimum_golden_cases: form.minimum_golden_cases,
    allow_visual_fallback: form.allow_visual_fallback
  });

  const runPreflight = async () => {
    if (!engineOnline || busy) return;
    setBusy("preflight");
    setError("");
    setPreview(null);
    setImported(null);
    setConfirmed(false);
    try {
      const response = await fetch(`${ENGINE_URL}/historical/onboarding/preflight`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(requestPayload())
      });
      if (!response.ok) throw new Error(await decodeError(response));
      setPreview((await response.json()) as PreviewResponse);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Preflight failed");
    } finally {
      setBusy(null);
    }
  };

  const runImport = async () => {
    if (!engineOnline || busy || !confirmed || !preview?.report.ready_to_import || !preview.snapshot_id) return;
    setBusy("import");
    setError("");
    try {
      const response = await fetch(`${ENGINE_URL}/historical/onboarding/import`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          ...requestPayload(),
          snapshot_id: preview.snapshot_id,
          dataset_name: form.dataset_name.trim(),
          confirmation: "IMPORT"
        })
      });
      if (!response.ok) throw new Error(await decodeError(response));
      setImported((await response.json()) as ImportResponse);
      setConfirmed(false);
      setPreview(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Import failed");
    } finally {
      setBusy(null);
    }
  };

  const isReady = Boolean(preview?.report.ready_to_import && preview.snapshot_id);
  const fieldsPresent = form.mode === "manifest"
    ? Boolean(form.manifest_path.trim())
    : Boolean(form.source_dir.trim() && form.target_dir.trim());
  const report = preview?.report;
  const splits = report?.projected_artwork_split_counts;

  return (
    <section className="dataset-workspace">
      <div className="dataset-intro">
        <div>
          <div className="eyebrow">LEARNING DATA</div>
          <h2>Historical Dataset</h2>
          <p>Match original product images to approved final artworks. Preflight never writes to the dataset registry.</p>
        </div>
        <span className="dataset-safe">Read-only until Import</span>
      </div>

      <div className="dataset-panel">
        <div className="dataset-section-title">1. Choose existing data</div>
        <div className="dataset-mode-selector" role="group" aria-label="Dataset source type">
          <button className={form.mode === "folders" ? "selected" : ""} onClick={() => update("mode", "folders")}>Folders</button>
          <button className={form.mode === "manifest" ? "selected" : ""} onClick={() => update("mode", "manifest")}>JSON manifest</button>
        </div>

        {form.mode === "folders" ? (
          <div className="dataset-paths">
            <label>
              <span>Source images folder</span>
              <div className="dataset-path-control">
                <input value={form.source_dir} onChange={(e) => update("source_dir", e.target.value)} placeholder="D:\\Artwork\\Sources" />
                <button onClick={() => choosePath("source_dir")}>Browse</button>
              </div>
            </label>
            <label>
              <span>Approved final artworks folder</span>
              <div className="dataset-path-control">
                <input value={form.target_dir} onChange={(e) => update("target_dir", e.target.value)} placeholder="D:\\Artwork\\Finals" />
                <button onClick={() => choosePath("target_dir")}>Browse</button>
              </div>
            </label>
          </div>
        ) : (
          <div className="dataset-paths">
            <label>
              <span>Pairs manifest (.json)</span>
              <div className="dataset-path-control">
                <input value={form.manifest_path} onChange={(e) => update("manifest_path", e.target.value)} placeholder="D:\\Artwork\\pairs.json" />
                <button onClick={() => choosePath("manifest_path")}>Browse</button>
              </div>
            </label>
          </div>
        )}

        <details className="dataset-options">
          <summary>Matching options</summary>
          <div className="dataset-option-grid">
            <label>
              <span>Split seed</span>
              <input value={form.seed} onChange={(e) => update("seed", e.target.value)} />
            </label>
            <label>
              <span>Minimum Golden cases</span>
              <input type="number" min="1" max="10000" value={form.minimum_golden_cases} onChange={(e) => update("minimum_golden_cases", Number(e.target.value) || 1)} />
            </label>
            {form.mode === "folders" && (
              <>
                <label>
                  <span>ID regex (optional)</span>
                  <input value={form.id_regex} onChange={(e) => update("id_regex", e.target.value)} placeholder="Design ID extraction" />
                </label>
                <label className="dataset-checkbox-row">
                  <input type="checkbox" checked={form.allow_visual_fallback} onChange={(e) => update("allow_visual_fallback", e.target.checked)} />
                  Strict visual fallback
                </label>
              </>
            )}
          </div>
        </details>

        <button className="dataset-action" onClick={runPreflight} disabled={!engineOnline || !fieldsPresent || Boolean(busy)}>
          {busy === "preflight" ? "Checking images and pairing…" : "Run Preflight"}
        </button>
      </div>

      <div className="dataset-panel">
        <div className="dataset-section-title">2. Review results</div>
        {!report && !imported && <p className="dataset-muted">Run Preflight to see valid pairs, mismatches, duplicates and the projected Golden split.</p>}
        {report && (
          <>
            <div className={`dataset-status ${isReady ? "ready" : "blocked"}`}>
              <strong>{isReady ? "Ready to import" : "Import blocked"}</strong>
              <span>{isReady ? "All checks passed. Import remains a separate action." : "Resolve the blockers below and run Preflight again."}</span>
            </div>
            <div className="dataset-stats">
              <div><strong>{report.source_count}</strong><span>Source images</span></div>
              <div><strong>{report.target_count}</strong><span>Final images</span></div>
              <div><strong>{report.pair_count}</strong><span>Paired artworks</span></div>
              <div><strong>{report.projected_golden_case_count} / {report.minimum_golden_cases}</strong><span>Golden cases</span></div>
            </div>
            {splits && (
              <p className="dataset-muted">Projected unique designs: Train {splits.train ?? 0} · Validation {splits.validation ?? 0} · Golden {splits.golden_holdout ?? 0}</p>
            )}
            {report.blockers.length > 0 && (
              <div className="dataset-issues">
                <strong>Blockers ({report.blockers.length})</strong>
                {report.blockers.map((reason) => <div key={reason}>{reasonLabels[reason] ?? reason}</div>)}
              </div>
            )}
            {report.warnings.length > 0 && (
              <div className="dataset-warnings">
                <strong>Notes ({report.warnings.length})</strong>
                {report.warnings.map((reason) => <div key={reason}>{reasonLabels[reason] ?? reason}</div>)}
              </div>
            )}
            {(report.conflicts.length > 0 || report.unmatched_sources.length > 0 || report.unmatched_targets.length > 0 || report.invalid_images.length > 0) && (
              <>
                <button className="dataset-details-toggle" onClick={() => setShowDetails((value) => !value)}>{showDetails ? "Hide file details" : "Show file details"}</button>
                {showDetails && (
                  <div className="dataset-file-issues">
                    {[...report.conflicts, ...report.unmatched_sources, ...report.unmatched_targets, ...report.invalid_images].slice(0, 80).map((value, index) => <div key={index} title={value}>{value}</div>)}
                  </div>
                )}
              </>
            )}
          </>
        )}
      </div>

      <div className="dataset-panel">
        <div className="dataset-section-title">3. Import approved data</div>
        {imported?.dataset ? (
          <div className="dataset-status ready">
            <strong>Dataset registered</strong>
            <span>{imported.imported_pair_ids.length} pairs · ID: {imported.dataset.dataset_id}</span>
          </div>
        ) : (
          <>
            <p className="dataset-muted">Import writes a dataset to the local registry. It does not activate production AI policy.</p>
            <label className="dataset-name-label">
              <span>Dataset name</span>
              <input value={form.dataset_name} onChange={(e) => { setForm((current) => ({ ...current, dataset_name: e.target.value })); setConfirmed(false); }} />
            </label>
            <label className="dataset-checkbox-row">
              <input type="checkbox" checked={confirmed} disabled={!isReady || Boolean(busy)} onChange={(e) => setConfirmed(e.target.checked)} />
              I verified these source/final pairs and approve registration.
            </label>
            <button className="dataset-action import" onClick={runImport} disabled={!isReady || !confirmed || !form.dataset_name.trim() || Boolean(busy) || !engineOnline}>
              {busy === "import" ? "Importing…" : "Import verified dataset"}
            </button>
          </>
        )}
      </div>
      <GoldenReadiness engineOnline={engineOnline} importedDatasetId={imported?.dataset?.dataset_id} />
      {error && <p className="dataset-error" role="alert">{error}</p>}
    </section>
  );
}
