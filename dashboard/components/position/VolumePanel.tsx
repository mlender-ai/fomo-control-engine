import type { PositionChartAnalysis } from "@/lib/api";
import { volumeStateLabel } from "@/lib/labels/marketStateLabels";

export function VolumePanel({ analysis, averageVolume }: { analysis: PositionChartAnalysis; averageVolume: number }) {
  const xray = analysis.volume_xray;
  const latest = analysis.derivatives?.latest;
  const signals = analysis.derivatives?.signals;
  const funding = signals?.funding_state;
  const crowding = signals?.crowding_score;
  const divergence = signals?.oi_price_divergence;
  return (
    <div className="chartVolumeSummary">
      <span>거래량</span>
      <strong>{volumeStateLabel(xray.volume_state)}</strong>
      <em>상대 거래량 {xray.relative_volume.toFixed(2)}배</em>
      <em>평균 거래량 {formatCompactNumber(averageVolume)}</em>
      <em>{xray.method === "trade_fills" ? "실체결 기준" : "체결 데이터 부족"}</em>
      {xray.spike_detected ? <i>거래량 급증</i> : <i>거래량 둔화</i>}
      <span>파생 수급</span>
      <strong>{crowding?.label ?? "쏠림 표본 부족"}</strong>
      <em>OI 변화 {formatPct(latest?.open_interest_change_pct)}</em>
      <em>펀딩 {funding?.label ?? "표본 부족"}</em>
      <em>롱숏비 {formatNumber(latest?.long_short_ratio)}</em>
      <em>{divergence?.label ?? "가격/OI 표본 부족"}</em>
      {analysis.derivatives?.coinglass?.source_status === "locked" ? <i>Coinglass 미연결</i> : null}
    </div>
  );
}

function formatCompactNumber(value: number): string {
  if (value >= 1_000_000_000) return `${(value / 1_000_000_000).toFixed(2)}B`;
  if (value >= 1_000_000) return `${(value / 1_000_000).toFixed(2)}M`;
  if (value >= 1_000) return `${(value / 1_000).toFixed(2)}K`;
  return value.toFixed(2);
}

function formatPct(value: number | null | undefined): string {
  if (typeof value !== "number") return "표본 부족";
  return `${value > 0 ? "+" : ""}${value.toFixed(2)}%`;
}

function formatNumber(value: number | null | undefined): string {
  if (typeof value !== "number") return "-";
  return value.toFixed(2);
}
