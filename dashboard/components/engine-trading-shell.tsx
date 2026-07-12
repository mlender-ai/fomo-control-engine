"use client";

import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { Bot, RefreshCw } from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";
import { TerminalWarning } from "@/components/terminal";
import { api, type PaperDashboard, type PaperGateFunnel, type PaperTrade } from "@/lib/api";

const tabs = [
  { id: "battle", label: "대결" },
  { id: "positions", label: "엔진 포지션" },
  { id: "journal", label: "거래 일지" },
  { id: "status", label: "엔진 상태" }
] as const;

type TabId = (typeof tabs)[number]["id"];

export function EngineTradingShell() {
  const search = useSearchParams();
  const requested = search.get("tab") as TabId | null;
  const active = tabs.some((tab) => tab.id === requested) ? requested! : "battle";
  const [data, setData] = useState<PaperDashboard | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  const load = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      setData(await api.paperDashboard());
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "엔진 트레이딩 데이터를 불러오지 못했습니다.");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { void load(); }, [load]);

  return (
    <div className="page engineTradingPage" data-testid="engine-trading-page">
      <header className="pageHeader engineTradingHeader">
        <div>
          <p className="eyebrow">Paper benchmark · read only</p>
          <h1>엔진 트레이딩</h1>
          <p className="subtle">엔진의 가상 매매를 내 실계좌와 같은 기간으로 비교합니다. 실제 주문은 실행하지 않습니다.</p>
        </div>
        <button className="button secondary" onClick={load} disabled={loading} type="button"><RefreshCw size={16} />새로고침</button>
      </header>

      <nav className="engineTabs" aria-label="엔진 트레이딩 보기">
        {tabs.map((tab) => <Link className={active === tab.id ? "active" : ""} href={`/engine?tab=${tab.id}`} key={tab.id}>{tab.label}</Link>)}
      </nav>

      {error ? <TerminalWarning tone="error">{error}</TerminalWarning> : null}
      {!data ? <EngineLoading /> : active === "battle" ? <BattleView data={data} /> : active === "positions" ? <PositionsView trades={data.open_trades} funnel={data.gate_funnel} /> : active === "journal" ? <JournalView trades={data.closed_trades} /> : <EngineStatusView data={data} />}
    </div>
  );
}

function BattleView({ data }: { data: PaperDashboard }) {
  const board = data.scoreboard;
  return (
    <div className="engineView" data-testid="engine-battle-tab">
      <section className="engineBattleHero">
        <div>
          <span className="engineSectionLabel">4주 롤링 판정</span>
          <strong className={board.rolling_4w.engine_leading ? "positive" : "neutral"}>{board.rolling_4w.engine_leading ? "엔진 우세" : "우세 미확정"}</strong>
          <small>엔진 N={board.engine.trade_count} · 나 N={board.user.trade_count}</small>
        </div>
        <EquityComparison engine={board.equity_curve.engine} user={board.equity_curve.user} />
      </section>
      <section className="engineMetricGrid">
        <ComparisonMetric label="수익률" engine={pct(board.engine.net_return_pct)} user={pct(board.user.net_return_pct)} />
        <ComparisonMetric label="승률" engine={pct(board.engine.win_rate_pct)} user={pct(board.user.win_rate_pct)} />
        <ComparisonMetric label="수익팩터" engine={ratio(board.engine.profit_factor)} user={ratio(board.user.profit_factor)} />
        <ComparisonMetric label="최대 낙폭" engine={pct(board.engine.mdd_pct)} user={pct(board.user.mdd_pct)} inverse />
      </section>
      <p className="engineFairnessNote">{board.fairness_note} · N은 종료 거래 수입니다.</p>
    </div>
  );
}

function PositionsView({ trades, funnel }: { trades: PaperTrade[]; funnel: PaperGateFunnel }) {
  if (!trades.length) return <EngineEmpty title="현재 엔진 포지션 없음" body={funnelSummary(funnel)} />;
  return <section className="enginePositionGrid" data-testid="engine-positions-tab">{trades.map((trade) => <PaperPositionCard key={trade.id} trade={trade} />)}</section>;
}

function PaperPositionCard({ trade }: { trade: PaperTrade }) {
  const evidence = evidenceLines(trade).slice(0, 3);
  return (
    <article className="enginePositionCard">
      <header><div><strong>{trade.symbol}</strong><span>{direction(trade.direction)} · {trade.leverage}x</span></div><b>{pct(trade.net_return_pct)}</b></header>
      <dl><div><dt>진입</dt><dd>{price(trade.entry_price)}</dd></div><div><dt>무효화</dt><dd>{price(trade.invalidation_price)}</dd></div><div><dt>익절1</dt><dd>{price(trade.take_profit_price)}</dd></div></dl>
      <div className="engineEvidence"><span>진입 근거</span>{evidence.map((line, index) => <p key={`${line}-${index}`}>{line}</p>)}</div>
    </article>
  );
}

