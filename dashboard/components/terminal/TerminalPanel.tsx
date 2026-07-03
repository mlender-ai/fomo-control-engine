import { StatusDot } from "@astryxdesign/core/StatusDot";

type TerminalPanelStatus = "ok" | "warning" | "error" | "neutral" | "accent";

export function TerminalPanel({
  title,
  subtitle,
  status = "neutral",
  actions,
  children,
  className = ""
}: {
  title: string;
  subtitle?: string;
  status?: TerminalPanelStatus;
  actions?: React.ReactNode;
  children: React.ReactNode;
  className?: string;
}) {
  return (
    <section className={`terminalPanel ${className}`}>
      <div className="terminalPanelHeader">
        <div className="terminalPanelTitleBlock">
          <div className="terminalPanelTitle">
            <StatusDot variant={status === "ok" ? "success" : status} label={`${title} ${status}`} />
            <h2>{title}</h2>
          </div>
          {subtitle ? <p>{subtitle}</p> : null}
        </div>
        {actions ? <div className="terminalPanelActions">{actions}</div> : null}
      </div>
      <div className="terminalPanelBody">{children}</div>
    </section>
  );
}
