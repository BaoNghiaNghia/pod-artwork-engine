import { useEffect, useMemo, useState } from "react";
import { audit } from "./system";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { Badge } from "@/components/ui/badge";
import { ImagePlus, Send, Settings2, ShieldCheck, Download, CheckCircle2 } from "lucide-react";

const API = "http://127.0.0.1:8765";
const MAX_UPLOAD_BYTES = 50 * 1024 * 1024;
const LAST_CHAT_KEY = "pod-artwork-last-image-chat";

type ArtworkJob = { job_id: string; state: string };
type Version = {
  version_id: string;
  base_version_id: string | null;
  prompt: string;
  state: "running" | "completed" | "failed";
  created_at: string;
  model: string;
  error: string | null;
  print_ready: false;
};
type ImageConfig = { configured: boolean; model: string; provider: string; key_persistence: string };

export default function AIChat({
  engineOnline, inputFiles, artworkJob, onPrintCheck, onOpenSettings
}: {
  engineOnline: boolean;
  inputFiles: File[];
  artworkJob: ArtworkJob | null;
  onPrintCheck: (job: ArtworkJob) => void;
  onOpenSettings: () => void;
}) {
  const [config, setConfig] = useState<ImageConfig | null>(null);
  const [usingArtwork, setUsingArtwork] = useState(Boolean(artworkJob));
  const [chatJobId, setChatJobId] = useState(() => window.localStorage.getItem(LAST_CHAT_KEY) || "");
  const [attached, setAttached] = useState<File[]>([]);
  const [versions, setVersions] = useState<Version[]>([]);
  const [baseVersion, setBaseVersion] = useState<string | null>(null);
  const [prompt, setPrompt] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");

  const activeJobId = usingArtwork && artworkJob ? artworkJob.job_id : chatJobId;
  const selected = useMemo(() => versions.find((v) => v.version_id === baseVersion), [versions, baseVersion]);
  const running = versions.some((v) => v.state === "running");
  const visibleVersion = selected?.state === "completed" ? selected : [...versions].reverse().find((v) => v.state === "completed");
  const previewUrl = visibleVersion
    ? `${API}/jobs/${activeJobId}/image-chat/${visibleVersion.version_id}/image`
    : activeJobId ? `${API}/jobs/${activeJobId}/image-chat/source` : "";

  useEffect(() => {
    if (!engineOnline) return;
    let cancelled = false;
    fetch(`${API}/image-chat/config`)
      .then(async (r) => { if (!r.ok) throw new Error("Cannot load image AI settings"); return await r.json() as ImageConfig; })
      .then((value) => { if (!cancelled) setConfig(value); })
      .catch((err) => { if (!cancelled) setError(String(err)); });
    return () => { cancelled = true; };
  }, [engineOnline]);

  useEffect(() => {
    setVersions([]); setBaseVersion(null); setError(""); setNotice("");
    if (!engineOnline || !activeJobId) return;
    let cancelled = false;
    const refresh = async () => {
      try {
        const response = await fetch(`${API}/jobs/${activeJobId}/image-chat`);
        if (!response.ok) throw new Error("Chat session not found");
        const list = (await response.json()) as Version[];
        if (!cancelled) setVersions(list);
      } catch (err) {
        if (!cancelled) setError(err instanceof Error ? err.message : String(err));
      }
    };
    void refresh();
    const timer = window.setInterval(() => { void refresh(); }, 2500);
    return () => { cancelled = true; window.clearInterval(timer); };
  }, [activeJobId, engineOnline]);

  useEffect(() => {
    if (chatJobId) window.localStorage.setItem(LAST_CHAT_KEY, chatJobId);
  }, [chatJobId]);

  const responseError = async (response: Response) => {
    const result = await response.json().catch(() => ({}));
    return typeof result.detail === "string" ? result.detail : `HTTP ${response.status}`;
  };

  const createSession = async () => {
    const images = attached.length ? attached : inputFiles;
    if (!images.length || busy) return;
    const oversized = images.find((file) => file.size > MAX_UPLOAD_BYTES);
    if (oversized) {
      setError(`Ảnh "${oversized.name}" vượt 50 MB. Vui lòng chọn ảnh nhỏ hơn.`);
      return;
    }
    setBusy(true); setError(""); setNotice("");
    try {
      const body = new FormData();
      images.forEach((image) => body.append("files", image));
      const response = await fetch(`${API}/image-chat/sessions`, { method:"POST", body });
      if (!response.ok) throw new Error(await responseError(response));
      const result = (await response.json()) as ArtworkJob;
      setUsingArtwork(false); setChatJobId(result.job_id); setVersions([]); setBaseVersion(null);
      audit("chat_session_created", "chat", result.job_id);
      setNotice("Đã mở phiên chỉnh sửa. Nhập mô tả bạn muốn thay đổi.");
    } catch (err) { setError(err instanceof Error ? err.message : String(err)); }
    finally { setBusy(false); }
  };

  const send = async () => {
    if (!activeJobId || !prompt.trim() || running || busy || !config?.configured) return;
    setBusy(true); setError(""); setNotice("");
    try {
      const response = await fetch(`${API}/jobs/${activeJobId}/image-chat`, {
        method: "POST", headers: {"Content-Type":"application/json"},
        body: JSON.stringify({prompt:prompt.trim(), base_version_id:baseVersion})
      });
      if (!response.ok) throw new Error(await responseError(response));
      const result = (await response.json()) as Version;
      setVersions((items) => [...items, result]);
      audit("chat_edit_submitted", "chat", activeJobId);
      setPrompt(""); setBaseVersion(result.version_id);
    } catch (err) { setError(err instanceof Error ? err.message : String(err)); }
    finally { setBusy(false); }
  };

  const printCheck = async (versionId: string) => {
    if (!activeJobId || busy) return;
    setBusy(true); setError("");
    try {
      const response = await fetch(`${API}/jobs/${activeJobId}/image-chat/${versionId}/print-check`, {method:"POST"});
      if (!response.ok) throw new Error(await responseError(response));
      onPrintCheck(await response.json() as ArtworkJob);
      audit("chat_print_check", "chat", activeJobId);
    } catch (err) { setError(err instanceof Error ? err.message : String(err)); }
    finally { setBusy(false); }
  };

  return (
    <section className="image-chat">
      <div className="image-chat-heading">
        <div>
          <div className="eyebrow">ARTWORK IMAGE STUDIO</div>
          <h2>AI Image Studio</h2>
          <p>Chỉnh sửa bằng ngôn ngữ tự nhiên, lưu mọi phiên bản.</p>
        </div>
        <Badge variant={config?.configured ? "secondary" : "destructive"} className={config?.configured ? "chat-connected" : "chat-disconnected"}>
          {config?.configured ? "Image AI ready" : "Chưa kết nối"}
        </Badge>
      </div>

      <div className="chat-settings-summary">
        <span><Settings2 size={14}/> {config?.configured
          ? `Image AI ready · ${config.model}`
          : "Image AI chưa kết nối · cấu hình trong AI Settings"}</span>
        <Button type="button" variant="outline" size="sm" onClick={onOpenSettings}>
          <Settings2 size={14}/> Mở AI Settings
        </Button>
      </div>

      <div className="chat-session-bar">
        <div className="chat-session-buttons">
          {artworkJob && <button type="button" className={usingArtwork ? "selected" : ""} onClick={() => setUsingArtwork(true)}>Dùng job hiện tại</button>}
          {chatJobId && <button type="button" className={!usingArtwork ? "selected" : ""} onClick={() => setUsingArtwork(false)}>Phiên chat đã lưu</button>}
          <label className="secondary-button"><ImagePlus size={15}/> Thêm ảnh
            <input type="file" accept="image/png,image/jpeg,image/webp" multiple hidden onChange={(event) => {
              const selected = Array.from(event.target.files ?? []);
              const oversized = selected.find((file) => file.size > MAX_UPLOAD_BYTES);
              if (oversized) {
                setError(`Ảnh "${oversized.name}" vượt giới hạn 50 MB/ảnh.`);
              } else {
                setAttached(selected);
                setError("");
              }
              event.target.value = "";
            }}/>
          </label>
          <span className="chat-upload-limit">Max 50 MB / image</span>
          {(attached.length > 0 || inputFiles.length > 0) && (
            <button type="button" className="primary-button" disabled={busy || !engineOnline}
              onClick={createSession}>Tạo phiên từ {attached.length || inputFiles.length} ảnh</button>
          )}
        </div>
        <span>{activeJobId ? `Session ${activeJobId.slice(0, 12)}` : "Chọn ảnh hoặc dùng job hiện tại để bắt đầu"}</span>
      </div>

      <div className={`chat-workspace ${!activeJobId ? "is-empty" : ""}`}>
        <div className="chat-preview-pane">
          <div className="chat-pane-title"><strong>Ảnh đang chọn</strong><span>{visibleVersion ? `Version ${versions.findIndex((v) => v.version_id === visibleVersion.version_id) + 1}` : "Ảnh gốc"}</span></div>
          <div className="chat-canvas">
            {previewUrl ? <img src={previewUrl} alt="Ảnh đang chỉnh sửa" key={previewUrl}/> :
              <div className="chat-empty"><div className="chat-empty-icon"><ImagePlus size={27}/></div><strong>Chưa có ảnh làm việc</strong><span>Thêm ảnh tham chiếu ở thanh công cụ để bắt đầu chỉnh sửa.</span></div>}
          </div>
          <div className="chat-preview-footer">
            <span><ShieldCheck size={13}/> Kết quả AI cần qua QC trước khi in.</span>
            {visibleVersion && <a className="secondary-button" href={previewUrl} download={`artwork-ai-${visibleVersion.version_id.slice(0,8)}.png`}><Download size={14}/> Tải bản nháp</a>}
            {visibleVersion && <button className="primary-button" disabled={busy} onClick={() => printCheck(visibleVersion.version_id)}><CheckCircle2 size={14}/> Kiểm tra chuẩn in</button>}
          </div>
        </div>
        <div className="chat-conversation">
          <div className="chat-pane-title"><strong>Lịch sử chỉnh sửa</strong><span>{versions.length} phiên bản</span></div>
          <div className="chat-messages">
            <button className={baseVersion === null ? "chat-version selected" : "chat-version"}
              onClick={() => setBaseVersion(null)} disabled={!activeJobId}>
              <strong>Ảnh gốc</strong><span>Chỉnh sửa từ ảnh tham chiếu ban đầu</span>
            </button>
            {versions.map((version, index) => (
              <button key={version.version_id}
                className={baseVersion === version.version_id ? "chat-version selected" : "chat-version"}
                onClick={() => setBaseVersion(version.version_id)}
                disabled={version.state !== "completed"}>
                <div><strong>V{index + 1} · {version.model}</strong><span>{version.state === "running" ? "Đang tạo ảnh…" : version.state === "failed" ? "Lỗi" : "Hoàn thành"}</span></div>
                <p>{version.prompt}</p>
                {version.error && <small>{version.error}</small>}
              </button>
            ))}
          </div>
          <div className="chat-composer">
            <div className="chat-compose-label">Đang sửa từ: {baseVersion ? `V${versions.findIndex((v) => v.version_id === baseVersion) + 1}` : "Ảnh gốc"}</div>
            <Textarea value={prompt} onChange={(e) => setPrompt(e.target.value)}
              onKeyDown={(e) => { if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) { e.preventDefault(); void send(); } }}
              rows={4} maxLength={4000} placeholder="VD: Giữ nguyên toàn bộ chữ, thay hoa hồng thành hoa cúc, màu trắng nền trong suốt, nét sắc rõ để in áo." />
            <Button className="run-button" onClick={send} disabled={!engineOnline || !activeJobId || !config?.configured || !prompt.trim() || running || busy}>
              <Send size={14}/>{running ? "Đang tạo ảnh…" : busy ? "Đang gửi…" : "Tạo phiên bản mới"}
            </Button>
            <small>Ctrl + Enter để gửi · Lưu riêng mỗi phiên bản</small>
          </div>
        </div>
      </div>
      {notice && <p className="dataset-status ready" role="status">{notice}</p>}
      {error && <p className="dataset-error" role="alert">{error}</p>}
    </section>
  );
}
