"use client";

import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { Bot, Plus, RefreshCw, Trash2, Waves } from "lucide-react";
import { useCallback, useEffect, useMemo, useState, type FormEvent } from "react";
import { TerminalWarning } from "@/components/terminal";
import { api, type OnchainWhaleDashboard, type PaperDashboard, type PaperGateFunnel, type PaperTrade } from "@/lib/api";

const tabs = [
  { id: "battle", label: "대결" },
  { id: "positions", label: "엔진 포지션" },
  { id: "journal", label: "거래 일지" },
  { id: "status", label: "엔진 상태" },
  { id: "onchain", label: "온체인" }
] as const;

type TabId = (typeof tabs)[number]["id"];

export function EngineTradingShell() {
  const search = useSearchParams();
  const requested = search.get("tab") as TabId | null;
  const active = tabs.some((tab) => tab.id === requested) ? requested! : "battle";
  const [data, setData] = useState<PaperDashboard | null>(null);
  const [whales, setWhales] = useState<OnchainWhaleDashboard | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [starting, setStarting] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const [paper, onchain] = await Promise.all([api.paperDashboard(), api.onchainWhales()]);
      setData(paper);
      setWhales(onchain);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "엔진 트레이딩 데이터를 불러오지 못했습니다.");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { void load(); }, [load]);

  async function startBenchmark(reset = false) {
    if (reset && !window.confirm("기존 거래 기록은 보존하고 4주 비교 창만 오늘부터 다시 시작합니다.")) return;
    setStarting(true);
    setError("");
    try {
      await api.startPaperBenchmark(reset);
      await load();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "4주 대결을 시작하지 못했습니다.");
    } finally {
      setStarting(false);
    }
  }

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
      {!data ? <EngineLoading /> : active === "battle" ? <BattleView data={data} whales={whales} starting={starting} onStart={startBenchmark} /> : active === "positions" ? <PositionsView trades={data.open_trades} funnel={data.gate_funnel} /> : active === "journal" ? <JournalView trades={data.closed_trades} /> : active === "onchain" ? <OnchainView data={whales} onReload={load} /> : <EngineStatusView data={data} />}
    </div>
  );
}

