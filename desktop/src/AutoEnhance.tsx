import { useEffect, useMemo, useState } from "react";
import {
  AlertTriangle, ArrowRight, CheckCircle2, Clock3, Download, FileImage,
  ImageIcon, RefreshCw, ScanSearch, Settings2, ShieldCheck, Sparkles,
} from "lucide-react";
import { Button } from "@/components/ui/button";

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
type Entry = {
  name: string;
  status: "analyzing" | "enhancing" | "ready" | "error";
  route?: Route;
  url?: string;
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
  const [retry, setRetry] = useState(0);
  const [entries, setEntries] = useState<Entry[]>([]);
  const selected = files.slice(0, MAX_AUTOMATIC_IMAGES);
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
    const batch = files.slice(0, MAX_AUTOMATIC_IMAGES);
    setEntries(batch.map((file) => ({ name: file.name, status: "analyzing" })));
    const update = (index: number, patch: Partial<Entry>) => {
      if (!abort.signal.aborted) {
        setEntries((previous) => previous.map((entry, i) =>
          i === index ? { ...entry, ...patch } : entry));
      }
    };

    // Serial processing bounds memory when several 50 MB images are selected.
    void (async () => {
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

          const upload = new FormData();
          upload.append("file", file);
          const processed = await fetch(
            `${API}/enhance/run?mode=${encodeURIComponent(override)}`,
            { method: "POST", body: upload, signal: abort.signal },
          );
          if (!processed.ok) throw new Error(await readFailure(processed));
          const blob = await processed.blob();
          if (abort.signal.aborted) return;
          const url = URL.createObjectURL(blob);
          outputUrls.push(url);
          update(i, {
            status: "ready", url,
            route: { ...route, mode: processed.headers.get("X-Enhance-Mode") || route.mode },
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
  }, [files, engineOnline, override, retry]);

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

      {files.length > MAX_AUTOMATIC_IMAGES && (
        <div className="enhance-batch-note" role="note">
          Tự xử lý {MAX_AUTOMATIC_IMAGES} ảnh đầu tiên; các ảnh còn lại vẫn được giữ trong References.
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

              <div className="enhance-compare">
                <div className="enhance-compare-pane">
                  <div className="enhance-compare-title"><strong>Ảnh gốc</strong><span>Original</span></div>
                  <div className="enhance-image-frame">
                    <img src={sourceUrls[index]} alt={`Original: ${file.name}`} loading="lazy"/>
                  </div>
                </div>
                <ArrowRight className="enhance-compare-arrow" size={19} aria-hidden="true"/>
                <div className="enhance-compare-pane">
                  <div className="enhance-compare-title">
                    <strong>Ảnh sau xử lý</strong><span>Raster draft</span>
                  </div>
                  <div className="enhance-image-frame">
                    {result?.url ? (
                      <img src={result.url} alt={`Enhanced: ${file.name}`} loading="lazy"/>
                    ) : (
                      <div className="enhance-image-placeholder">
                        {result?.status === "error" ? <AlertTriangle size={25}/> :
                          active ? <RefreshCw size={25}/> : <ImageIcon size={25}/>}
                        <span>{result?.status === "error" ? "Chưa thể xử lý" :
                          active ? "Đang xử lý ảnh…" : "Chờ Engine kết nối"}</span>
                      </div>
                    )}
                  </div>
                </div>
              </div>

              <div className="enhance-result-footer">
                <div className="enhance-detection">
                  {result?.route ? (
                    <>
                      <span className="enhance-category">
                        <ScanSearch size={13}/> {CATEGORY_LABELS[result.route.category] || result.route.category}
                      </span>
                      <span>Confidence: <strong>{Math.round(result.route.confidence * 100)}%</strong></span>
                      <span>Pipeline: <strong>{MODE_LABELS[result.route.mode] || result.route.mode}</strong></span>
                      <small>{result.route.reasons.join(" · ")}</small>
                    </>
                  ) : <span>Hệ thống tự nhận diện nhóm và chọn bộ xử lý phù hợp.</span>}
                </div>
                {result?.url && (
                  <a className="enhance-download" href={result.url}
                    download={`enhanced_${file.name.replace(/\.[^.]+$/, "")}.png`}>
                    <Download size={15}/> Download draft PNG
                  </a>
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
        Đây là bản raster cleanup/upscale, không phải ảnh AI redraw hoặc SVG.
        Kết quả chưa được duyệt chuẩn in; kiểm tra chi tiết trước khi dùng cho POD.
      </p>
    </section>
  );
}
