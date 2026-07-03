import type { ScoreBreakdown } from "@/lib/api";

const labels: Array<[keyof ScoreBreakdown, string, string]> = [
  ["structure", "Structure", "시장 구조"],
  ["volume", "Volume", "거래량"],
  ["liquidity", "Liquidity", "유동성"],
  ["momentum", "Momentum", "모멘텀"],
  ["risk", "Risk", "높을수록 위험"],
  ["fomo", "FOMO", "추격 심리"]
];

export function ScoreBreakdownView({ scores }: { scores: ScoreBreakdown }) {
  return (
    <div className="scoreBreakdown">
      {labels.map(([key, label, description]) => (
        <div className="scoreRow" key={String(key)}>
          <div className="scoreRowLabel">
            <strong>{label}</strong>
            <span>{description}</span>
          </div>
          <div className="scoreTrack" aria-label={`${label} score ${scores[key]}`}>
            <div className={`scoreFill ${key === "risk" || key === "fomo" ? "riskFill" : ""}`} style={{ width: `${scores[key]}%` }} />
          </div>
          <strong className="scoreValue">{scores[key]}</strong>
        </div>
      ))}
    </div>
  );
}