function BattleView({ data, whales, starting, onStart }: { data: PaperDashboard; whales: OnchainWhaleDashboard | null; starting: boolean; onStart: (reset?: boolean) => void }) {
  const board = data.scoreboard;
  const benchmark = board.benchmark;
  const competition = board.competition;
  const recent = board.recent_28d;
  return (
    <div className="engineView" data-testid="engine-battle-tab">
      <section className={`engineActivationStrip ${data.activation.running ? "running" : "blocked"}`} data-testid="paper-activation-strip">
        <div>
          <strong>{data.activation.running ? "가동 중" : "가동 확인 필요"}</strong>
          <span>{benchmark.started ? `${shortDate(benchmark.started_at)}~${shortDate(benchmark.ends_at)}` : "대결 시작 전"}</span>
          <small className="engineActivationProof">{activationProof(data.activation)}</small>
          <small className="engineActivationProof">내 실계좌 체결 {board.user_fill_sync.stored_fill_count ?? 0}건 수집 · 마지막 {board.user_fill_sync.last_fill_at ? shortDateTime(board.user_fill_sync.last_fill_at) : statusLabel(board.user_fill_sync.status)}</small>
        </div>
        <div className="engineActivationItems">
          {data.activation.items.map((item) => <span className={item.ok ? "ok" : "error"} key={item.id} title={item.reason ?? "정상"}><i />{item.label} {item.value}</span>)}
        </div>
        {benchmark.started
          ? <button className="button secondary" disabled={starting} onClick={() => onStart(true)} type="button">창 다시 시작</button>
          : <button className="button" disabled={starting} onClick={() => onStart(false)} type="button">{starting ? "시작 중" : "대결 시작"}</button>}
      </section>
      <section className="engineBattleHero">
        <div>
          <span className="engineSectionLabel">대결 기간 판정 · {shortDate(competition.started_at)} 이후</span>
          <strong className={competition.engine_leading ? "positive" : "neutral"}>{competition.engine_leading ? "엔진 우세" : competition.verdict === "insufficient_samples" ? "표본 부족" : "우세 미확정"}</strong>
          <small>엔진 채점 N={competition.engine.scored_trade_count} · 나 채점 N={competition.user.scored_trade_count}</small>
          {!competition.engine.sample_sufficient || !competition.user.sample_sufficient ? <small>각 N≥10 전까지 우세 판정 유보</small> : null}
        </div>
        <EquityComparison engine={competition.equity_curve.engine} user={competition.equity_curve.user} />
      </section>
      <div className="engineWindowHeader"><strong>대결 기간 성과</strong><span>판정용 · 동일 시작 앵커</span></div>
      <section className="engineMetricGrid">
        <ComparisonMetric label="수익률" engine={pct(competition.engine.net_return_pct)} user={pct(competition.user.net_return_pct)} />
        <ComparisonMetric label="승률" engine={winRate(competition.engine)} user={winRate(competition.user)} />
        <ComparisonMetric label="수익팩터" engine={ratio(competition.engine.profit_factor)} user={ratio(competition.user.profit_factor)} />
        <ComparisonMetric label="최대 낙폭" engine={pct(competition.engine.mdd_pct)} user={pct(competition.user.mdd_pct)} inverse />
      </section>
      <div className="engineWindowHeader"><strong>최근 28일 참고 성과</strong><span>표시용 · 대결 판정에 미사용</span></div>
      <section className="engineMetricGrid engineRecentMetrics">
        <ComparisonMetric label="수익률" engine={pct(recent.engine.net_return_pct)} user={pct(recent.user.net_return_pct)} />
        <ComparisonMetric label="승률" engine={winRate(recent.engine)} user={winRate(recent.user)} />
        <ComparisonMetric label="종료 거래" engine={`N=${recent.engine.trade_count}`} user={`N=${recent.user.trade_count}`} />
        <ComparisonMetric label="중립 종료" engine={`N=${recent.engine.neutral_count}`} user={`N=${recent.user.neutral_count}`} />
      </section>
      <p className="engineFairnessNote">{board.fairness_note} · N은 종료 거래 수입니다.</p>
      <WhaleBenchmarkReference data={whales} />
    </div>
  );
}

function WhaleBenchmarkReference({ data }: { data: OnchainWhaleDashboard | null }) {
  const validated = (data?.wallets ?? []).filter((wallet) => wallet.review.state === "validated");
  return (
    <section className="whaleBattleReference" data-testid="whale-battle-reference">
      <div><Waves size={17} /><span>고래 참고군</span><strong>{validated.length ? `검증 ${validated.length}지갑` : "검증 표본 대기"}</strong></div>
      <p>{validated.length ? validated.slice(0, 3).map((wallet) => `${wallet.label} 1R ${wallet.review.win_1r_pct}% (N=${wallet.review.sample_size})`).join(" · ") : "candidate 고래는 엔진 vs 나 판정에 포함하지 않습니다. N≥30·CI 하한 55% 승격 후에만 3자 참고군으로 표시됩니다."}</p>
    </section>
  );
}

