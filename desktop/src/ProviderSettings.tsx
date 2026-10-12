import { useEffect, useState } from "react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";

const API = "http://127.0.0.1:8765";
type Config = {
  url: string;
  name: string;
  model_alias: string;
  timeout_seconds: number;
  token_set: boolean;
  token_persistence: string;
  protocol: string;
  provider_state: { configured: boolean; available: boolean; failure_count: number; cooldown_remaining_seconds: number };
};

export default function ProviderSettings({ engineOnline, onChanged }: {
  engineOnline: boolean;
  onChanged: () => void;
}) {
  const [config, setConfig] = useState<Config | null>(null);
  const [token, setToken] = useState("");
  const [clearToken, setClearToken] = useState(false);
  const [busy, setBusy] = useState(false);
  const [testing, setTesting] = useState(false);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  useEffect(() => {
    if (!engineOnline) return;
    fetch(`${API}/provider/config`)
      .then((r) => { if (!r.ok) throw new Error("Unable to load provider"); return r.json(); })
      .then(setConfig)
      .catch((e) => setError(String(e)));
  }, [engineOnline]);

  const update = (key: keyof Pick<Config,"url"|"name"|"model_alias"|"timeout_seconds">, value: string) => {
    setConfig((current) => current ? ({ ...current, [key]: key === "timeout_seconds" ? Number(value) : value }) : current);
    setMessage("");
  };
  const responseError = async (response: Response) => {
    const data = await response.json().catch(() => ({}));
    return typeof data.detail === "string" ? data.detail : `HTTP ${response.status}`;
  };

  const save = async () => {
    if (!config || busy) return;
    setBusy(true); setError(""); setMessage("");
    try {
      const r = await fetch(`${API}/provider/config`, {
        method: "POST", headers: {"Content-Type": "application/json"},
        body: JSON.stringify({
          url: config.url, name: config.name, model_alias: config.model_alias,
          timeout_seconds: config.timeout_seconds,
          ...(token.trim() ? {token: token.trim()} : {}),
          clear_token: clearToken
        })
      });
      if (!r.ok) throw new Error(await responseError(r));
      setConfig(await r.json()); setToken(""); setClearToken(false);
      setMessage("Saved. Provider settings apply to new jobs immediately.");
      onChanged();
    } catch (err) { setError(err instanceof Error ? err.message : String(err)); }
    finally { setBusy(false); }
  };

  const test = async () => {
    setTesting(true); setError(""); setMessage("");
    try {
      const r = await fetch(`${API}/provider/test`, {method:"POST"});
      if (!r.ok) throw new Error(await responseError(r));
      const data = await r.json();
      setMessage(`Analyze contract verified: ${data.provider} (${data.model_version || "unspecified model"}). Reconstruction will be tested by an actual job.`);
      onChanged();
    } catch (err) { setError(err instanceof Error ? err.message : String(err)); }
    finally { setTesting(false); }
  };
  return (
    <section className="dataset-workspace provider-settings">
      <div className="dataset-intro">
        <div>
          <div className="eyebrow">CONNECTIONS</div>
          <h2>AI Reconstruction</h2>
          <p>Manage the image reconstruction service used for artwork recovery.</p>
        </div>
        <Badge variant={config?.provider_state.available ? "secondary" : "outline"} className={config?.provider_state.available ? "dataset-safe" : "chat-disconnected"}>
          {config?.provider_state.available ? "Connected" : "Not connected"}
        </Badge>
      </div>
      <div className="dataset-panel provider-main">
        <div className="dataset-section-title">Provider connection</div>
        {config ? (
          <>
            <div className="provider-essential">
              <label><span>Provider endpoint</span>
                <Input value={config.url} onChange={(e) => update("url", e.target.value)}
                  placeholder="https://your-pod-provider.example/v1/execute" /></label>
              <label><span>API key <small>(optional)</small></span>
                <Input type="password" autoComplete="off" value={token}
                  onChange={(e) => setToken(e.target.value)}
                  placeholder={config.token_set ? "Key active — leave blank to keep" : "Enter provider API key"} /></label>
            </div>
            <details className="provider-advanced">
              <summary>Advanced connection options</summary>
              <div className="provider-fields">
                <label><span>Provider name</span>
                  <Input value={config.name} onChange={(e) => update("name", e.target.value)} /></label>
                <label><span>Model alias</span>
                  <Input value={config.model_alias} onChange={(e) => update("model_alias", e.target.value)}
                    placeholder="Provider-specific identifier" /></label>
                <label><span>Timeout (seconds)</span>
                  <Input type="number" min={5} max={600} value={config.timeout_seconds}
                    onChange={(e) => update("timeout_seconds", e.target.value)} /></label>
                <label className="dataset-checkbox-row"><input type="checkbox" checked={clearToken}
                  onChange={(e) => setClearToken(e.target.checked)} />Clear saved session key</label>
              </div>
              <p className="dataset-muted">Requires a POD-compatible analyze / reconstruct / judge endpoint, not a direct OpenAI/Gemini URL. Remote endpoints must use HTTPS.</p>
            </details>
            <div className="provider-bottom">
              <div className="provider-actions">
                <Button className="dataset-action" disabled={!engineOnline || busy || testing} onClick={save}>
                  {busy ? "Saving…" : "Save connection"}</Button>
                <Button variant="outline" className="secondary-button"
                  disabled={!engineOnline || !config.provider_state.available || busy || testing} onClick={test}>
                  {testing ? "Testing…" : "Test provider"}</Button>
              </div>
              <p className="provider-small-note">Keys stay in the running engine session. Tests call the provider and may incur usage costs.</p>
            </div>
          </>
        ) : <p className="dataset-muted">Connect the local engine to edit provider settings.</p>}
        {message && <div className="dataset-status ready" role="status">{message}</div>}
        {error && <div className="dataset-error" role="alert">{error}</div>}
      </div>
    </section>
  );
}
