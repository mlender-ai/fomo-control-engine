import type { PositionChartAnalysis } from "@/lib/api";
import { volumeStateLabel } from "@/lib/labels/marketStateLabels";

export function VolumePanel({ analysis, averageVolume }: { analysis: PositionChartAnalysis; averageVolume: number }) {
  const xray = analysis.volume_xray;
  return (
    <div className="chartVolumeSummary">
      <span>거래량</span>
      <strong>{volumeStateLabel(xray.volume_state)}</strong>
      <em>상대 거래량 {xray.relative_volume.toFixed(2)}배</em>
      <em>평균 거래량 {formatCompactNumber(averageVolume)}</em>
      <em>{xray.method === "trade_fills" ? "실체결 기준" : "체결 데이터 부족"}</em>
      {xray.spike_detected ? <i>거래량 급증</i> : <i>거래량 둔화</i>}
    </div>
  );
}

function formatCompactNumber(value: number): string {
  if (value >= 1_000_000_000) return `${(value / 1_000_000_000).toFixed(2)}B`;
  if (value >= 1_000_000) return `${(value / 1_000_000).toFixed(2)}M`;
  if (value >= 1_000) return `${(value / 1_000).toFixed(2)}K`;
  return value.toFixed(2);
}
