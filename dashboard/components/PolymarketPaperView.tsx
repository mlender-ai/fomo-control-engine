"use client";

import { Braces, RefreshCw, ShieldCheck } from "lucide-react";
import { useMemo, useState } from "react";
import { TerminalWarning } from "@/components/terminal";
import type { PolyPaperDashboard, PolyPaperMarket } from "@/lib/api";

export function PolymarketPaperView({
  data,
  collecting,
  onCollect
}: {
  data: PolyPaperDashboard | null;
  collecting: boolean;
  onCollect: () => Promise<void>;
}) {
  const [category, setCategory] = useState<"all" | "crypto" | "macro">("all");
  const markets = useMemo(
    () => (data?.markets ?? []).filter((market) => category === "all" || market.category === category),
    [category, data?.markets]
  );
  if (!data) return <div className="engineLoading"><Braces size={24} /><span>확률 원장을 불러오는 중입니다.</span></div>;
  const track = data.track;
  const cash = track.cash ?? track.initial_cash ?? 0;
  return (
    <div className="engineView polyPaperView" data-testid="engine-poly-paper-tab">
      <section className="polyPaperGate">
        <div><ShieldCheck size={18} /><span>Public data · PaperBroker</span><strong>지갑·실주문 구현 없음</strong></div>
        <p>{data.performance_gate}</p>
        <button className="button secondary" disabled={collecting || !data.enabled} onClick={() => void onCollect()} type="button">
          <RefreshCw size={15} />{collecting ? "수집 중" : "시장 수집"}
        </button>
      </section>

      {track.last_collection_error ? <TerminalWarning tone="warning">공개 시장 수집 실패 · {track.last_collection_error} · 기존 원장은 보존됩니다.</TerminalWarning> : null}
      <header className="polyPaperHeader">
        <div><span className="engineSectionLabel">독립 확률 검증 · {data.parameter_version}</span><h2>Polymarket · Crypto / Macro</h2><p>캔들 판정 엔진과 분리하고, 만기 정답으로 Brier score를 채점합니다.</p></div>
        <div className="polyTrackSummary">
          <p><span>USDC 잔액</span><strong>{money(cash)}</strong></p>
          <p><span>검증 시계</span><strong>{track.clock_valid ? `${track.elapsed_days ?? 0}/28일` : "첫 수집 대기"}</strong></p>
          <p><span>정산 표본</span><strong>N={data.calibration.n}</strong></p>
        </div>
      </header>
      <TerminalWarning tone="warning">비용 후 edge 5% 이상은 엄격 진입, 근거·정산·CLOB가 완비된 edge 미달 시장은 0.5% 소액 캘리브레이션 표본으로 분리합니다.</TerminalWarning>

      <section className="polyCalibration" data-testid="poly-calibration-card">
        <header>
          <div><span className="engineSectionLabel">객관 정산 원장</span><h3>Probability calibration</h3></div>
          <div><strong>{data.calibration.mean_brier_score === null ? "Brier —" : `Brier ${data.calibration.mean_brier_score.toFixed(4)}`}</strong><small>{data.calibration.sample_warning || `채점 N=${data.calibration.n}`}</small></div>
        </header>
        <div className="polyCalibrationCurve" role="img" aria-label="예측 확률 버킷별 실제 YES 비율">
          {data.calibration.curve.map((row) => (
            <div key={row.bucket} title={`${row.bucket} · N=${row.n}`}>
              <i className="forecast" style={{ height: `${Math.round((row.mean_forecast ?? 0) * 100)}%` }} />
              <i className="actual" style={{ height: `${Math.round((row.actual_yes_rate ?? 0) * 100)}%` }} />
              <span>{row.bucket.split("–")[0]}</span><small>{row.n}</small>
            </div>
          ))}
        </div>
        <p>회색=평균 예측 · 초록=실제 YES · {data.sample_note}</p>
      </section>

      <section className="polyMarketSection">
        <header><div><span className="engineSectionLabel">관측과 진입 후보 분리</span><h3>시장 확률 ↔ FCE 추정</h3></div><nav aria-label="Polymarket 카테고리">{(["all", "crypto", "macro"] as const).map((item) => <button className={category === item ? "active" : ""} key={item} onClick={() => setCategory(item)} type="button">{item === "all" ? "전체" : item}</button>)}</nav></header>
        <div className="polyMarketGrid">
          {markets.map((market) => <PolyMarketCard key={market.market_id} market={market} />)}
          {!markets.length ? <p className="engineEmptyLine">공개 시장 수집을 실행하면 crypto/macro 관측 시장이 표시됩니다.</p> : null}
        </div>
      </section>

      <section className="polyPositionLedger">
        <header><div><span className="engineSectionLabel">USDC 독립 회계</span><h3>페이퍼 포지션</h3></div><strong>{data.positions.filter((item) => item.status === "open").length} open</strong></header>
        {data.positions.length ? data.positions.map((position) => (
          <article key={position.market_id}><div><strong>{position.direction} <em className={`paperModeBadge ${position.entry_mode}`}>{position.entry_mode === "coverage_calibration" ? "캘리브레이션" : "엄격 edge"}</em></strong><span>{position.question}</span></div><p>{position.shares.toFixed(2)} shares @ {(position.average_price * 100).toFixed(1)}¢</p><b>{position.status === "resolved" ? `${position.pnl !== null && position.pnl >= 0 ? "+" : ""}${money(position.pnl ?? 0)}` : money(position.cost)}</b></article>
        )) : <p className="engineEmptyLine">비용 차감 edge·근거 품질·호가 유동성을 모두 통과한 포지션이 아직 없습니다.</p>}
      </section>
    </div>
  );
}

