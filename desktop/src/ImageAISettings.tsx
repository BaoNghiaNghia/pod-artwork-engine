import { useEffect, useState } from "react";
import { Image, ShieldCheck } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { audit } from "./system";

type ImageConfig = {
  configured: boolean;
  model: string;
  provider: string;
  key_persistence: string;
};
const API = "http://127.0.0.1:8765";

/** Single home for AI Image Chat credentials and model configuration. */
export default function ImageAISettings({ engineOnline }: { engineOnline: boolean }) {
  const [config, setConfig] = useState<ImageConfig | null>(null);
  const [model, setModel] = useState("gpt-image-1");
  const [key, setKey] = useState("");
  const [clearKey, setClearKey] = useState(false);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");

  useEffect(() => {
    if (!engineOnline) {
      setConfig(null);
      return;
    }
    let cancelled = false;
    fetch(`${API}/image-chat/config`)
      .then(async (response) => {
        if (!response.ok) throw new Error("Không thể đọc cấu hình Image AI.");
        return await response.json() as ImageConfig;
      })
      .then((value) => {
        if (!cancelled) { setConfig(value); setModel(value.model); }
      })
      .catch(() => { if (!cancelled) setError("Không thể đọc cấu hình Image AI."); });
    return () => { cancelled = true; };
  }, [engineOnline]);

  const save = async () => {
    if (!engineOnline || busy || !config) return;
    setBusy(true); setMessage(""); setError("");
    try {
      const response = await fetch(`${API}/image-chat/config`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          model: model.trim(),
          clear_key: clearKey,
          ...(!clearKey && key.trim() ? { api_key: key.trim() } : {}),
        }),
      });
      if (!response.ok) {
        const payload = await response.json().catch(() => ({}));
        throw new Error(typeof payload.detail === "string" ? payload.detail : "Không thể lưu Image AI.");
      }
      const updated = await response.json() as ImageConfig;
      setConfig(updated);
      setModel(updated.model);
      setKey("");
      setClearKey(false);
      setMessage("Đã lưu cấu hình Image AI. AI Chat sẽ sử dụng cấu hình này.");
      audit("image_ai_config_saved", "provider");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Không thể lưu Image AI.");
    } finally {
      setBusy(false);
    }
  };

  return (
    <section id="image-ai-settings" className="dataset-workspace provider-settings ai-connection-card">
      <div className="dataset-intro">
        <div>
          <div className="eyebrow">02 · IMAGE EDITING</div>
          <h2>Image AI</h2>
          <p>OpenAI Images API · chỉnh sửa ảnh qua AI Image Chat.</p>
        </div>
        <Badge variant={config?.configured ? "secondary" : "outline"}
          className={config?.configured ? "dataset-safe" : "chat-disconnected"}>
          {config?.configured ? "Ready" : "Not connected"}
        </Badge>
      </div>
      <div className="dataset-panel provider-main">
        {config ? (
          <>
            <div className="provider-essential">
              <label htmlFor="image-ai-model"><span>Image model</span>
                <Input id="image-ai-model" aria-label="Image model" value={model} maxLength={80}
                  onChange={(event) => { setModel(event.target.value); setMessage(""); }}
                  placeholder="gpt-image-1" autoComplete="off" />
              </label>
              <label htmlFor="image-ai-key"><span>OpenAI API key</span>
                <Input id="image-ai-key" aria-label="OpenAI API key" type="password"
                  autoComplete="off" value={key}
                  onChange={(event) => { setKey(event.target.value); setMessage(""); }}
                  placeholder={config.configured ? "Key active — leave blank to keep" : "Enter OpenAI API key"}
                />
              </label>
            </div>
            <label className="dataset-checkbox-row ai-clear-key">
              <input type="checkbox" checked={clearKey}
                onChange={(event) => setClearKey(event.target.checked)} />
              Clear Image AI session key
            </label>
            <div className="provider-bottom">
              <div className="provider-actions">
                <Button className="dataset-action" disabled={!engineOnline || busy || !model.trim()}
                  onClick={save}>
                  {busy ? "Saving…" : "Save Image AI"}
                </Button>
              </div>
              <p className="provider-small-note">
                <ShieldCheck size={13} aria-hidden="true" /> API key chỉ lưu trong Engine đang chạy, không ghi vào file.
                Để tự cấu hình khi khởi động, dùng POD_IMAGE_API_KEY và POD_IMAGE_MODEL.
              </p>
            </div>
          </>
        ) : (
          <p className="dataset-muted"><Image size={15} aria-hidden="true" />
            {engineOnline ? "Đang tải cấu hình Image AI…" : "Kết nối Engine để cấu hình Image AI."}
          </p>
        )}
        {message && <div className="dataset-status ready" role="status">{message}</div>}
        {error && <div className="dataset-error" role="alert">{error}</div>}
      </div>
    </section>
  );
}
