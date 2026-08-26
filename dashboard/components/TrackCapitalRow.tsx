"use client";

import type { TrackCapitalBlock } from "@/lib/api";

/**
 * 부호 있는 금액. `-0` 을 `0` 으로 정규화한다.
 *
 * JS 에서 `-0 >= 0` 은 `true` 라 `${v >= 0 ? "+" : ""}${v}` 가 `+-0` 을 만든다. 실측에서
 * 폴리 실현 손익이 정확히 `-0` 이라 화면에 `실현 +-0` 으로 찍혔다.
 */
function signedAmount(value: number | null): string {
  if (value === null) return "미상";
  const normalized = value === 0 ? 0 : value;
  const body = normalized.toLocaleString("ko-KR", { maximumFractionDigits: 4 });
  return normalized > 0 ? `+${body}` : body;
}

function formatCapital(value: number | null, currency: string): string {
  if (value === null) return "미상";
  const digits = currency === "KRW" ? 0 : 2;
  return `${value.toLocaleString("ko-KR", { minimumFractionDigits: digits, maximumFractionDigits: digits })} ${currency}`;
}

/**
 * WO-FCE-TRACK-CAPITAL-01 1-3 — 4탭 공통 자본 줄.
 *
 * `시작 X → 현재 Y · 실현 +Z (자본 대비 N%) · 미실현 W (미확정)`
 *
 * 금액이 대표값이고 수익률은 병기다(`METRIC_DEFINITIONS.md` §1). 시작 자본이 미상이면
 * 수익률 자리에 "미산출"을 쓴다 — 역산하지 않는다.
 */
export function TrackCapitalRow({ block, track, label }: { block?: TrackCapitalBlock; track: string; label?: string }) {
  const capital = block?.tracks?.[track];
  if (!block?.available || !capital?.available) {
    return (
      <section className="trackCapitalRow" data-testid={`track-capital-${track}`}>
        <span className="trackCapitalLabel">{label ?? "자본"}</span>
        <strong>조회 불가</strong>
        <small>{block?.reason ?? capital?.reason ?? "자본 원장을 읽을 수 없습니다."}</small>
      </section>
    );
  }
  const realized = capital.realized_pnl;
  const unrealized = capital.unrealized_pnl;
  return (
    <section className="trackCapitalRow" data-testid={`track-capital-${track}`}>
      <span className="trackCapitalLabel">{label ?? "자본"}</span>
      <div className="trackCapitalFigures">
        <span>시작 <b>{formatCapital(capital.starting_capital, capital.currency)}</b></span>
        <span aria-hidden>→</span>
        <span>현재 <b>{formatCapital(capital.current_capital, capital.currency)}</b></span>
        <span className={(realized ?? 0) >= 0 ? "long" : "short"}>
          실현 {signedAmount(realized)}
          {capital.return_on_capital_pct !== null ? ` (자본 대비 ${capital.return_on_capital_pct > 0 ? "+" : ""}${(capital.return_on_capital_pct === 0 ? 0 : capital.return_on_capital_pct).toFixed(2)}%)` : " (자본 대비 미산출)"}
        </span>
        {/* C4 — 미실현은 실현과 같은 칸에 넣지 않는다. */}
        <span className="trackCapitalUnrealized">
          미실현 {signedAmount(unrealized)} (미확정)
        </span>
      </div>
      <small>
        {capital.sample_note}
        {capital.deployed_capital ? ` · 운용중 ${formatCapital(capital.deployed_capital, capital.currency)}` : ""}
        {capital.return_note ? ` · ${capital.return_note}` : ""}
        {capital.current_capital_note ? ` · ${capital.current_capital_note}` : ""}
      </small>
    </section>
  );
}

