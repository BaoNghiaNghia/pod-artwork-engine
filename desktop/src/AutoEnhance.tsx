import { useEffect, useMemo, useState } from "react";
import {
  AlertTriangle, ArrowRight, CheckCircle2, Clock3, Download, FileImage,
  ImageIcon, RefreshCw, ScanSearch, Settings2, ShieldCheck, Sparkles,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import EnhanceComparator from "./EnhanceComparator";

const API = "http://127.0.0.1:8765";
const MAX_AUTOMATIC_IMAGES = 6;
const MAX_UPLOAD_BYTES = 50 * 1024 * 1024;

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
type RuntimeReport = {
  vtracer: { installed: boolean };
  realesrgan: { installed: boolean; model_names: string[] };
};

type QualityProfile = "fast" | "balanced" | "max_quality";
type Candidate = {
  id: string;
  url: string;
  engine: string;
  score: number;
  edgeGain: number;
  alphaDrift: number;
  operation?: string;
  tileCount?: number;
  fallbackReason?: string;
};
const PROFILE_VARIANTS: Record<QualityProfile, string[]> = {
  fast: ["standard"], balanced: ["standard", "detail"],
  max_quality: ["standard", "detail", "texture"],
};
const VARIANT_LABELS: Record<string, string> = {
  standard: "Natural Clean", detail: "Sharp / AI Detail", texture: "Texture Preserve",
  vector: "SVG Vector",
};

type Entry = {
  name: string;
  status: "analyzing" | "enhancing" | "ready" | "error";
  route?: Route;
  url?: string;
  candidates?: Candidate[];
  selectedVariant?: string;
  svgUrl?: string;
  engine?: string;
  fallbackReason?: string;
  vectorWarning?: string;
  error?: string;
};

const MODE_LABELS: Record<string, string> = {
  auto: "Automatic (recommended)",
  typography_clean: "Typography / chữ",
  vector_clean: "Logo / hình phẳng",
  sticker_clean: "Sticker / Cartoon",
  photo_cutout: "Photo / Cutout",
  painterly_clean: "Painterly / Brush",
  metal_preserve: "Metal / Chrome",
  dark_rescue: "Dark Contrast",
  gentle_clean: "Gentle Clean",
};
const CATEGORY_LABELS: Record<string, string> = {
  typography: "Typography",
  logo_vector: "Logo / flat shape",
  sticker_cartoon: "Sticker / Cartoon",
  photo_cutout: "Photo / Cutout",
  painterly_textured: "Painterly / Texture",
  metal_gloss_effect: "Metal / Chrome",
  dark_low_contrast: "Dark / Low Contrast",
  mixed: "Mixed / uncertain",
};
const bytesLabel = (size: number) => size >= 1024 * 1024
  ? `${(size / (1024 * 1024)).toFixed(1)} MB`
  : `${Math.max(1, Math.round(size / 1024))} KB`;

async function readFailure(response: Response): Promise<string> {
  const body = await response.json().catch(() => ({}));
  return typeof body.detail === "string"
    ? body.detail : `Processing failed (HTTP ${response.status})`;
}

/** Upload -> classify -> enhance. Previews live only in the current browser session. */
export default function AutoEnhance({ files, engineOnline }: { files: File[]; engineOnline: boolean }) {
  const [override, setOverride] = useState("auto");
  const [profile, setProfile] = useState<QualityProfile>("max_quality");
  const [retry, setRetry] = useState(0);
  const [entries, setEntries] = useState<Entry[]>([]);
  const [runtime, setRuntime] = useState<RuntimeReport | null>(null);
  const batchLimit = profile === "max_quality" ? 2 : profile === "balanced" ? 3 : MAX_AUTOMATIC_IMAGES;
  const selected = files.slice(0, batchLimit);
  const sourceUrls = useMemo(
    () => files.slice(0, MAX_AUTOMATIC_IMAGES).map((file) => URL.createObjectURL(file)),
    [files],
  );

  useEffect(() => () => {
    sourceUrls.forEach(URL.revokeObjectURL);
  }, [sourceUrls]);

  useEffect(() => {
    if (!engineOnline || files.length === 0) {
      setEntries([]);
      return;
    }
    const abort = new AbortController();
    const outputUrls: string[] = [];
    const batch = files.slice(0, batchLimit);
    setEntries(batch.map((file) => ({ name: file.name, status: "analyzing" })));
    const update = (index: number, patch: Partial<Entry>) => {
      if (!abort.signal.aborted) {
        setEntries((previous) => previous.map((entry, i) =>
          i === index ? { ...entry, ...patch } : entry));
      }
    };

    // Serial processing bounds memory when several 50 MB images are selected.
    void (async () => {
      let capabilities: RuntimeReport | null = null;
      try {
        const status = await fetch(`${API}/enhance/capabilities`, { signal: abort.signal });
        if (status.ok) {
          capabilities = await status.json() as RuntimeReport;
          if (!abort.signal.aborted) setRuntime(capabilities);
        }
      } catch {
        if (!abort.signal.aborted) setRuntime(null);
      }
      for (let i = 0; i < batch.length; i++) {
        if (abort.signal.aborted) return;
        const file = batch[i];
        if (file.size > MAX_UPLOAD_BYTES) {
          update(i, { status: "error", error: "File vượt quá 50 MB. Chọn ảnh tối đa 50 MB." });
          continue;
        }
        try {
          const data = new FormData();
          data.append("file", file);
          const analyzed = await fetch(`${API}/enhance/analyze`, {
            method: "POST", body: data, signal: abort.signal,
          });
          if (!analyzed.ok) throw new Error(await readFailure(analyzed));
          const route = await analyzed.json() as Route;
          update(i, { route, status: "enhancing" });

          let actualMode = route.mode;
          let actualEngine = "pillow-fallback";
          let fallbackReason: string | undefined;
          const candidates: Candidate[] = [];
          for (const variant of PROFILE_VARIANTS[profile]) {
            if (abort.signal.aborted) return;
            const upload = new FormData();
            upload.append("file", file);
            const processed = await fetch(
              `${API}/enhance/run?mode=${encodeURIComponent(override)}&profile=${profile}&variant=${variant}`,
              { method: "POST", body: upload, signal: abort.signal },
            );
            if (!processed.ok) throw new Error(await readFailure(processed));
            const blob = await processed.blob();
            if (abort.signal.aborted) return;
            const url = URL.createObjectURL(blob);
            outputUrls.push(url);
            actualMode = processed.headers.get("X-Enhance-Mode") || route.mode;
            actualEngine = processed.headers.get("X-Enhance-Engine") || "pillow-fallback";
            fallbackReason = processed.headers.get("X-Enhance-Fallback-Reason") || undefined;
            candidates.push({
              id: variant, url, engine: actualEngine,
              score: Number(processed.headers.get("X-Enhance-Technical-Score") || 0),
              edgeGain: Number(processed.headers.get("X-Enhance-Edge-Gain") || 0),
              alphaDrift: Number(processed.headers.get("X-Enhance-Alpha-Drift") || 0),
              operation: processed.headers.get("X-Enhance-Operation") || undefined,
              tileCount: Number(processed.headers.get("X-Enhance-Tile-Count") || 0),
              fallbackReason,
            });
            // A detail candidate is shown by default when available; technical
            // heuristics alone must never silently approve a print candidate.
            const prefer = candidates.find((item) => item.id === "detail") || candidates[0];
            update(i, {
              url: prefer.url, selectedVariant: prefer.id,
              candidates: [...candidates], engine: prefer.engine, fallbackReason: prefer.fallbackReason,
              route: { ...route, mode: actualMode }, status: "enhancing",
            });
          }
          const selectedCandidate = candidates.find((item) => item.id === "detail") || candidates[0];
          let svgUrl: string | undefined;
          let vectorWarning: string | undefined;
          if (capabilities?.vtracer?.installed &&
              ["typography_clean", "vector_clean", "sticker_clean"].includes(actualMode)) {
            try {
              const vectorData = new FormData();
              vectorData.append("file", file);
              const vector = await fetch(
                `${API}/enhance/vector?mode=${encodeURIComponent(actualMode)}&profile=${profile}`,
                { method: "POST", body: vectorData, signal: abort.signal },
              );
              if (!vector.ok) throw new Error(await readFailure(vector));
              const svgBlob = await vector.blob();
              if (abort.signal.aborted) return;
              svgUrl = URL.createObjectURL(svgBlob);
              outputUrls.push(svgUrl);
              candidates.push({
                id: "vector", url: svgUrl, engine: "vtracer",
                score: 0, edgeGain: 0, alphaDrift: 0,
              });
            } catch (cause) {
              if (abort.signal.aborted) return;
              vectorWarning = cause instanceof Error ? cause.message : "SVG export unavailable";
            }
          }
          update(i, {
            status: "ready", url: selectedCandidate.url, svgUrl, vectorWarning,
            selectedVariant: selectedCandidate.id, candidates,
            engine: selectedCandidate.engine, fallbackReason: selectedCandidate.fallbackReason,
            route: { ...route, mode: actualMode },
          });
        } catch (error) {
          if (abort.signal.aborted) return;
          update(i, {
            status: "error",
            error: error instanceof Error ? error.message : "Enhance failed",
          });
        }
      }
    })();

    return () => {
      abort.abort();
      outputUrls.forEach(URL.revokeObjectURL);
    };
  }, [files, engineOnline, override, profile, retry, batchLimit]);

  const updateCandidate = (index: number, candidate: Candidate) => {
    setEntries((previous) => previous.map((entry, i) => i !== index ? entry : ({
      ...entry, url: candidate.url, selectedVariant: candidate.id, engine: candidate.engine,
      fallbackReason: candidate.fallbackReason,
    })));
  };
  if (!files.length) return null;
  const completed = entries.filter((entry) => entry.status === "ready").length;
  const failed = entries.filter((entry) => entry.status === "error").length;
  const working = entries.some((entry) =>
    entry.status === "enhancing" || entry.status === "analyzing");
  return (
    <section className="enhance-panel" aria-label="Auto Enhance Artwork">
      <div className="enhance-heading">
        <div className="enhance-heading-main">
          <div className="enhance-heading-icon"><Sparkles size={21} aria-hidden="true"/></div>
          <div>
            <div className="eyebrow">AUTOMATIC IMAGE REFINEMENT</div>
            <h3>Auto Enhance Artwork</h3>
            <p>Tự phân loại ảnh, chọn cách làm nét và hiển thị kết quả để bạn so sánh.</p>
          </div>
        </div>
        <div className="enhance-heading-meta">
          {runtime && <span className="enhance-limit subtle">
            Real-ESRGAN {runtime.realesrgan.installed ? "ready" : "unavailable"}
            {" · "}VTracer {runtime.vtracer.installed ? "ready" : "unavailable"}
          </span>}
          <span className="enhance-limit"><FileImage size={14}/> 50 MB / file</span>
          <span className="enhance-limit subtle">Max 30 MP input</span>
        </div>
      </div>

      <div className="enhance-flow" aria-label="Processing steps">
        <span><ScanSearch size={15}/> 1. Detect type</span>
        <ArrowRight size={14} aria-hidden="true"/>
        <span><Settings2 size={15}/> 2. Enhance</span>
        <ArrowRight size={14} aria-hidden="true"/>
        <span><ShieldCheck size={15}/> 3. Review result</span>
      </div>

      <div className="enhance-profile-panel" role="group" aria-label="Chất lượng xử lý">
        <div className="enhance-profile-title"><Sparkles size={15}/> Processing Quality
          <span>Có thể xử lý lâu hơn để tạo nhiều phương án</span>
        </div>
        <div className="enhance-profile-options">
          {([
            ["fast", "Fast", "1 phương án · xem nhanh"],
            ["balanced", "Balanced", "2 phương án · so sánh"],
            ["max_quality", "Max Quality", "3 phương án · ưu tiên chất lượng"],
          ] as const).map(([value, label, description]) => (
            <button key={value} type="button" aria-pressed={profile === value}
              className={profile === value ? "selected" : ""}
              onClick={() => setProfile(value)}>
              <strong>{label}</strong><small>{description}</small>
            </button>
          ))}
        </div>
      </div>

      <div className="enhance-toolbar">
        <div className="enhance-toolbar-control">
          <label htmlFor="enhance-route">Chế độ xử lý</label>
          <select id="enhance-route" value={override}
            onChange={(event) => setOverride(event.target.value)}>
            {Object.entries(MODE_LABELS).map(([value, label]) => (
              <option key={value} value={value}>{label}</option>
            ))}
          </select>
        </div>
        <span className="enhance-tally" role="status">
          {working ? <Clock3 size={15}/> : <CheckCircle2 size={15}/>}
          {engineOnline
            ? `${completed}/${selected.length} hoàn thành${failed ? ` · ${failed} lỗi` : ""}`
            : "Engine offline"}
        </span>
        <Button type="button" size="sm" variant="outline"
          disabled={!engineOnline || working}
          onClick={() => setRetry((value) => value + 1)}>
          <RefreshCw size={14}/> Chạy lại
        </Button>
      </div>

      {files.length > batchLimit && (
        <div className="enhance-batch-note" role="note">
          Chế độ {profile === "max_quality" ? "Max Quality" : profile === "balanced" ? "Balanced" : "Fast"}
          {" "}xử lý {batchLimit} ảnh đầu để giới hạn RAM/VRAM; ảnh còn lại vẫn nằm trong References.
        </div>
      )}

      <div className="enhance-results">
        {selected.map((file, index) => {
          const result = entries[index];
          const active = result?.status === "analyzing" || result?.status === "enhancing";
          return (
            <article className="enhance-result" key={`${file.name}-${file.lastModified}-${index}`}>
              <div className="enhance-result-header">
                <span className="enhance-file-name" title={file.name}>
                  <FileImage size={16} aria-hidden="true"/>
                  <strong>{file.name}</strong>
                  <small>{bytesLabel(file.size)}</small>
                </span>
                <span className={`enhance-status ${result?.status || "pending"}`} role="status">
                  {result?.status === "ready"
                    ? <><CheckCircle2 size={14}/> Hoàn thành · Cần duyệt</>
                    : result?.status === "error"
                      ? <><AlertTriangle size={14}/> Có lỗi</>
                      : active
                        ? <><RefreshCw size={14}/> {result?.status === "analyzing" ? "Đang phân loại" : "Đang làm nét"}</>
                        : engineOnline ? "Đang chờ" : "Engine offline"}
                </span>
              </div>

              {result?.candidates?.length ? (
                <>
                  <div className="enhance-candidate-strip" aria-label="Các phương án xử lý">
                    {result.candidates.map((candidate) => (
                      <button type="button" key={candidate.id}
                        aria-pressed={result.selectedVariant === candidate.id}
                        className={result.selectedVariant === candidate.id ? "selected" : ""}
                        onClick={() => updateCandidate(index, candidate)}>
                        <img src={candidate.url} alt={`Preview ${VARIANT_LABELS[candidate.id]}`} loading="lazy"/>
                        <span><strong>{VARIANT_LABELS[candidate.id]}</strong>
                          <small>{candidate.engine === "vtracer" ? "True vector · không chấm điểm raster" :
                            candidate.operation === "hq-tiled-ai-detail-refine" ? `Tiled AI · ${candidate.tileCount} tiles` :
                            candidate.engine === "real-esrgan-ncnn-vulkan" ? "Real-ESRGAN AI" : "Local raster"}
                            {candidate.engine !== "vtracer" && ` · Score ${candidate.score.toFixed(1)}`}</small>
                        </span>
                      </button>
                    ))}
                  </div>
                  <EnhanceComparator before={sourceUrls[index]} after={result.url || result.candidates[0].url} name={file.name}/>
                  <div className="enhance-quality-note">
                    {result.selectedVariant === "vector" ? (
                      <strong>SVG Vector · đường nét phóng to không giới hạn</strong>
                    ) : (
                      <strong>Technical Score: {(result.candidates.find(c => c.id === result.selectedVariant) || result.candidates[0]).score.toFixed(1)}/100</strong>
                    )}
                    <span>Điểm kỹ thuật chỉ áp dụng raster; không chứng minh chữ đúng hay ảnh đạt chuẩn in.</span>
                    {result.candidates.find(c => c.id === result.selectedVariant)?.operation === "hq-tiled-ai-detail-refine" && (
                      <span>GPU đã tái tạo chi tiết theo từng tile và ghép về đúng kích thước canvas nguồn; đây không phải ảnh upscale 2×.</span>
                    )}
                  </div>
                </>
              ) : (
                <div className="enhance-awaiting-comparison">
                  <ImageIcon size={27}/>
                  <strong>{result?.status === "error" ? "Không thể so sánh" :
                    active ? "Đang phân tích và xử lý ảnh" : "Đang đợi Engine"}</strong>
                  <span>{result?.error || "Trình xem Before/After sẽ mở ngay sau khi xử lý xong."}</span>
                </div>
              )}

              <div className="enhance-result-footer">
                <div className="enhance-detection">
                  {result?.route ? (
                    <>
                      <span className="enhance-category">
                        <ScanSearch size={13}/> {CATEGORY_LABELS[result.route.category] || result.route.category}
                      </span>
                      <span>Confidence: <strong>{Math.round(result.route.confidence * 100)}%</strong></span>
                      <span>Pipeline: <strong>{MODE_LABELS[result.route.mode] || result.route.mode}</strong></span>
                      <span>Engine: <strong>{result.engine === "vtracer" ? "VTracer Vector" : result.engine === "real-esrgan-ncnn-vulkan" ? "Real-ESRGAN AI" : result.engine === "pillow-hq" ? "HQ raster" : "Raster fallback"}</strong></span>
                      {result.svgUrl && <span>SVG: <strong>VTracer</strong></span>}
                      {result.svgUrl && (
                        <small>Với artwork phẳng, PNG có thể trông gần như ảnh gốc. Hãy dùng SVG để kiểm tra đường nét vector khi zoom lớn.</small>
                      )}
                      {result.fallbackReason && <small>Fallback: {result.fallbackReason}</small>}
                      <small>{result.route.reasons.join(" · ")}</small>
                    </>
                  ) : <span>Hệ thống tự nhận diện nhóm và chọn bộ xử lý phù hợp.</span>}
                </div>
                {result?.svgUrl && (
                  <a className="enhance-download" href={result.svgUrl}
                    download={`vector_${file.name.replace(/\.[^.]+$/, "")}.svg`}>
                    <Download size={15}/> Download SVG
                  </a>
                )}
                {result?.url && (
                  <a className="enhance-download" href={result.candidates?.find(c => c.id === result.selectedVariant && c.id !== "vector")?.url ||
                    result.candidates?.find(c => c.id !== "vector")?.url || result.url}
                    download={`enhanced_${file.name.replace(/\.[^.]+$/, "")}.png`}>
                    <Download size={15}/> Download draft PNG
                  </a>
                )}
                {result?.vectorWarning && (
                  <div className="enhance-error" role="status">Vector: {result.vectorWarning}</div>
                )}
                {result?.error && (
                  <div className="enhance-error" role="alert">{result.error}</div>
                )}
              </div>
            </article>
          );
        })}
      </div>

      <p className="enhance-disclaimer">
        <ShieldCheck size={15} aria-hidden="true"/>
        Ảnh phẳng có thể xuất SVG bằng VTracer; ảnh phù hợp có thể được upscale bằng Real-ESRGAN.
        Các ảnh khác dùng fallback raster. Tất cả đầu ra đều cần duyệt và kiểm tra Print QC trước khi in.
      </p>
    </section>
  );
}
