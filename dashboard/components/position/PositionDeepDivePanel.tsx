"use client";

import { useEffect, useState } from "react";
import { Check, RefreshCw } from "lucide-react";
import type { PositionDeepDive, PositionDeepDiveSignal } from "@/lib/api";
import { formatPrice } from "@/lib/format";

export function PositionDeepDivePanel({
  deepDive,
  loading,
  error,
  onRetry,
  onSaveThesis,
  onOpenEvidence
}: {
  deepDive: PositionDeepDive | null;
  loading: boolean;
  error: string;
  onRetry: () => void;
  onSaveThesis: (value: string) => Promise<void>;
  onOpenEvidence?: () => void;
}) {
  const [thesis, setThesis] = useState("");
  const [saving, setSaving] = useState(false);
  useEffect(() => {
    setThesis(deepDive?.thesis?.text || deepDive?.entry_snapshot.thesis?.text || "");
  }, [deepDive?.position_id, deepDive?.thesis?.text, deepDive?.entry_snapshot.thesis?.text]);

  if (loading && !deepDive) {
    return <aside className="positionDeepDivePanel loading" data-testid="position-deepdive-panel">교차소스 판정을 불러오는 중입니다.</aside>;
  }
  if (error) {
    return (
      <aside className="positionDeepDivePanel error" data-testid="position-deepdive-panel">
        <strong>심화 판정을 불러오지 못했습니다.</strong>
        <p>{error}</p>
        <button className="button secondary" onClick={onRetry} type="button"><RefreshCw size={14} /> 다시 시도</button>
      </aside>
    );
  }
  if (!deepDive || deepDive.status !== "ready") {
    return (
      <aside className="positionDeepDivePanel unavailable" data-testid="position-deepdive-panel">
        <header><span>FCE 판정 계기판</span><strong>교차분석 대기</strong></header>
        <p>{deepDive?.reason || "검증된 Toss 기초자산 조인이 필요합니다."}</p>
      </aside>
    );
  }

  const thesisStatus = deepDive.thesis?.status || "maintained";
  const activeSignals = deepDive.cross_signals.filter((item) => item.status === "active");
  const signalPerformanceT1 = (deepDive.ledger.signal_performance || []).filter((item) => item.horizon_days === 1);
  return (
    <aside className="positionDeepDivePanel" data-testid="position-deepdive-panel">
      <header>
        <div><span>FCE 판정 계기판</span><strong>{deepDive.underlying?.symbol} 교차 관측</strong></div>
        <time>{new Date(deepDive.as_of).toLocaleTimeString("ko-KR", { hour: "2-digit", minute: "2-digit" })}</time>
      </header>

      <section className="deepDiveBlock thesisBlock" data-testid="deepdive-thesis-block">
        <div className="deepDiveBlockTitle">
          <span>01 · 논거 대비 현재</span>
          <em className={`thesisStatus ${thesisStatus}`}>{deepDive.thesis?.status_label}</em>
        </div>
        <strong>{deepDive.thesis?.text || "진입 논거 미입력"}</strong>
        <p>{deepDive.thesis?.comparison_note}</p>
        <div className="thesisEditor">
          <input aria-label="진입 논거" maxLength={160} onChange={(event) => setThesis(event.target.value)} value={thesis} placeholder="진입 논거 한 줄" />
          <button
            aria-label="진입 논거 저장"
            disabled={saving || !thesis.trim()}
            onClick={async () => {
              setSaving(true);
              try { await onSaveThesis(thesis.trim()); } finally { setSaving(false); }
            }}
            type="button"
          >
            {saving ? <RefreshCw size={13} /> : <Check size={13} />}
          </button>
        </div>
      </section>

      <section className="deepDiveBlock crossSignalBlock" id="fce-cross-signals" data-testid="deepdive-cross-signal-block">
        <div className="deepDiveBlockTitle">
          <span>02 · FCE 교차신호</span>
          <em>{activeSignals.length}/{deepDive.cross_signals.length} 관측</em>
        </div>
        <div className="deepDiveSignalList">
          {deepDive.cross_signals.map((signal) => <CrossSignalRow key={signal.id} signal={signal} />)}
        </div>
        {onOpenEvidence ? <button className="button secondary deepDiveEvidenceLink" onClick={onOpenEvidence} type="button">차트 근거 열기</button> : null}
      </section>

      <section className="deepDiveBlock riskLedgerBlock" data-testid="deepdive-risk-ledger-block">
        <div className="deepDiveBlockTitle"><span>03 · 리스크 & 판정 기록</span></div>
        <div className="riskMetricGrid">
          <RiskMetric label="청산 거리" value={formatPercent(deepDive.risk.liquidation_distance_pct)} />
          <RiskMetric label="무효화 거리" value={formatPercent(deepDive.risk.invalidation_distance_pct)} />
          <RiskMetric label="다음 구조" value={formatOptionalPrice(deepDive.risk.next_structure_price)} />
          <RiskMetric label="보상/리스크" value={deepDive.risk.reward_risk_r == null ? "-" : `${deepDive.risk.reward_risk_r.toFixed(2)}R`} />
        </div>
        <div className={`marketReading ${deepDive.risk.market_reading?.position_alignment || "unknown"}`}>
          <strong>{deepDive.risk.market_reading?.label}</strong>
          <ul>{(deepDive.risk.market_reading?.reasons || []).map((item) => <li key={item}>{item}</li>)}</ul>
          {deepDive.risk.market_reading?.reversal_condition ? (
            <p>읽기 전환 관측선 {formatPrice(deepDive.risk.market_reading.reversal_condition.price)} · {deepDive.risk.market_reading.reversal_condition.condition}</p>
          ) : null}
        </div>
        <div className="ledgerMiniView">
          {(deepDive.ledger.performance.length ? deepDive.ledger.performance : deepDive.ledger.horizons.map((horizon) => ({ horizon_days: horizon, n: 0, hit_rate_pct: null, sample_low: true }))).map((item) => (
            <div key={item.horizon_days}>
              <span>T+{item.horizon_days}</span>
              <strong>{item.hit_rate_pct == null ? "결과 대기" : `${item.hit_rate_pct.toFixed(1)}%`}</strong>
              <em>N={item.n} · {item.sample_low ? "표본 부족" : "표본 축적"}</em>
            </div>
          ))}
        </div>
        {signalPerformanceT1.length ? (
          <div className="signalPerformanceMini" data-testid="deepdive-signal-performance">
            <span>신호 유형별 · T+1</span>
            {signalPerformanceT1.map((item) => (
              <div key={item.signal_id}>
                <span>{item.signal_label}</span>
                <strong>{item.hit_rate_pct == null ? "결과 대기" : `${item.hit_rate_pct.toFixed(1)}%`}</strong>
                <em>N={item.n} · {item.sample_low ? "표본 부족" : "표본 축적"}</em>
              </div>
            ))}
          </div>
        ) : null}
        <details className="partialSimulation">
          <summary>부분 축소 정적 계산</summary>
          {(deepDive.risk.partial_exit_simulation || []).map((item) => (
            <p key={item.reduction_pct}>{item.reduction_pct}% 축소 · 잔여 ${item.remaining_notional.toLocaleString()} · 무효화 위험 ${item.invalidation_risk_notional?.toLocaleString() ?? "-"}</p>
          ))}
        </details>
      </section>
      <footer>{deepDive.truth_policy}</footer>
    </aside>
  );
}

