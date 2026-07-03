import type { PositionChartAnalysis } from "@/lib/api";

export function VolumeXrayPanel({ analysis }: { analysis: PositionChartAnalysis }) {
  const xray = analysis.volume_xray;
  return (
    <section className="analysisPanel volumeXrayPanel">
      <div className="analysisPanelHeader">
        <div>
          <h2>Volume X-Ray</h2>
          <p>현재 포지션 판단에 필요한 거래량 상태</p>
        </div>
        <span>RVOL {xray.relative_volume.toFixed(2)}x</span>
      </div>
      <div className="xrayGrid">
        <XrayItem label="STATE" value={xray.volume_state.replaceAll("_", " ")} tone={xray.climax_candidate ? "danger" : xray.spike_detected ? "warning" : "neutral"} />
        <XrayItem label="SPIKE" value={xray.spike_detected ? "detected" : "no"} tone={xray.spike_detected ? "warning" : "neutral"} />
        <XrayItem label="ABSORPTION" value={xray.absorption_candidate ? "possible" : "no"} tone={xray.absorption_candidate ? "warning" : "neutral"} />
        <XrayItem label="CLIMAX" value={xray.climax_candidate ? "candidate" : "no"} tone={xray.climax_candidate ? "danger" : "neutral"} />
        <XrayItem label="REBOUND" value={xray.rebound_with_volume ? "with volume" : "weak"} tone={xray.rebound_with_volume ? "positive" : "neutral"} />
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
