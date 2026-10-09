import { useEffect, useState } from "react";

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
          <div className="eyebrow">AI RECONSTRUCTION</div>
          <h2>Provider settings</h2>
          <p>Connect a server implementing the POD Engine typed analyze / reconstruct / judge contract.</p>
        </div>
        <span className="dataset-safe">{config?.provider_state.available ? "Provider available" : "Not connected"}</span>
      </div>
      <div className="dataset-panel">
        <div className="dataset-section-title">Connection</div>
        <p className="dataset-muted">A generic OpenAI or Gemini API URL does not implement the POD protocol directly. An adapter is required. HTTPS is mandatory for remote URLs.</p>
        {config && (
          <div className="provider-fields">
            <label><span>Provider URL</span>
              <input value={config.url} onChange={(e) => update("url",e.target.value)} placeholder="https://your-pod-provider.example/v1/execute" /></label>
            <label><span>Provider name</span>
              <input value={config.name} onChange={(e) => update("name",e.target.value)} /></label>
            <label><span>Model alias</span>
              <input value={config.model_alias} onChange={(e) => update("model_alias",e.target.value)} placeholder="provider-specific model identifier" /></label>
            <label><span>Timeout (seconds)</span>
              <input type="number" min={5} max={600} value={config.timeout_seconds} onChange={(e) => update("timeout_seconds",e.target.value)} /></label>
            <label><span>API key (optional, hidden)</span>
              <input type="password" autoComplete="off" value={token} onChange={(e) => setToken(e.target.value)} placeholder={config.token_set ? "Key active; leave blank to retain" : "Paste API key"} /></label>
            <label className="dataset-checkbox-row"><input type="checkbox" checked={clearToken} onChange={(e) => setClearToken(e.target.checked)} />Clear current key</label>
          </div>
        )}
        <p className="dataset-muted">For security, keys entered here are kept only in this Engine session, never saved in the configuration file. To retain a key across restarts, set POD_REMOTE_PROVIDER_TOKEN in the process environment.</p>
        <div className="provider-actions">
          <button className="dataset-action" disabled={!engineOnline || !config || busy || testing} onClick={save}>
            {busy ? "Saving…" : "Save connection"}</button>
          <button className="secondary-button" disabled={!engineOnline || !config?.provider_state.available || busy || testing} onClick={test}>
            {testing ? "Checking…" : "Test provider"}</button>
        </div>
        <p className="dataset-muted">Test provider makes a real ANALYZE request using a synthetic image; your provider may charge for it. Reconstruction is verified only by running an actual job.</p>
        {message && <div className="dataset-status ready" role="status">{message}</div>}
        {error && <div className="dataset-error" role="alert">{error}</div>}
      </div>
    </section>
  );
}