function PolyMarketCard({ market }: { market: PolyPaperMarket }) {
  const estimate = market.estimate;
  return (
    <article className={`polyMarketCard ${estimate?.trade_eligible || estimate?.coverage_eligible ? "eligible" : "observe"}`}>
      <header><span>{market.category}</span><b>{market.liquidity >= 1_000_000 ? `$${(market.liquidity / 1_000_000).toFixed(1)}M` : `$${Math.round(market.liquidity / 1000)}K`} liquidity</b></header>
      <h4>{market.question}</h4>
      <div className="polyProbabilityPair">
        <p><span>시장 YES</span><strong>{probability(market.market_probability)}</strong></p>
        <p><span>FCE 추정</span><strong>{estimate ? probability(estimate.estimated_probability) : "—"}</strong></p>
        <p><span>비용 후 edge</span><strong>{estimate?.after_cost_edge === null || estimate?.after_cost_edge === undefined ? "—" : signedProbability(estimate.after_cost_edge)}</strong></p>
      </div>
      {estimate ? <><div className="polyEstimateMeta"><span className={`quality-${estimate.estimate_quality}`}>{estimate.estimate_quality}</span><strong>{estimate.direction} 관측</strong><small>{shortTime(estimate.observed_at)}</small></div><details><summary>근거·베이스레이트 보기</summary><p>{estimate.reasoning}</p><code>{String(estimate.base_rate.model ?? "base rate")}</code>{estimate.evidence.map((item, index) => <div key={`${item.source}-${index}`}><strong>{item.claim}</strong><span>{item.source} · {shortTime(item.observed_at)}</span></div>)}</details></> : <p className="polyExclusion">추정 없음 · {exclusionLabel(market.exclusion_reason)}</p>}
      {estimate?.coverage_eligible ? <p className="polyCoverageEligible">캘리브레이션 진입 가능 · 실제 CLOB 소액 표본</p> : estimate && !estimate.trade_eligible ? <p className="polyExclusion">관측 전용 · {exclusionLabel(estimate.exclusion_reason)}</p> : null}
    </article>
  );
}

function probability(value: number | null): string { return value === null ? "—" : `${(value * 100).toFixed(1)}%`; }
function signedProbability(value: number): string { return `${value >= 0 ? "+" : ""}${(value * 100).toFixed(1)}%`; }
function money(value: number): string { return new Intl.NumberFormat("en-US", { style: "currency", currency: "USD", maximumFractionDigits: 2 }).format(value); }
function shortTime(value: string): string { return new Intl.DateTimeFormat("ko-KR", { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hour12: false }).format(new Date(value)); }
function exclusionLabel(reason: string | null): string {
  if (!reason) return "근거 평가 대기";
  return ({
    macro_base_rate_provider_unavailable: "거시 베이스레이트 공급기 미연결",
    unsupported_crypto_question: "지원하지 않는 크립토 질문",
    resolution_source_missing: "정산 출처 없음",
    resolution_time_missing: "만기 시각 없음",
    resolution_ambiguity_warning: "정산 규칙 모호",
    binary_yes_no_required: "Yes/No 이진 시장 아님",
    fee_schedule_missing: "수수료 규칙 미관측",
    liquidity_below_minimum: "유동성 하한 미달",
    resolution_too_near: "만기 임박",
    orderbook_unavailable: "호가 관측 없음",
    after_cost_edge_low: "비용 차감 edge 미달",
    estimate_quality_low: "근거 품질 낮음"
  } as Record<string, string>)[reason] ?? reason.replaceAll("_", " ");
}