function OnchainView({ data, onReload }: { data: OnchainWhaleDashboard | null; onReload: () => Promise<void> }) {
  const [address, setAddress] = useState("");
  const [label, setLabel] = useState("");
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");

  async function addWallet(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy(true); setMessage("");
    try {
      await api.addOnchainWhale({ address, ...(label.trim() ? { label: label.trim() } : {}) });
      setAddress(""); setLabel("");
      await onReload();
    } catch (reason) {
      setMessage(reason instanceof Error ? reason.message : "지갑을 등록하지 못했습니다.");
    } finally { setBusy(false); }
  }

  async function removeWallet(walletAddress: string) {
    setBusy(true); setMessage("");
    try { await api.removeOnchainWhale(walletAddress); await onReload(); }
    catch (reason) { setMessage(reason instanceof Error ? reason.message : "지갑을 삭제하지 못했습니다."); }
    finally { setBusy(false); }
  }

  async function collectNow() {
    setBusy(true); setMessage("");
    try { await api.collectOnchainWhales(); await onReload(); }
    catch (reason) { setMessage(reason instanceof Error ? reason.message : "온체인 수집에 실패했습니다."); }
    finally { setBusy(false); }
  }

  if (!data) return <EngineLoading />;
  return (
    <div className="engineView onchainView" data-testid="engine-onchain-tab">
      <section className="onchainToolbar">
        <div><span className="engineSectionLabel">Hyperliquid · read only</span><strong>등록 {data.wallet_count}/{data.max_wallets}</strong><small>최소 관측 규모 {compactMoney(data.minimum_event_size_usd)} USDT · 별칭은 사용자 추정</small></div>
        <button className="button secondary" disabled={busy || !data.wallet_count} onClick={collectNow} type="button"><RefreshCw size={15} />지금 수집</button>
      </section>
      <form className="onchainAddForm" onSubmit={addWallet}>
        <label><span>지갑 주소</span><input aria-label="Hyperliquid 지갑 주소" onChange={(event) => setAddress(event.target.value)} placeholder="0x…" required value={address} /></label>
        <label><span>추정 별칭</span><input aria-label="고래 별칭" onChange={(event) => setLabel(event.target.value)} placeholder="예: BTC 스윙 A" value={label} /></label>
        <button className="button" disabled={busy || !address.trim()} type="submit"><Plus size={15} />등록</button>
      </form>
      {message ? <TerminalWarning tone="error">{message}</TerminalWarning> : null}
      <p className="onchainPolicy">{data.policy}</p>
      {!data.wallets.length ? <EngineEmpty title="등록된 고래 지갑 없음" body="지갑을 수동 등록하면 확정 체결 이후부터 관측과 candidate 채점이 시작됩니다." /> : (
        <div className="onchainWalletList">
          {data.wallets.map((wallet) => (
            <section className={`onchainWalletRow ${wallet.review.state}`} key={wallet.address}>
              <header><div><strong>{wallet.label}</strong><code>{wallet.address_short}</code><small>{wallet.alias_disclaimer}</small></div><div><span>{whaleState(wallet.review.state)}</span><b>{wallet.review.sample_size >= 30 && wallet.review.win_1r_pct !== null ? `1R ${wallet.review.win_1r_pct}% · N=${wallet.review.sample_size}` : `축적 N=${wallet.review.sample_size} · 잔여 ${wallet.review.remaining_samples}`}</b><button aria-label={`${wallet.label} 삭제`} disabled={busy} onClick={() => void removeWallet(wallet.address)} title="워치리스트에서 삭제" type="button"><Trash2 size={15} /></button></div></header>
              <div className="onchainPositions">
                {wallet.positions.length ? wallet.positions.map((position) => <div key={position.coin}><strong>{position.coin} {position.side === "long" ? "롱" : "숏"}</strong><span>{compactMoney(position.size_usd)} · 진입 {price(position.entry_px)}</span><b className={(position.unrealized_pnl ?? 0) >= 0 ? "positive" : "negative"}>{money(position.unrealized_pnl ?? 0)} USDT</b></div>) : <p>현재 공개 포지션 없음</p>}
              </div>
            </section>
          ))}
        </div>
      )}
    </div>
  );
}

function PositionsView({ trades, funnel }: { trades: PaperTrade[]; funnel: PaperGateFunnel }) {
  if (!trades.length) return <EngineEmpty title="현재 엔진 포지션 없음" body={funnelSummary(funnel)} actionHref="/engine?tab=status" actionLabel="게이트 퍼널 보기" />;
  return <section className="enginePositionGrid" data-testid="engine-positions-tab">{trades.map((trade) => <PaperPositionCard key={trade.id} trade={trade} />)}</section>;
}

