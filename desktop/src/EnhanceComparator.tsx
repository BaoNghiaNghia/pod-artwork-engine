import { useEffect, useRef, useState, type PointerEvent as ReactPointerEvent } from "react";
import "./enhance-comparator.css";
import {
  ArrowDownUp, Check, Expand, GripVertical, Image as ImageIcon,
  Layers2, Minimize2, Move, RotateCcw, ScanEye, ZoomIn, ZoomOut,
} from "lucide-react";

type CompareMode = "wipe" | "split" | "diff";
type Background = "checker" | "light" | "dark";
type Zoom = "fit" | 100 | 200 | 400;

const zoomOptions: Zoom[] = ["fit", 100, 200, 400];
const clamp = (n: number, low: number, high: number) => Math.max(low, Math.min(high, n));

function drawContained(ctx: CanvasRenderingContext2D, image: HTMLImageElement, width: number, height: number) {
  const ratio = Math.min(width / image.naturalWidth, height / image.naturalHeight);
  const w = Math.round(image.naturalWidth * ratio);
  const h = Math.round(image.naturalHeight * ratio);
  ctx.fillStyle = "#ffffff"; // consistent alpha compositing for pixel comparisons
  ctx.fillRect(0, 0, width, height);
  ctx.drawImage(image, (width - w) / 2, (height - h) / 2, w, h);
}

async function createDifference(before: string, after: string) {
  const load = async (src: string) => {
    const image = new Image();
    image.src = src;
    await image.decode();
    return image;
  };
  const [source, result] = await Promise.all([load(before), load(after)]);
  const width = Math.max(32, Math.round(512 * Math.min(1, source.naturalWidth / source.naturalHeight)));
  const height = Math.max(32, Math.round(512 * Math.min(1, source.naturalHeight / source.naturalWidth)));
  const a = document.createElement("canvas");
  const b = document.createElement("canvas");
  a.width = b.width = width;
  a.height = b.height = height;
  const ac = a.getContext("2d", { willReadFrequently: true });
  const bc = b.getContext("2d", { willReadFrequently: true });
  if (!ac || !bc) throw new Error("Image comparison unavailable");
  drawContained(ac, source, width, height);
  drawContained(bc, result, width, height);
  const first = ac.getImageData(0, 0, width, height);
  const second = bc.getImageData(0, 0, width, height);
  const heat = ac.createImageData(width, height);
  let total = 0, changed = 0;
  for (let i = 0; i < first.data.length; i += 4) {
    const d = (
      Math.abs(first.data[i] - second.data[i]) +
      Math.abs(first.data[i + 1] - second.data[i + 1]) +
      Math.abs(first.data[i + 2] - second.data[i + 2])
    ) / 3;
    total += d;
    if (d > 10) changed += 1;
    heat.data[i] = Math.round(Math.min(255, d * 5 + 12));
    heat.data[i + 1] = Math.round(Math.min(255, d * 1.7 + 15));
    heat.data[i + 2] = Math.round(Math.min(255, d * 0.9 + 39));
    heat.data[i + 3] = 255;
  }
  ac.putImageData(heat, 0, 0);
  return {
    url: a.toDataURL("image/png"),
    average: (total / (width * height * 255)) * 100,
    changed: (changed / (width * height)) * 100,
  };
}

