import type { PositionHealthComponents, ScoreBreakdown } from "@/lib/api";

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

type HealthComponentKey = "survival" | "pnl_state" | "thesis_integrity" | "structure" | "flow";

const healthLabels: Array<[HealthComponentKey, string, string, number]> = [
  ["survival", "생존", "청산가 거리 기준", 30],
  ["pnl_state", "손익 상태", "ROE와 수익 반납", 20],
  ["thesis_integrity", "논리 유지", "방향 점수 변화", 20],
  ["structure", "구조", "포지션 방향 구조", 20],
  ["flow", "수급", "거래량·펀딩·OI", 10]
];

export function HealthScoreBreakdownView({ components }: { components: PositionHealthComponents }) {
  return (
    <div className="scoreBreakdown healthScoreBreakdown">
      {healthLabels.map(([key, label, description, weight]) => (
        <div className="scoreRow" key={String(key)}>
          <div className="scoreRowLabel">
            <strong>{label}</strong>
            <span>{description} · {weight}%</span>
          </div>
          <div className="scoreTrack" aria-label={`${label} score ${components[key]}`}>
            <div className={`scoreFill ${components[key] < 35 ? "riskFill" : ""}`} style={{ width: `${components[key]}%` }} />
          </div>
          <strong className="scoreValue">{components[key]}</strong>
        </div>
      ))}
    </div>
  );
}
