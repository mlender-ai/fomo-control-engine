export function formatPrice(value: number): string {
  if (value < 1) return value.toFixed(4);
  if (value < 100) return value.toFixed(2);
  return value.toLocaleString(undefined, { maximumFractionDigits: 2 });
}

export function signedPercent(value: number): string {
  return `${value >= 0 ? "+" : ""}${value.toFixed(2)}%`;
}

export function scoreTone(score: number): string {
  if (score >= 85) return "strong";
  if (score >= 75) return "candidate";
  if (score >= 65) return "watch";
  if (score >= 50) return "wait";
  return "weak";
}

