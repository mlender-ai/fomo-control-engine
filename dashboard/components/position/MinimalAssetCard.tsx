import type { ReactNode } from "react";

export function MinimalAssetCard({
  symbol,
  meta,
  summary,
  selected = false,
  tone = "neutral",
  title,
  children,
  onClick
}: {
  symbol: string;
  meta: string;
  summary?: string | null;
  selected?: boolean;
  tone?: "positive" | "negative" | "neutral" | "watch";
  title?: string;
  children?: ReactNode;
  onClick: () => void;
}) {
  return (
    <button
      className={`positionStripCard minimal sharedAssetCard tone-${tone} ${selected ? "selected" : ""}`}
      data-testid="minimal-asset-card"
      onClick={onClick}
      title={title}
      type="button"
    >
      <strong>{symbol}</strong>
      <span>{meta}</span>
      {summary ? <small>{summary}</small> : null}
      {children}
    </button>
  );
}
