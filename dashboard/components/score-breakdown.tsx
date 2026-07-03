import type { ScoreBreakdown } from "@/lib/api";

const labels: Array<[keyof ScoreBreakdown, string]> = [
  ["structure", "Structure"],
  ["volume", "Volume"],
  ["liquidity", "Liquidity"],
  ["momentum", "Momentum"],
  ["risk", "Risk"]
];

export function ScoreBreakdownView({ scores }: { scores: ScoreBreakdown }) {
  return (
    <div className="metricGrid">
      {labels.map(([key, label]) => (
        <div className="metric" key={String(key)}>
          <span>{label}</span>
          <strong>{scores[key]}</strong>
        </div>
      ))}
    </div>
  );
}