function PaperPositionCard({ trade }: { trade: PaperTrade }) {
  const evidence = evidenceLines(trade).slice(0, 3);
  const validationBootstrap = record(trade.entry_evidence).entry_mode === "validation_bootstrap";
  return (
    <article className="enginePositionCard">
      <header><div><strong>{trade.symbol}</strong><span>{direction(trade.direction)} · {trade.leverage}x{validationBootstrap ? " · 검증 시작 진입" : ""}</span></div><b>{pct(trade.net_return_pct)}</b></header>
      <dl><div><dt>진입</dt><dd>{price(trade.entry_price)}</dd></div><div><dt>무효화</dt><dd>{price(trade.invalidation_price)}</dd></div><div><dt>익절1</dt><dd>{price(trade.take_profit_price)}</dd></div><div><dt>익절2</dt><dd>{price(trade.take_profit_2_price)}</dd></div></dl>
      {trade.exit_monitor ? <p className="engineExitMonitor">자동 청산 감시 · 무효화까지 {signedPct(trade.exit_monitor.invalidation_distance_pct)} · 익절1까지 {signedPct(trade.exit_monitor.take_profit_distance_pct)}</p> : null}
      <div className="engineEvidence"><span>진입 근거</span>{evidence.map((line, index) => <p key={`${line}-${index}`}>{line}</p>)}</div>
    </article>
  );
}

function JournalView({ trades }: { trades: PaperTrade[] }) {
  const search = useSearchParams();
  const filter = search.get("filter") ?? "all";
  const rows = trades.filter((trade) => filter === "win" ? trade.net_pnl_usdt > 0 && !isNeutralExit(trade.exit_reason) : filter === "loss" ? trade.net_pnl_usdt <= 0 && !isNeutralExit(trade.exit_reason) : filter === "neutral" ? isNeutralExit(trade.exit_reason) : filter === "all" ? true : trade.exit_reason === filter);
  return (
    <div className="engineView" data-testid="engine-journal-tab">
      <div className="engineFilters">{[["all","전체"],["win","승"],["loss","패"],["neutral","중립"],["invalidation_breach","무효화"],["opposite_stance_flip","반대 전환"],["time_decay","시간 감쇠"]].map(([id,label]) => <Link className={filter === id ? "active" : ""} href={`/engine?tab=journal&filter=${id}`} key={id}>{label}</Link>)}</div>
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
      <CandidateReviewCard review={calibration.candidate_review} />
    </div>
  );
}

function CandidateReviewCard({ review }: { review: PaperDashboard["calibration"]["candidate_review"] }) {
  const items = review?.items ?? [];
  return (
    <section className="engineStatusCard engineCandidateReview" data-testid="candidate-review-status">
      <header><h2>Candidate 심사 현황</h2><span>승격 제안 {review?.pending_promotions ?? 0}</span></header>
      {items.length ? <div className="candidateReviewRows">{items.map((item) => (
        <div key={item.engine}>
          <strong>{item.label}</strong>
          <span>N {item.sample_size} · 1R {candidateRate(item.win_1r_pct, item.win_1r_ci)}</span>
          <small>승격까지 {item.remaining_samples}표본 · live {item.source_counts.live ?? 0}</small>
          <b>{candidateStatus(item.status)}</b>
        </div>
      ))}</div> : <p className="engineEmptyLine">일일 채점 잡 실행을 기다리는 중입니다.</p>}
      <p className="engineEmptyLine">백테스트와 라이브 검증 표본을 분리 집계합니다.</p>
    </section>
  );
}

