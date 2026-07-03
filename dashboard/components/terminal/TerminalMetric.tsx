type TerminalMetricTone = "positive" | "negative" | "warning" | "neutral" | "info" | "agent";

export function TerminalMetric({
  label,
  value,
  delta,
  tone = "neutral",
  mono = true
}: {
  label: string;
  value: string | number;
  delta?: string | number;
  tone?: TerminalMetricTone;
  mono?: boolean;
}) {
  return (
    <div className={`terminalMetric tone-${tone}`}>
      <span>{label}</span>
      <strong className={mono ? "terminalMono" : undefined}>{value}</strong>
      {delta !== undefined ? <em>{delta}</em> : null}
    </div>
  );
}
