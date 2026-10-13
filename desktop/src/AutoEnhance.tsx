import { useEffect, useState } from "react";
import { AlertTriangle, CheckCircle2, Download, RefreshCw, SlidersHorizontal, Sparkles } from "lucide-react";
import { Button } from "@/components/ui/button";

const API = "http://127.0.0.1:8765";
const MAX_AUTOMATIC_IMAGES = 6;

type Route = {
  category: string;
  mode: string;
  confidence: number;
  method: string;
  reasons: string[];
  supported_modes: string[];
  requires_review: boolean;
  print_ready: boolean;
};
type Entry = {
  name: string;
  status: "analyzing" | "enhancing" | "ready" | "error";
  route?: Route;
  url?: string;
  error?: string;
};

const MODE_LABELS: Record<string, string> = {
  auto: "Auto detect",
  typography_clean: "Typography Clean",
  vector_clean: "Flat Logo Clean",
  sticker_clean: "Sticker / Cartoon",
  painterly_clean: "Painterly / Brush",
  photo_cutout: "Photo / Cutout",
  metal_preserve: "Metal / Gloss",
  dark_rescue: "Dark Contrast Rescue",
  gentle_clean: "Gentle Clean",
};

const CATEGORY_LABELS: Record<string, string> = {
  typography: "Typography",
  logo_vector: "Logo / flat shapes",
  sticker_cartoon: "Sticker / cartoon",
  photo_cutout: "Photo / cutout",
  painterly_textured: "Painterly / texture",
  metal_gloss_effect: "Metal / chrome",
  dark_low_contrast: "Dark / low contrast",
  mixed: "Mixed / uncertain",
};

async function readFailure(response: Response): Promise<string> {
  const body = await response.json().catch(() => ({}));
  return typeof body.detail === "string" ? body.detail : `Processing failed (HTTP ${response.status})`;
}

