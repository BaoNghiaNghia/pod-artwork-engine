import { useCallback, useEffect, useState } from "react";
import { Download, FileText, RefreshCcw, ShieldCheck, CircleAlert } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { systemRequest, type SafeLogEntry, type SystemOverview, type UpdateCheck } from "./system";

export default function SystemStatus({
  engineOnline, latestCheck, onChecked,
}: {
  engineOnline: boolean;
  latestCheck: UpdateCheck | null;
  onChecked: (check: UpdateCheck) => void;
}) {
  const [overview, setOverview] = useState<SystemOverview | null>(null);
  const [events, setEvents] = useState<SafeLogEntry[]>([]);
  const [loading, setLoading] = useState(false);
  const [staging, setStaging] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const check = latestCheck ?? overview?.last_check ?? null;
  const pending = overview?.update_state.staged_version;

  const refresh = useCallback(async () => {
    if (!engineOnline) return;
    try {
      const [state, logs] = await Promise.all([
        systemRequest<SystemOverview>("/system/overview"),
        systemRequest<SafeLogEntry[]>("/system/events?limit=30"),
      ]);
      setOverview(state);
      setEvents(logs);
      setError("");
    } catch {
      setError("Không thể tải trạng thái updater/logger từ Engine.");
    }
  }, [engineOnline]);

  useEffect(() => { void refresh(); }, [refresh]);

  const checkNow = async () => {
    if (loading || !engineOnline) return;
    setLoading(true); setError(""); setNotice("");
    try {
      const result = await systemRequest<UpdateCheck>("/system/check-updates", "POST");
      onChecked(result);
      setNotice(result.update_available
        ? `Đã tìm thấy v${result.latest_version}. Có thể tải bản cập nhật.`
        : `Đang sử dụng bản mới nhất: v${result.current_version}.`);
      await refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Không thể kiểm tra phiên bản mới.");
    } finally { setLoading(false); }
  };

  const stage = async () => {
    if (staging || !engineOnline) return;
    setStaging(true); setError(""); setNotice("");
    try {
      const result = await systemRequest<{status: string; version: string}>("/system/stage-update", "POST");
      setNotice(result.status === "restart_required"
        ? `Đã tải và xác minh v${result.version}. Đóng và mở lại POD Artwork Tool để cài đặt. Job đang chạy sẽ không bị ngắt tự động.`
        : "Hiện đã là bản mới nhất.");
      await refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Tải cập nhật thất bại.");
    } finally { setStaging(false); }
  };

  return (
    <section className="system-monitor" aria-label="System updates and logger">
      <Card className="system-monitor-card gap-3 p-4">
        <div className="system-monitor-heading">
          <div>
            <div className="eyebrow">APPLICATION</div>
            <h3>Version & updates</h3>
            <p>Kiểm tra phiên bản và tải bản stable mới khi đang mở tool.</p>
          </div>
          <Badge variant="outline">v{overview?.current_version ?? "…"}</Badge>
        </div>
        <div className="system-update-meta">
          {pending
            ? <span className="system-update-ready"><ShieldCheck size={15}/> v{pending} đã sẵn sàng · khởi động lại để kích hoạt</span>
            : check?.update_available
              ? <span className="system-update-available"><Download size={15}/> Có phiên bản mới: v{check.latest_version}</span>
              : <span><ShieldCheck size={15}/> {check ? "Đang sử dụng bản mới nhất" : "Chưa kiểm tra trong phiên này"}</span>}
          <span>Channel: stable{check?.checked_at ? ` · Checked ${new Date(check.checked_at).toLocaleString()}` : ""}</span>
        </div>
        <div className="system-monitor-actions">
          <Button variant="outline" size="sm" disabled={!engineOnline || loading || staging} onClick={checkNow}>
            <RefreshCcw size={14} className={loading ? "animate-spin" : ""}/>
            {loading ? "Đang kiểm tra…" : "Check for updates"}
          </Button>
          {check?.update_available && !pending &&
            <Button size="sm" disabled={!engineOnline || staging || loading} onClick={stage}>
              <Download size={14}/>{staging ? "Đang tải và xác minh…" : "Download update"}
            </Button>}
        </div>
        <p className="system-monitor-hint">Tool không tự đóng khi tải cập nhật. Launcher sẽ kiểm tra sức khỏe Engine và tự rollback nếu bản mới không khởi động được.</p>
      </Card>
      <Card className="system-monitor-card gap-3 p-4">
        <div className="system-monitor-heading">
          <div>
            <div className="eyebrow">DIAGNOSTICS</div>
            <h3>Logger</h3>
            <p>Log được xoay vòng, lưu cục bộ. Giao diện chỉ hiển thị sự kiện an toàn, không hiển thị API key hoặc nội dung yêu cầu AI.</p>
          </div>
          <Button size="sm" variant="ghost" onClick={() => void refresh()} disabled={!engineOnline}>
            <RefreshCcw size={14}/> Refresh
          </Button>
        </div>
        {overview && <>
          <p className="system-log-path">Folder: <code>{overview.logs.directory}</code></p>
          <p className="system-monitor-hint">Retention: {overview.logs.job_retention_days} ngày cho job logs · {overview.logs.job_log_count} job logs.</p>
          <div className="system-log-files">
            {overview.logs.files.map((file) => (
              <div key={file.name}><FileText size={14}/><strong>{file.name}</strong>
                <span>{file.present ? `${(file.bytes / 1024).toFixed(1)} KB` : "Chưa có log"}</span>
              </div>
            ))}
          </div>
        </>}
        <div className="system-log-recent">
          <strong>Recent events</strong>
          {events.length ? events.slice(-12).reverse().map((entry, i) => (
            <div key={`${entry.timestamp ?? i}-${i}`} className="system-log-event">
              <span>{entry.timestamp ? new Date(entry.timestamp).toLocaleTimeString() : "—"}</span>
              <strong>{entry.event}</strong>
              <span>{entry.stage || entry.route || ""}</span>
            </div>
          )) : <p className="system-monitor-hint">Chưa có sự kiện mới trong Engine hiện tại.</p>}
        </div>
      </Card>
      {notice && <div role="status" className="dataset-status ready">{notice}</div>}
      {error && <div role="alert" className="dataset-error"><CircleAlert size={14}/>{error}</div>}
    </section>
  );
}
