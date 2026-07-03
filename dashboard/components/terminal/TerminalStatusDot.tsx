import { StatusDot } from "@astryxdesign/core/StatusDot";

export function TerminalStatusDot({
  label,
  variant,
  pulse = false
}: {
  label: string;
  variant: "success" | "warning" | "error" | "accent" | "neutral";
  pulse?: boolean;
}) {
  return (
    <span className="terminalStatus">
      <StatusDot variant={variant} label={label} isPulsing={pulse} />
      <span>{label}</span>
    </span>
  );
}
