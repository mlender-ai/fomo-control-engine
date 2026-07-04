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
        <span>{xray.method === "trade_fills" ? "실체결 기준" : "데이터 부족"}</span>
      </div>
      <div className="xrayGrid">
        <XrayItem label="현재 상태" value={volumeStateLabel(xray.volume_state)} tone={xray.climax_candidate ? "danger" : xray.spike_detected ? "warning" : "neutral"} />
        <XrayItem label="상대 거래량" value={`${xray.relative_volume.toFixed(2)}배`} tone="neutral" />
        <XrayItem label="급증 여부" value={xray.spike_detected ? "감지됨" : "아님"} tone={xray.spike_detected ? "warning" : "neutral"} />
        <XrayItem label="흡수 흔적" value={xray.absorption_candidate ? "가능성 있음" : "아님"} tone={xray.absorption_candidate ? "warning" : "neutral"} />
        <XrayItem label="클라이맥스 후보" value={yesNoLabel(xray.climax_candidate)} tone={xray.climax_candidate ? "danger" : "neutral"} />
        <XrayItem label="체결 델타" value={xray.delta_ratio === null ? "데이터 부족" : `${(xray.delta_ratio * 100).toFixed(1)}%`} tone={xray.delta_ratio !== null && Math.abs(xray.delta_ratio) >= 0.25 ? "warning" : "neutral"} />
        <XrayItem label="CVD 변화" value={xray.cvd_change === null ? "데이터 부족" : formatCompactNumber(xray.cvd_change)} tone={xray.cvd_change && xray.cvd_change > 0 ? "positive" : "neutral"} />
      </div>
      <div className="xrayNotes">
        {xray.notes.map((note) => (
          <p key={note}>{note}</p>
        ))}
      </div>
    </section>
  );
}

function formatCompactNumber(value: number): string {
  const sign = value > 0 ? "+" : "";
  const absolute = Math.abs(value);
  if (absolute >= 1_000_000_000) return `${sign}${(value / 1_000_000_000).toFixed(2)}B`;
  if (absolute >= 1_000_000) return `${sign}${(value / 1_000_000).toFixed(2)}M`;
  if (absolute >= 1_000) return `${sign}${(value / 1_000).toFixed(2)}K`;
  return `${sign}${value.toFixed(2)}`;
}

function XrayItem({ label, value, tone }: { label: string; value: string; tone: "positive" | "warning" | "danger" | "neutral" }) {
  return (
    <div className={`xrayItem tone-${tone}`}>
      <span>{label}</span>
      <strong>{value}</strong>
    </div>
  );
}
