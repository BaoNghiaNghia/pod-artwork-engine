import { SlidersHorizontal } from "lucide-react";
import ProviderSettings from "./ProviderSettings";
import ImageAISettings from "./ImageAISettings";
import SystemStatus from "./SystemStatus";
import { type UpdateCheck } from "./system";

/** All AI credentials/models are managed here; job and chat screens contain no config forms. */
export default function AISettings({
  engineOnline, onProviderChanged, latestCheck, onChecked,
}: {
  engineOnline: boolean;
  onProviderChanged: () => void;
  latestCheck: UpdateCheck | null;
  onChecked: (check: UpdateCheck) => void;
}) {
  return (
    <div className="ai-settings-page">
      <div className="studio-page-heading">
        <div>
          <div className="eyebrow">CONNECTIONS & MODELS</div>
          <h1>AI Settings</h1>
          <p>Một nơi quản lý tất cả model và API key của Artwork Reconstruction và AI Image Chat.</p>
        </div>
        <span className="studio-page-tag"><SlidersHorizontal size={14}/> Centralized</span>
      </div>
      <div className="ai-settings-connections">
        <ProviderSettings engineOnline={engineOnline} onChanged={onProviderChanged} />
        <ImageAISettings engineOnline={engineOnline} />
      </div>
      <div className="ai-settings-info">
        <strong>Hai loại AI, một trang cấu hình</strong>
        <p>Artwork Reconstruction yêu cầu provider tương thích giao thức POD (analyze, reconstruct, judge).
          Image AI sử dụng trực tiếp OpenAI Images API. Hai API key không dùng chung, nhưng đều được quản lý tại đây.</p>
      </div>
      <div className="ai-settings-health">
        <div className="eyebrow">SYSTEM HEALTH</div>
        <SystemStatus engineOnline={engineOnline} latestCheck={latestCheck} onChecked={onChecked}/>
      </div>
    </div>
  );
}