export default function EnhanceComparator({
  before, after, name,
}: { before: string; after: string; name: string }) {
  const [mode, setMode] = useState<CompareMode>("wipe");
  const [zoom, setZoom] = useState<Zoom>("fit");
  const [background, setBackground] = useState<Background>("checker");
  const [divider, setDivider] = useState(50);
  const [expanded, setExpanded] = useState(false);
  const [originalSize, setOriginalSize] = useState({ width: 0, height: 0 });
  const [viewSize, setViewSize] = useState({ width: 0, height: 0 });
  const [pan, setPan] = useState({ x: 0, y: 0 });
  const [difference, setDifference] = useState<{
    url: string; average: number; changed: number;
  } | null>(null);
  const container = useRef<HTMLDivElement>(null);
  const gesture = useRef<{ type: "divider" | "pan"; x: number; y: number; panX: number; panY: number } | null>(null);

  useEffect(() => {
    if (!container.current) return;
    const observe = new ResizeObserver(([entry]) => {
      setViewSize({ width: entry.contentRect.width, height: entry.contentRect.height });
    });
    observe.observe(container.current);
    return () => observe.disconnect();
  }, [expanded, mode]);

  useEffect(() => {
    // Avoid decoding several 4500x5400 images just to show a normal
    // before/after view. The bounded difference pass runs on demand only.
    if (mode !== "diff") return;
    let active = true;
    setDifference(null);
    void createDifference(before, after).then((data) => {
      if (active) setDifference(data);
    }).catch(() => { if (active) setDifference(null); });
    return () => { active = false; };
  }, [before, after, mode]);

  useEffect(() => {
    if (!expanded) return;
    const handler = (event: KeyboardEvent) => {
      if (event.key === "Escape") setExpanded(false);
    };
    document.addEventListener("keydown", handler);
    return () => document.removeEventListener("keydown", handler);
  }, [expanded]);

  useEffect(() => { setPan({ x: 0, y: 0 }); }, [zoom, mode]);

  const paneWidth = mode === "split" ? viewSize.width / 2 : viewSize.width;
  const fit = originalSize.width && originalSize.height && paneWidth && viewSize.height
    ? Math.min(paneWidth / originalSize.width, viewSize.height / originalSize.height)
    : 1;
  // 100% corresponds to one CSS screen pixel per *original* source pixel.
  // Both before and after share this logical scale, even if AI doubled resolution.
  const scale = zoom === "fit" ? 1 : clamp((zoom / 100) / fit, 1, 48);
  const dimensions = originalSize.width ? {
    w: originalSize.width * fit * scale,
    h: originalSize.height * fit * scale,
  } : { w: paneWidth * scale, h: viewSize.height * scale };
  const maxX = Math.max(0, (dimensions.w - paneWidth) / 2);
  const maxY = Math.max(0, (dimensions.h - viewSize.height) / 2);
  const transform = `translate3d(${clamp(pan.x, -maxX, maxX)}px, ${clamp(pan.y, -maxY, maxY)}px, 0) scale(${scale})`;

  const pointerDown = (event: ReactPointerEvent<HTMLDivElement>) => {
    if (event.button !== 0 || mode === "diff") return;
    const rect = event.currentTarget.getBoundingClientRect();
    // At Fit, dragging anywhere moves the divider; zoomed in, drag to pan
    // except when grabbing the divider itself.
    const x = event.clientX - rect.left;
    const nearDivider = mode === "wipe" && Math.abs(x - rect.width * divider / 100) <= 26;
    const type = mode === "wipe" && (zoom === "fit" || nearDivider) ? "divider" : "pan";
    gesture.current = { type, x: event.clientX, y: event.clientY, panX: pan.x, panY: pan.y };
    event.currentTarget.setPointerCapture(event.pointerId);
    if (type === "divider") setDivider(clamp(Math.round(x / rect.width * 100), 0, 100));
  };

  const pointerMove = (event: ReactPointerEvent<HTMLDivElement>) => {
    const current = gesture.current;
    if (!current) return;
    if (current.type === "divider") {
      const rect = event.currentTarget.getBoundingClientRect();
      setDivider(clamp(Math.round((event.clientX - rect.left) / rect.width * 100), 0, 100));
    } else {
      setPan({ x: clamp(current.panX + event.clientX - current.x, -maxX, maxX),
        y: clamp(current.panY + event.clientY - current.y, -maxY, maxY) });
    }
  };
  const pointerUp = (event: ReactPointerEvent<HTMLDivElement>) => {
    if (event.currentTarget.hasPointerCapture(event.pointerId)) {
      event.currentTarget.releasePointerCapture(event.pointerId);
    }
    gesture.current = null;
  };

  const image = (src: string, alt: string, trackDimensions = false) => (
    <img className="enhance-inspect-image" src={src} alt={alt} draggable={false}
      style={{ transform }}
      onLoad={trackDimensions ? (event) => {
        setOriginalSize({
          width: event.currentTarget.naturalWidth,
          height: event.currentTarget.naturalHeight,
        });
      } : undefined}
    />
  );

  return (
    <section className={`enhance-inspector ${expanded ? "is-expanded" : ""}`}
      role="region" aria-label="Trình xem Before/After">
      <div className="enhance-inspector-topbar">
        <div className="enhance-inspector-head">
          <span className="enhance-inspector-mark"><ScanEye size={17}/></span>
          <div><strong>Kiểm tra chi tiết Before / After</strong>
            <small>Kéo đường chia hoặc zoom để kiểm tra từng nét vẽ</small></div>
        </div>
        <button className="enhance-inspector-icon" type="button"
          aria-label={expanded ? "Thu nhỏ trình xem" : "Phóng to trình xem"}
          title={expanded ? "Thoát chế độ toàn màn hình (Esc)" : "Mở toàn màn hình"}
          onClick={() => setExpanded(!expanded)}>
          {expanded ? <Minimize2 size={17}/> : <Expand size={17}/>}
        </button>
      </div>

      <div className="enhance-inspector-tools">
        <div className="enhance-segments" role="group" aria-label="Kiểu so sánh">
          <button type="button" className={mode === "wipe" ? "active" : ""}
            aria-pressed={mode === "wipe"} onClick={() => setMode("wipe")}>
            <GripVertical size={14}/> So sánh kéo
          </button>
          <button type="button" className={mode === "split" ? "active" : ""}
            aria-pressed={mode === "split"} onClick={() => setMode("split")}>
            <Layers2 size={14}/> Song song
          </button>
          <button type="button" className={mode === "diff" ? "active" : ""}
            aria-pressed={mode === "diff"} onClick={() => setMode("diff")}>
            <ScanEye size={14}/> Khác biệt
          </button>
        </div>
        <div className="enhance-inspect-zoom" role="group" aria-label="Độ phóng đại">
          <ZoomOut size={15} aria-hidden="true"/>
          {zoomOptions.map(value => (
            <button type="button" className={zoom === value ? "active" : ""}
              aria-pressed={zoom === value} key={value}
              onClick={() => setZoom(value)}>
              {value === "fit" ? "Vừa khung" : `${value}%`}
            </button>
          ))}
          <ZoomIn size={15} aria-hidden="true"/>
        </div>
        <div className="enhance-inspect-bg" role="group" aria-label="Màu nền xem ảnh">
          {(["checker", "light", "dark"] as const).map(value => (
            <button key={value} type="button"
              aria-label={value === "checker" ? "Nền caro" : value === "light" ? "Nền trắng" : "Nền tối"}
              title={value === "checker" ? "Nền caro" : value === "light" ? "Nền trắng" : "Nền tối"}
              aria-pressed={background === value}
              className={`${value} ${background === value ? "active" : ""}`}
              onClick={() => setBackground(value)}>
              {background === value && <Check size={12}/>}
            </button>
          ))}
        </div>
        <button type="button" className="enhance-inspect-reset"
          title="Khôi phục vị trí ảnh và thanh chia" aria-label="Đặt lại góc nhìn"
          onClick={() => { setZoom("fit"); setPan({ x: 0, y: 0 }); setDivider(50); }}>
          <RotateCcw size={15}/>
        </button>
      </div>

      <div className={`enhance-inspect-viewport ${background} ${mode}`}
        ref={container}
        onPointerDown={pointerDown} onPointerMove={pointerMove}
        onPointerUp={pointerUp} onPointerCancel={pointerUp}
        onDoubleClick={() => setZoom((old) => old === "fit" ? 100 : "fit")}>
        {mode === "wipe" && (
          <>
            <div className="enhance-inspect-layer">{image(before, `Original: ${name}`, true)}</div>
            <div className="enhance-inspect-layer" style={{ clipPath: `inset(0 0 0 ${divider}%)` }}>
              {image(after, `Enhanced: ${name}`)}
            </div>
            <span className="enhance-inspect-tag before">BEFORE</span>
            <span className="enhance-inspect-tag after">AFTER</span>
            <div className="enhance-inspect-divider" style={{ left: `${divider}%` }}>
              <span className="enhance-inspect-handle"><ArrowDownUp size={18}/></span>
            </div>
          </>
        )}
        {mode === "split" && (
          <div className="enhance-inspect-two">
            <div className="enhance-inspect-half">
              <span className="enhance-inspect-tag before">BEFORE</span>
              {image(before, `Original: ${name}`, true)}
            </div>
            <div className="enhance-inspect-half">
              <span className="enhance-inspect-tag after">AFTER</span>
              {image(after, `Enhanced: ${name}`)}
            </div>
          </div>
        )}
        {mode === "diff" && (
          <div className="enhance-inspect-layer enhance-inspect-heatmap">
            {difference ? <img className="enhance-inspect-image" src={difference.url}
              alt="Bản đồ khác biệt pixel" draggable={false}
              style={{ transform }}/> : <span>Đang tạo bản đồ khác biệt…</span>}
          </div>
        )}
      </div>

      <div className="enhance-inspect-bottom">
        {mode === "wipe" ? (
          <label className="enhance-inspect-slider">
            <span>Before</span>
            <input type="range" min={0} max={100} value={divider}
              aria-label="Vị trí thanh Before/After"
              onChange={(event) => setDivider(Number(event.target.value))}/>
            <span>After</span>
          </label>
        ) : mode === "diff" ? (
          <div className="enhance-inspect-diffstat" role="status">
            {difference ? (
              <>
                <strong>Thay đổi pixel: {difference.average.toFixed(2)}%</strong>
                <span>{difference.changed.toFixed(1)}% điểm ảnh thay đổi rõ rệt ·
                  {difference.average < 0.5 ? " Khác biệt rất ít" :
                    difference.average < 2 ? " Khác biệt nhẹ" : " Có khác biệt nhìn thấy"}
                </span>
              </>
            ) : <span>Đang phân tích…</span>}
          </div>
        ) : <span className="enhance-inspect-hint"><Move size={14}/> Kéo để di chuyển hai ảnh đồng bộ khi zoom</span>}
        <span className="enhance-inspect-meta">
          {originalSize.width > 0 ? `${originalSize.width} × ${originalSize.height} nguồn` : "Đang đọc kích thước"}
          {" · "}{zoom === "fit" ? "Fit" : `${zoom}% source pixels`}
        </span>
      </div>
      {mode === "diff" && <p className="enhance-inspect-footnote">
        Màu sáng chỉ vùng pixel khác nhau, không đồng nghĩa chất lượng đã tốt hơn.
        Chỉ số tính trên ảnh xem trước thu nhỏ để tránh tốn RAM.
      </p>}
    </section>
  );
}