function JournalView({ trades }: { trades: PaperTrade[] }) {
  const search = useSearchParams();
  const filter = search.get("filter") ?? "all";
  const rows = trades.filter((trade) => filter === "win" ? trade.net_pnl_usdt > 0 : filter === "loss" ? trade.net_pnl_usdt <= 0 : filter === "all" ? true : trade.exit_reason === filter);
  return (
    <div className="engineView" data-testid="engine-journal-tab">
      <div className="engineFilters">{[["all","전체"],["win","승"],["loss","패"],["invalidation_breach","무효화"],["opposite_stance_flip","반대 전환"],["time_stop","시간 종료"]].map(([id,label]) => <Link className={filter === id ? "active" : ""} href={`/engine?tab=journal&filter=${id}`} key={id}>{label}</Link>)}</div>
      {!rows.length ? <EngineEmpty title="표시할 거래 없음" body="선택한 조건의 종료 거래가 없습니다." /> : <div className="engineJournalList">{rows.map((trade) => <PaperJournalRow key={trade.id} trade={trade} />)}</div>}
    </div>
  );
}

function PaperJournalRow({ trade }: { trade: PaperTrade }) {
  return (
    <details className="engineJournalRow">
      <summary><strong>{trade.symbol}</strong><span>{direction(trade.direction)}</span><b className={trade.net_pnl_usdt >= 0 ? "positive" : "negative"}>{pct(trade.net_return_pct)}</b><span>{trade.holding_bars}캔들</span><span>{exitReason(trade.exit_reason)}</span></summary>
      <div className="engineJournalDetail"><section><h3>진입 당시</h3><p>스탠스 {stanceLabel(trade.stance_snapshot)}</p>{evidenceLines(trade).slice(0, 4).map((line) => <p key={line}>{line}</p>)}</section><section><h3>청산</h3><p>{exitReason(trade.exit_reason)} · {price(trade.exit_price)}</p><p>비용 차감 net {money(trade.net_pnl_usdt)} USDT</p><p>{trade.loss_tags.length ? trade.loss_tags.join(" · ") : "채점 결과는 판단 원장에 기록됨"}</p></section></div>
    </details>
  );
}

function EngineStatusView({ data }: { data: PaperDashboard }) {
  const calibration = data.calibration;
  const report = calibration.weekly_report;
  const digest = record(report.improvement_digest);
  const suggestions = calibration.suggestions.slice(0, 8);
  const counts = calibration.signature_state_counts;
  return (
    <div className="engineView engineStatusGrid" data-testid="engine-status-tab">
      <GateFunnel funnel={data.gate_funnel} />
      <section className="engineStatusCard engineDigest"><span className="engineSectionLabel">이번 주 개선</span><h2>{String(digest.headline ?? digest.summary ?? "이번 주 유의미한 개선 없음")}</h2><p>{String(digest.honesty_line ?? report.sample_warning ?? "표본과 조치 이력을 같은 주 단위로 비교합니다.")}</p></section>
      {data.performance_action.poor ? <section className="engineCausalRow"><div><span>페이퍼 부진</span><strong>{data.performance_action.summary}</strong></div><i>→</i><div><span>같은 기간 엔진 조치</span><strong>{actionSummary(data.performance_action.actions)}</strong></div></section> : null}
      <section className="engineStatusCard"><header><h2>파라미터 자율 피드</h2><span>예정 {calibration.suggestion_status_counts.scheduled ?? 0} · 실험 {calibration.suggestion_status_counts.experiment ?? 0}</span></header>{suggestions.length ? suggestions.map((item) => <div className="engineFeedRow" key={item.id}><span>{item.title}</span><b>{statusLabel(item.status)}</b></div>) : <p className="engineEmptyLine">진행 중인 변경이 없습니다.</p>}</section>
      <section className="engineStatusCard"><header><h2>시그니처 상태</h2><span>변동만 추적</span></header><div className="signatureCounts"><div><strong>{counts.validated ?? 0}</strong><span>검증됨</span></div><div><strong>{counts.degraded ?? 0}</strong><span>저하</span></div><div><strong>{counts.quarantined ?? 0}</strong><span>격리</span></div><div><strong>{counts.candidate ?? 0}</strong><span>표본 축적</span></div></div></section>
    </div>
  );
}

function GateFunnel({ funnel }: { funnel: PaperGateFunnel }) {
  const visible = funnel.stages.filter((stage) => ["evaluated", "confirmed_flip", "checklist", "signature_gate", "entered"].includes(stage.id));
  return (
    <section className="engineStatusCard engineGateFunnel" data-testid="paper-gate-funnel">
      <header><h2>최근 {funnel.period_days}일 진입 게이트</h2><span>확정 캔들 기준</span></header>
      <div className="engineFunnelStages">
        {visible.map((stage, index) => (
          <div key={stage.id}><span>{stage.label}</span><strong>{stage.count}</strong>{index < visible.length - 1 ? <i>→</i> : null}</div>
        ))}
      </div>
      <p>{funnel.top_rejection ? `최다 탈락: ${funnel.top_rejection.label} · ${funnel.top_rejection.count}회` : "평가가 쌓이면 최다 탈락 관문을 표시합니다."}</p>
    </section>
  );
}

