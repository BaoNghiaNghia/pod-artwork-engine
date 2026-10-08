import { useCallback, useEffect, useState } from "react";
import { open } from "@tauri-apps/plugin-dialog";

const ENGINE_URL = "http://127.0.0.1:8765";

type Dataset = { dataset_id: string; name: string; pair_count: number; artwork_count: number };
type Lane = { ready: boolean; blockers: string[]; warnings: string[] };
type GoldenReport = {
  status: "ready" | "partial" | "blocked";
  ready_for_full_golden_chain: boolean;
  dataset_id: string | null;
  dataset_name: string | null;
  golden_case_count: number;
  minimum_golden_cases: number;
  pair_count: number;
  artwork_count: number;
  missing_golden_asset_count: number;
  golden_asset_fingerprint_mismatch_count: number;
  recipe_id: string | null;
  recipe_version: string | null;
  registration: Lane;
  material_separation: Lane;
  super_resolution: Lane;
  blockers: string[];
  warnings: string[];
  production_execution_enabled: boolean;
};

const readable: Record<string, string> = {
  golden_preflight_no_datasets_registered: "Import a verified historical dataset first",
  golden_preflight_dataset_not_found: "Selected dataset no longer exists",
  golden_preflight_dataset_id_required: "Select one dataset",
  golden_preflight_dataset_is_empty: "Dataset has no pairs or artworks",
  golden_preflight_dataset_manifest_missing: "Dataset manifest missing",
  golden_preflight_dataset_manifest_invalid: "Dataset manifest is unreadable",
  golden_preflight_dataset_manifest_identity_mismatch: "Dataset manifest identity changed",
  golden_preflight_insufficient_golden_cases: "Not enough real Golden Holdout cases",
  golden_preflight_missing_golden_assets: "Some Golden source/final images cannot be found",
  golden_preflight_golden_asset_fingerprint_mismatch: "Golden image contents changed since import",
  golden_preflight_benchmark_recipe_missing: "Choose the benchmark recipe JSON",
  golden_preflight_benchmark_recipe_invalid: "Benchmark recipe is invalid",
  golden_preflight_sr_backend_unavailable: "No verified super-resolution backend configured",
  golden_preflight_remote_provider_not_configured: "Remote AI provider is not configured",
  golden_preflight_remote_provider_recipe_invalid: "Remote provider recipe is invalid",
  golden_preflight_local_sr_adapter_invalid: "Local SR adapter configuration is invalid",
  golden_preflight_local_sr_adapter_unavailable: "Local SR adapter command is unavailable",
  golden_preflight_remote_sr_adapter_invalid: "Remote SR adapter configuration is invalid",
  golden_preflight_remote_sr_adapter_unavailable: "Remote SR adapter is unavailable"
};

async function errorText(response: Response): Promise<string> {
  try {
    const data = await response.json();
    if (typeof data.detail === "string") return data.detail;
  } catch {
    // Fall through to the status code.
  }
  return `HTTP ${response.status}`;
}