function GateFunnel({ funnel }: { funnel: PaperGateFunnel }) {
  const visible = funnel.stages.filter((stage) => ["evaluated", "confirmed_flip", "checklist", "signature_gate", "entered"].includes(stage.id));
  const pills = funnel.pill_diagnostics;
  return (
    <section className="engineStatusCard engineGateFunnel" data-testid="paper-gate-funnel">
      <header><h2>최근 {funnel.period_days}일 진입 게이트</h2><span>확정 캔들 기준</span></header>
      <div className="engineFunnelStages">
        {visible.map((stage, index) => (
          <div key={stage.id}>
            <span>{stage.label}</span>
            <strong>{stage.count}</strong>
            {stage.rejection_top3?.length ? (
              <ul>{stage.rejection_top3.map((reason) => <li key={reason.detail}>{reason.detail} · {reason.count}회</li>)}</ul>
            ) : <small>탈락 사유 없음</small>}
            {index < visible.length - 1 ? <i>→</i> : null}
          </div>
        ))}
      </div>
      {funnel.checklist_pass_rates?.length ? (
        <div className="engineChecklistRates" aria-label="체크리스트 항목별 통과율">
          {funnel.checklist_pass_rates.map((item) => (
            <span key={item.key}><b>{item.label}</b><small>{item.pass_rate_pct}% · {item.passed}/{item.evaluated}</small></span>
          ))}
        </div>
      ) : null}
      <p>{funnel.top_rejection ? `최다 탈락: ${funnel.top_rejection.label} · ${funnel.top_rejection.count}회` : "평가가 쌓이면 최다 탈락 관문을 표시합니다."}</p>
      {funnel.signature_gate_note ? <p>{funnel.signature_gate_note}</p> : null}
      <p data-testid="event-pill-diagnostics">
        최근 {funnel.period_days}일 알약 렌더 {pills?.rendered ?? 0}개 · {pillBottleneckLabel(pills?.bottleneck)}
      </p>
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
function EngineEmpty({ title, body, actionHref, actionLabel }: { title: string; body: string; actionHref?: string; actionLabel?: string }) { return <div className="engineEmpty"><Bot size={24} /><strong>{title}</strong><span>{body}</span>{actionHref && actionLabel ? <Link className="button secondary" href={actionHref}>{actionLabel}</Link> : null}</div>; }

function funnelSummary(funnel: PaperGateFunnel): string {
  if (!funnel.evaluations) return "엔진 정상 가동 · 첫 확정 캔들 평가를 기다리는 중입니다. 무거래는 관성 설계상 정상입니다.";
  const flip = funnel.stages.find((stage) => stage.id === "confirmed_flip")?.count ?? 0;
  const top = funnel.top_rejection;
  return `엔진 정상 가동 · 이번 주 flip ${flip}회 → 진입 ${funnel.entered}회. 무거래는 관성 설계상 정상이며 7일 지속 시 자동 진단합니다${top ? ` · 최다 탈락: ${top.label} ${top.count}회` : ""}`;
}

function activationProof(activation: PaperDashboard["activation"]): string {
  return `누적 flip ${activation.flip_count_7d ?? 0} · 진입 ${activation.entry_count_7d ?? 0} · 다음 확정 캔들까지 ${eta(activation.next_confirmed_bar_minutes)}`;
}

function eta(minutes: number | null | undefined): string {
  if (typeof minutes !== "number" || !Number.isFinite(minutes)) return "대기";
  if (minutes >= 60) return `${Math.floor(minutes / 60)}h ${Math.max(0, Math.round(minutes % 60))}m`;
  return `${Math.max(1, Math.round(minutes))}m`;
}

function evidenceLines(trade: PaperTrade): string[] { const raw = trade.entry_evidence.items; if (!Array.isArray(raw)) return ["진입 규정 게이트 통과"]; return raw.map((item) => { const row = record(item); return String(row.claim ?? row.label ?? row.reason ?? "검증 근거"); }); }
function record(value: unknown): Record<string, unknown> { return value && typeof value === "object" && !Array.isArray(value) ? value as Record<string, unknown> : {}; }
function actionSummary(actions: Array<Record<string, unknown>>): string { return actions.length ? actions.slice(0, 3).map((item) => String(item.reason ?? item.transition ?? item.signature_key ?? "자율 조치")).join(" · ") : "기록된 자율 조치 없음"; }
function statusLabel(value: string): string { return ({ scheduled: "예정", experiment: "실험 중", adopted: "적용됨", rolled_back: "롤백됨", vetoed: "거부됨", dwell_blocked: "대기 기간", waiting: "수집 대기", not_configured: "인증 확인 필요", error: "수집 오류", ok: "정상" } as Record<string,string>)[value] ?? value; }
function candidateStatus(value: string): string { return ({ candidate: "표본 축적", promotion_proposed: "승격 제안", validated: "검증됨", degraded: "저하" } as Record<string,string>)[value] ?? value; }
function candidateRate(value: number | null, ci: [number, number] | null): string { return value === null ? "유보" : `${value.toFixed(1)}%${ci ? ` (CI ${ci[0]}~${ci[1]})` : ""}`; }
function pillBottleneckLabel(value: string | null | undefined): string { return ({ window_events: "최근 이벤트 없음", validated: "검증 통계 단계에서 최다 탈락", confirmed: "확정 캔들 단계에서 최다 탈락", event_mapping: "이벤트-캔들 매핑 단계에서 최다 탈락" } as Record<string,string>)[value ?? ""] ?? "진단 표본 대기"; }
function stanceLabel(value: Record<string, unknown>): string { return ({ long: "상방", long_leaning: "상방", short: "하방", short_leaning: "하방", conflicted: "충돌" } as Record<string,string>)[String(value.stance ?? "")] ?? "판단 유보"; }
function direction(value: string): string { return value === "long" ? "롱" : "숏"; }
function exitReason(value: string | null): string { return ({ invalidation_breach: "무효화 이탈", breakeven_stop: "본전 스탑", opposite_stance_flip: "반대 스탠스 전환", take_profit_pressure: "익절 압력 지속", take_profit_2: "익절2 도달", time_decay: "시간 감쇠 · 중립", time_stop: "기존 시간 종료 · 중립" } as Record<string,string>)[value ?? ""] ?? "기록 없음"; }
function pct(value: number): string { return `${value > 0 ? "+" : ""}${Number(value || 0).toFixed(2)}%`; }
function signedPct(value: number): string { return `${value > 0 ? "+" : ""}${Number(value || 0).toFixed(2)}%`; }
function ratio(value: number | null): string { return value === null ? "유보" : Number(value).toFixed(2); }
function winRate(value: PaperDashboard["scoreboard"]["engine"]): string { return value.sample_sufficient && value.win_rate_pct !== null ? pct(value.win_rate_pct) : `유보 · N=${value.scored_trade_count}`; }
function isNeutralExit(value: string | null): boolean { return value === "time_stop" || value === "time_decay"; }
function price(value: number | null): string { if (value === null || !Number.isFinite(value)) return "-"; return value >= 100 ? value.toLocaleString(undefined, { maximumFractionDigits: 2 }) : value.toFixed(value >= 1 ? 4 : 6); }
function money(value: number): string { return `${value > 0 ? "+" : ""}${Number(value).toFixed(2)}`; }
function compactMoney(value: number): string { return value >= 1_000_000 ? `${(value / 1_000_000).toFixed(1)}M` : value >= 1_000 ? `${Math.round(value / 1_000)}K` : value.toFixed(0); }
function whaleState(value: string): string { return ({ validated: "검증됨", degraded: "성적 저하", candidate: "표본 축적", quarantined: "격리" } as Record<string,string>)[value] ?? "표본 축적"; }
function metricTone(value: string, inverse: boolean): string { const number = Number(value.replace(/[+%,]/g, "")); if (!Number.isFinite(number) || number === 0) return "neutral"; const positive = inverse ? number < 0 : number > 0; return positive ? "positive" : "negative"; }
function shortDate(value: string | null): string { return value ? new Date(value).toLocaleDateString("ko-KR", { month: "numeric", day: "numeric" }) : "-"; }
function shortDateTime(value: string): string { return new Date(value).toLocaleString("ko-KR", { month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit" }); }