function EquityComparison({ engine, user }: { engine: Array<{ ts: string; return_pct: number }>; user: Array<{ ts: string; return_pct: number }> }) {
  const hasSeries = engine.length > 1 || user.length > 1;
  const all = useMemo(() => [...engine.map((p) => p.return_pct), ...user.map((p) => p.return_pct), 0], [engine, user]);
  const min = Math.min(...all); const max = Math.max(...all); const span = max - min || 1;
  const path = (points: Array<{ return_pct: number }>) => points.length ? points.map((point, index) => `${index ? "L" : "M"}${(index / Math.max(points.length - 1, 1)) * 100},${90 - ((point.return_pct - min) / span) * 80}`).join(" ") : "";
  return <div className="engineEquityChart" role="img" aria-label="엔진과 내 계좌 누적 수익률"><svg viewBox="0 0 100 100" preserveAspectRatio="none"><line x1="0" x2="100" y1={90 - ((0 - min) / span) * 80} y2={90 - ((0 - min) / span) * 80} /><path className="engineLine" d={path(engine)} /><path className="userLine" d={path(user)} /></svg>{!hasSeries ? <p className="engineChartEmpty">종료 거래가 쌓이면 같은 기간의 수익률 곡선을 비교합니다.</p> : null}<div><span className="engineLegend">엔진 페이퍼</span><span className="userLegend">내 실계좌</span></div></div>;
}

function ComparisonMetric({ label, engine, user, inverse = false }: { label: string; engine: string; user: string; inverse?: boolean }) { return <article className="engineMetric"><span>{label}</span><div><p><small>엔진</small><strong className={metricTone(engine, inverse)}>{engine}</strong></p><p><small>나</small><strong className={metricTone(user, inverse)}>{user}</strong></p></div></article>; }
function EngineLoading() { return <div className="engineLoading"><Bot size={24} /><span>엔진 기록을 불러오는 중입니다.</span></div>; }
function EngineEmpty({ title, body }: { title: string; body: string }) { return <div className="engineEmpty"><Bot size={24} /><strong>{title}</strong><span>{body}</span></div>; }

function funnelSummary(funnel: PaperGateFunnel): string {
  if (!funnel.evaluations) return "확정 캔들 평가가 시작되면 진입 게이트별 통과 수를 표시합니다.";
  const flip = funnel.stages.find((stage) => stage.id === "confirmed_flip")?.count ?? 0;
  const top = funnel.top_rejection;
  return `이번 주 스탠스 전환 ${flip}회 중 진입 ${funnel.entered}회${top ? ` · 최다 탈락: ${top.label} ${top.count}회` : ""}`;
}

function evidenceLines(trade: PaperTrade): string[] { const raw = trade.entry_evidence.items; if (!Array.isArray(raw)) return ["진입 규정 게이트 통과"]; return raw.map((item) => { const row = record(item); return String(row.claim ?? row.label ?? row.reason ?? "검증 근거"); }); }
function record(value: unknown): Record<string, unknown> { return value && typeof value === "object" && !Array.isArray(value) ? value as Record<string, unknown> : {}; }
function actionSummary(actions: Array<Record<string, unknown>>): string { return actions.length ? actions.slice(0, 3).map((item) => String(item.reason ?? item.transition ?? item.signature_key ?? "자율 조치")).join(" · ") : "기록된 자율 조치 없음"; }
function statusLabel(value: string): string { return ({ scheduled: "예정", experiment: "실험 중", adopted: "적용됨", rolled_back: "롤백됨", vetoed: "거부됨", dwell_blocked: "대기 기간" } as Record<string,string>)[value] ?? value; }
function stanceLabel(value: Record<string, unknown>): string { return ({ long: "상방", long_leaning: "상방", short: "하방", short_leaning: "하방", conflicted: "충돌" } as Record<string,string>)[String(value.stance ?? "")] ?? "판단 유보"; }
function direction(value: string): string { return value === "long" ? "롱" : "숏"; }
function exitReason(value: string | null): string { return ({ invalidation_breach: "무효화 이탈", breakeven_stop: "본전 스탑", opposite_stance_flip: "반대 스탠스 전환", take_profit_pressure: "익절 압력 지속", time_stop: "최대 보유시간" } as Record<string,string>)[value ?? ""] ?? "기록 없음"; }
function pct(value: number): string { return `${value > 0 ? "+" : ""}${Number(value || 0).toFixed(2)}%`; }
function ratio(value: number | null): string { return value === null ? "유보" : Number(value).toFixed(2); }
function price(value: number | null): string { if (value === null || !Number.isFinite(value)) return "-"; return value >= 100 ? value.toLocaleString(undefined, { maximumFractionDigits: 2 }) : value.toFixed(value >= 1 ? 4 : 6); }
function money(value: number): string { return `${value > 0 ? "+" : ""}${Number(value).toFixed(2)}`; }
function metricTone(value: string, inverse: boolean): string { const number = Number(value.replace(/[+%,]/g, "")); if (!Number.isFinite(number) || number === 0) return "neutral"; const positive = inverse ? number < 0 : number > 0; return positive ? "positive" : "negative"; }