export default function GoldenReadiness({
  engineOnline,
  importedDatasetId
}: {
  engineOnline: boolean;
  importedDatasetId?: string | null;
}) {
  const [datasets, setDatasets] = useState<Dataset[]>([]);
  const [selectedId, setSelectedId] = useState("");
  const [recipePath, setRecipePath] = useState("");
  const [localAdapterPath, setLocalAdapterPath] = useState("");
  const [remoteAdapterPath, setRemoteAdapterPath] = useState("");
  const [minCases, setMinCases] = useState(3);
  const [report, setReport] = useState<GoldenReport | null>(null);
  const [loading, setLoading] = useState(false);
  const [fetching, setFetching] = useState(false);
  const [error, setError] = useState("");

  const refresh = useCallback(async () => {
    if (!engineOnline) return;
    setFetching(true);
    try {
      const response = await fetch(`${ENGINE_URL}/datasets`);
      if (!response.ok) throw new Error(await errorText(response));
      const list = (await response.json()) as Dataset[];
      setDatasets(list);
      setSelectedId((current) => {
        if (importedDatasetId && list.some((item) => item.dataset_id === importedDatasetId)) {
          return importedDatasetId;
        }
        return current && list.some((item) => item.dataset_id === current)
          ? current
          : (list.length === 1 ? list[0].dataset_id : "");
      });
      setError("");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to list datasets");
    } finally {
      setFetching(false);
    }
  }, [engineOnline, importedDatasetId]);

  useEffect(() => { void refresh(); }, [refresh]);

  const selectFile = async (kind: "recipe" | "local" | "remote") => {
    try {
      const result = await open({
        multiple: false,
        directory: false,
        title: kind === "recipe" ? "Choose benchmark recipe" : "Choose SR adapter JSON",
        filters: [{ name: "JSON", extensions: ["json"] }]
      });
      if (typeof result === "string") {
        if (kind === "recipe") setRecipePath(result);
        else if (kind === "local") setLocalAdapterPath(result);
        else setRemoteAdapterPath(result);
        setReport(null);
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "Unable to open file picker");
    }
  };

  const check = async () => {
    if (!engineOnline || loading) return;
    setLoading(true);
    setError("");
    setReport(null);
    try {
      const response = await fetch(`${ENGINE_URL}/harness/golden-preflight`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          dataset_id: selectedId || null,
          recipe_path: recipePath.trim() || null,
          local_sr_adapter_path: localAdapterPath.trim() || null,
          remote_sr_adapter_path: remoteAdapterPath.trim() || null,
          minimum_golden_cases: minCases
        })
      });
      if (!response.ok) throw new Error(await errorText(response));
      setReport((await response.json()) as GoldenReport);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Golden preflight failed");
    } finally {
      setLoading(false);
    }
  };

  const laneEntries: [string, Lane][] = report
    ? [
      ["Registration", report.registration],
      ["Material separation", report.material_separation],
      ["Super-resolution", report.super_resolution]
    ]
    : [];

  return (
    <div className="dataset-panel golden-panel">
      <div className="dataset-section-title">4. Golden Holdout readiness</div>
      <p className="dataset-muted">
        Read-only. Verify benchmark readiness for a registered dataset. No model is trained, no
        benchmark is run, and production policies remain disabled.
      </p>
      <div className="golden-inputs">
        <label>
          <span>Registered dataset</span>
          <select value={selectedId} onChange={(event) => { setSelectedId(event.target.value); setReport(null); }}>
            <option value="">Select dataset</option>
            {datasets.map((dataset) => (
              <option value={dataset.dataset_id} key={dataset.dataset_id}>
                {dataset.name} ({dataset.pair_count} pairs)
              </option>
            ))}
          </select>
        </label>
        <label>
          <span>Minimum Golden cases</span>
          <input type="number" value={minCases} min={1} max={10000} onChange={(event) => { setMinCases(Math.max(1, Number(event.target.value) || 1)); setReport(null); }} />
        </label>
      </div>
      <label className="golden-path">
        <span>Benchmark recipe (.json)</span>
        <div className="dataset-path-control">
          <input value={recipePath} onChange={(event) => { setRecipePath(event.target.value); setReport(null); }} placeholder="Choose recipe or leave blank for configured default" />
          <button type="button" onClick={() => void selectFile("recipe")}>Browse</button>
        </div>
      </label>
      <details className="dataset-options">
        <summary>SR backend adapters (optional)</summary>
        <label className="golden-path">
          <span>Local SR adapter JSON</span>
          <div className="dataset-path-control">
            <input value={localAdapterPath} onChange={(event) => { setLocalAdapterPath(event.target.value); setReport(null); }} />
            <button type="button" onClick={() => void selectFile("local")}>Browse</button>
          </div>
        </label>
        <label className="golden-path">
          <span>Remote SR adapter JSON</span>
          <div className="dataset-path-control">
            <input value={remoteAdapterPath} onChange={(event) => { setRemoteAdapterPath(event.target.value); setReport(null); }} />
            <button type="button" onClick={() => void selectFile("remote")}>Browse</button>
          </div>
        </label>
      </details>
      {datasets.length === 0 && !fetching && (
        <p className="dataset-muted">No registered dataset yet. Complete steps 1–3 above first.</p>
      )}
      <div className="golden-actions">
        <button className="dataset-action" disabled={!engineOnline || !selectedId || loading} onClick={() => void check()}>
          {loading ? "Checking Golden data…" : "Check Golden readiness"}
        </button>
        <button className="dataset-details-toggle" disabled={!engineOnline || fetching} onClick={() => void refresh()}>
          {fetching ? "Refreshing…" : "Refresh datasets"}
        </button>
      </div>
      {report && (
        <div className="golden-report">
          <div className={`dataset-status ${report.ready_for_full_golden_chain ? "ready" : "blocked"}`}>
            <strong>{report.status === "ready" ? "All Golden lanes ready" : report.status === "partial" ? "Partially ready" : "Golden readiness blocked"}</strong>
            <span>{report.golden_case_count} / {report.minimum_golden_cases} Golden cases · {report.artwork_count} unique artworks · {report.pair_count} pairs</span>
          </div>
          <div className="golden-lanes">
            {laneEntries.map(([name, lane]) => (
              <div className="golden-lane" key={name}>
                <div className="golden-lane-header">
                  <strong>{name}</strong>
                  <span className={lane.ready ? "lane-ready" : "lane-blocked"}>{lane.ready ? "Ready" : "Blocked"}</span>
                </div>
                {lane.blockers.map((reason) => <p key={reason}>{readable[reason] ?? reason}</p>)}
              </div>
            ))}
          </div>
          {report.warnings.length > 0 && (
            <div className="dataset-warnings">
              <strong>Notes</strong>
              {report.warnings.map((reason) => <div key={reason}>{readable[reason] ?? reason}</div>)}
            </div>
          )}
        </div>
      )}
      {error && <p className="dataset-error" role="alert">{error}</p>}
    </div>
  );
}
