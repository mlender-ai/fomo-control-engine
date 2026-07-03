import type { PositionChartAnalysis } from "@/lib/api";

export function VolumePanel({ analysis }: { analysis: PositionChartAnalysis }) {
  const xray = analysis.volume_xray;
  return (
    <div className="chartVolumeSummary">
      <span>Volume Panel</span>
      <strong>{xray.volume_state.replaceAll("_", " ")}</strong>
      <em>RVOL {xray.relative_volume.toFixed(2)}x</em>
      {xray.spike_detected ? <i>Spike</i> : null}
    </div>
  );
}
