import { Badge } from "@astryxdesign/core/Badge";

type ScoreType = "entry" | "fomo" | "risk" | "structure" | "volume" | "liquidity" | "momentum";

export function TerminalScoreBadge({ score, type }: { score: number; type: ScoreType }) {
  const tone = scoreVariant(score, type);
  return <Badge variant={tone} label={`${score}`} />;
}

function scoreVariant(score: number, type: ScoreType): "neutral" | "info" | "success" | "warning" | "error" | "cyan" | "purple" {
  if (type === "fomo" || type === "risk") {
    if (score >= 75) return "error";
    if (score >= 60) return "warning";
    return "neutral";
  }
  if (type === "liquidity" || type === "momentum") {
    if (score >= 75) return "cyan";
    if (score >= 60) return "info";
    return "neutral";
  }
  if (type === "volume") {
    if (score >= 75) return "success";
    if (score >= 60) return "info";
    return "neutral";
  }
  if (score >= 85) return "success";
  if (score >= 75) return "info";
  if (score >= 65) return "warning";
  if (score < 50) return "error";
  return "neutral";
}
