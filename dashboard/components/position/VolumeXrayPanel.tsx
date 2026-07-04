import type { PositionChartAnalysis } from "@/lib/api";
import { volumeStateLabel, yesNoLabel } from "@/lib/labels/marketStateLabels";

export function VolumeXrayPanel({ analysis }: { analysis: PositionChartAnalysis }) {
  const xray = analysis.volume_xray;
  return (
    <section className="analysisPanel volumeXrayPanel">
      <div className="analysisPanelHeader">
        <div>
          <h2>거래량 엑스레이</h2>
          <p>현재 포지션 판단에 필요한 거래량 상태</p>
        </div>
        <span>상대 거래량 {xray.relative_volume.toFixed(2)}배</span>
      </div>
      <div className="xrayGrid">
        <XrayItem label="현재 상태" value={volumeStateLabel(xray.volume_state)} tone={xray.climax_candidate ? "danger" : xray.spike_detected ? "warning" : "neutral"} />
        <XrayItem label="급증 여부" value={xray.spike_detected ? "감지됨" : "아님"} tone={xray.spike_detected ? "warning" : "neutral"} />
        <XrayItem label="흡수 흔적" value={xray.absorption_candidate ? "가능성 있음" : "아님"} tone={xray.absorption_candidate ? "warning" : "neutral"} />
        <XrayItem label="클라이맥스 후보" value={yesNoLabel(xray.climax_candidate)} tone={xray.climax_candidate ? "danger" : "neutral"} />
        <XrayItem label="거래량 동반 반등" value={xray.rebound_with_volume ? "확인됨" : "약함"} tone={xray.rebound_with_volume ? "positive" : "neutral"} />
      </div>
      <div className="xrayNotes">
        {xray.notes.map((note) => (
          <p key={note}>{note}</p>
        ))}
      </div>
    </section>
  );
}

function XrayItem({ label, value, tone }: { label: string; value: string; tone: "positive" | "warning" | "danger" | "neutral" }) {
  return (
    <div className={`xrayItem tone-${tone}`}>
      <span>{label}</span>
      <strong>{value}</strong>
    </div>
  );
}
