export function formatPrice(value: number | null | undefined): string {
  if (typeof value !== "number" || !Number.isFinite(value)) return "-";
  const magnitude = Math.abs(value);
  if (magnitude < 1) return value.toFixed(4);
  if (magnitude < 100) return value.toFixed(2);
  return value.toLocaleString(undefined, { maximumFractionDigits: 2 });
}

export function signedPercent(value: number | null | undefined): string {
  if (typeof value !== "number" || !Number.isFinite(value)) return "-";
  return `${value >= 0 ? "+" : ""}${value.toFixed(2)}%`;
}

export function scoreTone(score: number | null | undefined): string {
  if (typeof score !== "number" || !Number.isFinite(score)) return "weak";
  if (score >= 85) return "strong";
  if (score >= 75) return "candidate";
  if (score >= 65) return "watch";
  if (score >= 50) return "wait";
  return "weak";
}
