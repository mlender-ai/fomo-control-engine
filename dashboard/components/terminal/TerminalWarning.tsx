export function TerminalWarning({ children, tone = "warning" }: { children: React.ReactNode; tone?: "warning" | "error" | "info" }) {
  return <div className={`terminalWarning tone-${tone}`}>{children}</div>;
}