/** Does not persist input/output; old object URLs are revoked on file change. */
export default function AutoEnhance({ files, engineOnline }: { files: File[]; engineOnline: boolean }) {
  const [override, setOverride] = useState("auto");
  const [retry, setRetry] = useState(0);
  const [entries, setEntries] = useState<Entry[]>([]);
  const selected = files.slice(0, MAX_AUTOMATIC_IMAGES);

  useEffect(() => {
    if (!engineOnline || !selected.length) {
      setEntries([]);
      return;
    }
    const abort = new AbortController();
    const urls: string[] = [];
    setEntries(selected.map((file) => ({ name: file.name, status: "analyzing" })));
    const update = (index: number, patch: Partial<Entry>) => {
      if (!abort.signal.aborted) {
        setEntries((previous) => previous.map((entry, i) =>
          i === index ? { ...entry, ...patch } : entry));
      }
    };

    // Bound memory/CPU: process only a small batch, one image at a time.
    void (async () => {
      for (let i = 0; i < selected.length; i++) {
        if (abort.signal.aborted) return;
        const file = selected[i];
        if (file.size > 16 * 1024 * 1024) {
          update(i, { status: "error", error: "Image exceeds 16 MB limit" });
          continue;
        }
        try {
          const analyzeBody = new FormData();
          analyzeBody.append("file", file);
          update(i, { status: "analyzing" });
          const analyzed = await fetch(`${API}/enhance/analyze`, {
            method: "POST", body: analyzeBody, signal: abort.signal,
          });
          if (!analyzed.ok) throw new Error(await readFailure(analyzed));
          const route = await analyzed.json() as Route;
          update(i, { route, status: "enhancing" });

          const runBody = new FormData();
          runBody.append("file", file);
          const processed = await fetch(`${API}/enhance/run?mode=${encodeURIComponent(override)}`, {
            method: "POST", body: runBody, signal: abort.signal,
          });
          if (!processed.ok) throw new Error(await readFailure(processed));
          const blob = await processed.blob();
          if (abort.signal.aborted) return;
          const url = URL.createObjectURL(blob);
          urls.push(url);
          update(i, {
            status: "ready",
            url,
            route: { ...route, mode: processed.headers.get("X-Enhance-Mode") || route.mode },
          });
        } catch (error) {
          if (abort.signal.aborted) return;
          update(i, { status: "error", error: error instanceof Error ? error.message : "Enhance failed" });
        }
      }
    })();

    return () => {
      abort.abort();
      urls.forEach(URL.revokeObjectURL);
    };
    // file and mode changes should start a new batch without orphaning preview URLs
  }, [files, engineOnline, override, retry]);

  if (!files.length) return null;
  return (
    <section className="enhance-panel" aria-label="Auto Enhance Artwork">
      <div className="enhance-heading">
        <div>
          <div className="eyebrow">LOCAL IMAGE PROCESSING</div>
          <h3><Sparkles size={17}/> Auto Enhance Artwork</h3>
          <p>Tự nhận diện loại ảnh và áp dụng xử lý làm nét raster phù hợp ngay sau khi upload.</p>
        </div>
        <Button type="button" size="sm" variant="outline"
          disabled={!engineOnline}
          onClick={() => setRetry((v) => v + 1)}>
          <RefreshCw size={14}/> Re-run
        </Button>
      </div>
      <div className="enhance-controls">
        <label htmlFor="enhance-route"><SlidersHorizontal size={14}/> Chế độ xử lý</label>
        <select id="enhance-route" value={override} onChange={(event) => setOverride(event.target.value)}>
          {Object.entries(MODE_LABELS).map(([mode, label]) =>
            <option key={mode} value={mode}>{label}</option>)}
        </select>
        {files.length > MAX_AUTOMATIC_IMAGES &&
          <span role="note">Tự xử lý {MAX_AUTOMATIC_IMAGES} ảnh đầu; các ảnh còn lại vẫn giữ trong References.</span>}
      </div>
      <div className="enhance-results">
        {selected.map((file, index) => {
          const result = entries[index];
          return (
            <div className="enhance-result" key={`${file.name}-${file.lastModified}-${index}`}>
              <div className="enhance-result-details">
                <strong title={file.name}>{file.name}</strong>
                <span className="enhance-status" role="status">
                  {result?.status === "ready" ? <><CheckCircle2 size={13}/> Raster enhanced · Review needed</>
                    : result?.status === "error" ? <><AlertTriangle size={13}/> Failed</>
                    : result?.status === "enhancing" ? "Cleaning image edges…"
                    : result?.status === "analyzing" ? "Detecting artwork…"
                    : engineOnline ? "Waiting…" : "Engine offline"}
                </span>
                {result?.route && (
                  <>
                    <span>Detected: <b>{CATEGORY_LABELS[result.route.category] ?? result.route.category}</b>
                      {" · "}confidence {Math.round(result.route.confidence * 100)}% (heuristic)</span>
                    <span>Pipeline: <b>{MODE_LABELS[result.route.mode] ?? result.route.mode}</b></span>
                    <span>{result.route.reasons.join(" · ")}</span>
                  </>
                )}
                {result?.error && <span role="alert" className="enhance-error">{result.error}</span>}
              </div>
              {result?.url && (
                <div className="enhance-output">
                  <img src={result.url} alt={`Enhanced preview of ${file.name}`} loading="lazy" />
                  <a href={result.url} download={`enhanced_${file.name.replace(/\.[^.]+$/, "")}.png`}
                    className="secondary-button"><Download size={14}/> Download draft PNG</a>
                </div>
              )}
            </div>
          );
        })}
      </div>
      <p className="enhance-disclaimer">
        Output là raster đã cleanup/upscale, không phải SVG hay bản được AI vẽ lại.
        Không tự xác nhận print-ready; kiểm tra chi tiết khi zoom và chạy Print QC trước khi in.
      </p>
    </section>
  );
}