function CrossSignalRow({ signal }: { signal: PositionDeepDiveSignal }) {
  return (
    <article className={`deepDiveSignal ${signal.status}`} data-signal-id={signal.id}>
      <div>
        <strong>{signal.label}</strong>
        <span className="signalSources">{signal.sources.map((source) => <em key={source.id}>{source.label}</em>)}</span>
      </div>
      {signal.data.sparkline?.length ? <BasisSparkline values={signal.data.sparkline.map((item) => item.value)} /> : null}
      <p>{signal.reading} · {signal.detail}</p>
      {signal.data.warning ? <small>{signal.data.warning}</small> : null}
      <details><summary>왜 FCE만 가능한가</summary><small>{signal.moat_reason}</small></details>
    </article>
  );
}

function BasisSparkline({ values }: { values: number[] }) {
  const width = 88;
  const height = 24;
  const min = Math.min(...values);
  const max = Math.max(...values);
  const span = Math.max(max - min, 0.0001);
  const path = values.map((value, index) => {
    const x = values.length <= 1 ? width / 2 : (index / (values.length - 1)) * width;
    const y = height - 3 - ((value - min) / span) * (height - 6);
    return `${index === 0 ? "M" : "L"}${x.toFixed(1)},${y.toFixed(1)}`;
  }).join(" ");
  return <svg className="basisSparkline" viewBox={`0 0 ${width} ${height}`} aria-label="베이시스 변화"><line x1="0" x2={width} y1={height / 2} y2={height / 2} /><path d={path} /></svg>;
}

function RiskMetric({ label, value }: { label: string; value: string }) {
  return <div><span>{label}</span><strong>{value}</strong></div>;
}

function formatPercent(value: number | null | undefined) {
  return value == null ? "-" : `${value.toFixed(2)}%`;
}

function formatOptionalPrice(value: number | null | undefined) {
  return value == null ? "-" : formatPrice(value);
}
