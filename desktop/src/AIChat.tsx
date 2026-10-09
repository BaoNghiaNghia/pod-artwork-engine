import { useEffect, useMemo, useState } from "react";

const API = "http://127.0.0.1:8765";
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
  engineOnline, inputFiles, artworkJob, onPrintCheck
}: {
  engineOnline: boolean;
  inputFiles: File[];
  artworkJob: ArtworkJob | null;
  onPrintCheck: (job: ArtworkJob) => void;
}) {
  const [config, setConfig] = useState<ImageConfig | null>(null);
  const [key, setKey] = useState("");
  const [model, setModel] = useState("gpt-image-1");
  const [savingKey, setSavingKey] = useState(false);
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
      .then((value) => { if (!cancelled) { setConfig(value); setModel(value.model); } })
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

  const saveConfig = async () => {
    if (savingKey || !engineOnline) return;
    setSavingKey(true); setError(""); setNotice("");
    try {
      const response = await fetch(`${API}/image-chat/config`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ model: model.trim(), ...(key.trim() ? {api_key:key.trim()} : {}) })
      });
      if (!response.ok) throw new Error(await responseError(response));
      setConfig(await response.json());
      setKey("");
      setNotice("Đã cấu hình Image AI cho phiên Engine hiện tại.");
    } catch (err) { setError(err instanceof Error ? err.message : String(err)); }
    finally { setSavingKey(false); }
  };

  const createSession = async () => {
    const images = attached.length ? attached : inputFiles;
    if (!images.length || busy) return;
    setBusy(true); setError(""); setNotice("");
    try {
      const body = new FormData();
      images.forEach((image) => body.append("files", image));
      const response = await fetch(`${API}/image-chat/sessions`, { method:"POST", body });
      if (!response.ok) throw new Error(await responseError(response));
      const result = (await response.json()) as ArtworkJob;
      setUsingArtwork(false); setChatJobId(result.job_id); setVersions([]); setBaseVersion(null);
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
    } catch (err) { setError(err instanceof Error ? err.message : String(err)); }
    finally { setBusy(false); }
  };

  return (
    <section className="image-chat">
      <div className="image-chat-heading">
        <div>
          <div className="eyebrow">ARTWORK IMAGE STUDIO</div>
          <h2>Chỉnh sửa ảnh bằng AI Chat</h2>
          <p>Viết yêu cầu · Tạo version · Chọn bản phù hợp · Kiểm tra chuẩn in.</p>
        </div>
        <span className={config?.configured ? "chat-connected" : "chat-disconnected"}>
          {config?.configured ? "Image AI ready" : "Chưa kết nối"}
        </span>
      </div>

      <details className="chat-config" open={!config?.configured}>
        <summary>Cấu hình OpenAI Image AI <span>{config?.configured ? "Đã có key" : "Cần API key"}</span></summary>
        <div className="chat-config-fields">
          <label>Model ảnh <input value={model} onChange={(e) => setModel(e.target.value)} placeholder="gpt-image-1" /></label>
          <label>OpenAI API key <input value={key} type="password" autoComplete="off" onChange={(e) => setKey(e.target.value)} placeholder={config?.configured ? "Đã cấu hình — để trống nếu giữ key" : "Nhập API key"} /></label>
          <button type="button" className="secondary-button" disabled={!engineOnline || savingKey} onClick={saveConfig}>{savingKey ? "Đang lưu…" : "Lưu AI"}</button>
        </div>
        <p>Key chỉ giữ trong Engine đang chạy, không lưu vào file. Muốn giữ sau khởi động lại, đặt biến môi trường POD_IMAGE_API_KEY và POD_IMAGE_MODEL.</p>
      </details>

      <div className="chat-session-bar">
        <div className="chat-session-buttons">
          {artworkJob && <button type="button" className={usingArtwork ? "selected" : ""} onClick={() => setUsingArtwork(true)}>Dùng job hiện tại</button>}
          {chatJobId && <button type="button" className={!usingArtwork ? "selected" : ""} onClick={() => setUsingArtwork(false)}>Phiên chat đã lưu</button>}
          <label className="secondary-button">Thêm ảnh tham chiếu
            <input type="file" accept="image/png,image/jpeg,image/webp" multiple hidden onChange={(event) => setAttached(Array.from(event.target.files ?? []))}/>
          </label>
          <button type="button" className="secondary-button" disabled={busy || !engineOnline || !(attached.length || inputFiles.length)}
            onClick={createSession}>Tạo phiên từ ảnh ({attached.length || inputFiles.length})</button>
        </div>
        <span>{activeJobId ? `Session ${activeJobId.slice(0, 12)}` : "Chọn ảnh hoặc dùng job hiện tại để bắt đầu"}</span>
      </div>

      <div className="chat-workspace">
        <div className="chat-preview-pane">
          <div className="chat-pane-title"><strong>Ảnh đang chọn</strong><span>{visibleVersion ? `Version ${versions.findIndex((v) => v.version_id === visibleVersion.version_id) + 1}` : "Ảnh gốc"}</span></div>
          <div className="chat-canvas">
            {previewUrl ? <img src={previewUrl} alt="Ảnh đang chỉnh sửa" key={previewUrl}/> :
              <div className="chat-empty">Chọn ảnh tham chiếu để bắt đầu chỉnh sửa bằng AI.</div>}
          </div>
          <div className="chat-preview-footer">
            <span>AI version là bản nháp, chưa xác nhận chuẩn in.</span>
            {visibleVersion && <a className="secondary-button" href={previewUrl} download={`artwork-ai-${visibleVersion.version_id.slice(0,8)}.png`}>Tải PNG nháp</a>}
            {visibleVersion && <button className="primary-button" disabled={busy} onClick={() => printCheck(visibleVersion.version_id)}>Kiểm tra chuẩn in</button>}
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
            <textarea value={prompt} onChange={(e) => setPrompt(e.target.value)}
              onKeyDown={(e) => { if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) { e.preventDefault(); void send(); } }}
              rows={4} maxLength={4000} placeholder="VD: Giữ nguyên toàn bộ chữ, thay hoa hồng thành hoa cúc, màu trắng nền trong suốt, nét sắc rõ để in áo." />
            <button className="run-button" onClick={send} disabled={!engineOnline || !activeJobId || !config?.configured || !prompt.trim() || running || busy}>
              {running ? "AI đang tạo ảnh…" : busy ? "Đang gửi…" : "Gửi yêu cầu chỉnh sửa"}
            </button>
            <small>Ctrl + Enter để gửi · Mỗi lượt tạo version mới · Ảnh gốc luôn được giữ lại.</small>
          </div>
        </div>
      </div>
      {notice && <p className="dataset-status ready" role="status">{notice}</p>}
      {error && <p className="dataset-error" role="alert">{error}</p>}
    </section>
  );
}
