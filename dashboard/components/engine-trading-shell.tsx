"use client";

import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { Activity, Bot, Building2, Plus, Radar, RefreshCw, ShieldCheck, Trash2, Waves } from "lucide-react";
import { useCallback, useEffect, useMemo, useState, type FormEvent } from "react";
import { TerminalWarning } from "@/components/terminal";
import { StockPaperEntryChart } from "@/components/StockPaperEntryChart";
import { api, type OnchainWhaleDashboard, type PaperDashboard, type PaperGateFunnel, type PaperTrade, type StanceBacktestDashboard, type StockPaperDashboard, type StockPaperTrack } from "@/lib/api";

const tabs = [
  { id: "battle", label: "대결" },
  { id: "stocks", label: "주식 트랙" },
  { id: "positions", label: "엔진 포지션" },
  { id: "journal", label: "거래 일지" },
  { id: "status", label: "엔진 상태" },
  { id: "onchain", label: "고래 검증" }
] as const;

type TabId = (typeof tabs)[number]["id"];

export function EngineTradingShell() {
  const search = useSearchParams();
  const requested = search.get("tab") as TabId | null;
  const active = tabs.some((tab) => tab.id === requested) ? requested! : "battle";
  const [data, setData] = useState<PaperDashboard | null>(null);
  const [stockData, setStockData] = useState<StockPaperDashboard | null>(null);
  const [stanceData, setStanceData] = useState<StanceBacktestDashboard | null>(null);
  const [whales, setWhales] = useState<OnchainWhaleDashboard | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [whaleError, setWhaleError] = useState("");
  const [stockError, setStockError] = useState("");
  const [stanceError, setStanceError] = useState("");
  const [starting, setStarting] = useState(false);
  const [refreshingStance, setRefreshingStance] = useState(false);

  const loadWhales = useCallback(async () => {
    try {
      setWhales(await api.onchainWhales());
      setWhaleError("");
    } catch (reason) {
      setWhaleError(reason instanceof Error ? reason.message : "고래 관측 데이터를 불러오지 못했습니다.");
    }
  }, []);

  const load = useCallback(async () => {
    setLoading(true);
    setError("");
    void loadWhales();
    try {
      const [crypto, stocks, stance] = await Promise.allSettled([api.paperDashboard(), api.stockPaperDashboard(), api.stanceBacktest()]);
      if (crypto.status === "rejected") throw crypto.reason;
      setData(crypto.value);
      if (stocks.status === "fulfilled") {
        setStockData(stocks.value);
        setStockError("");
      } else {
        setStockError(stocks.reason instanceof Error ? stocks.reason.message : "주식 페이퍼 트랙을 불러오지 못했습니다.");
      }
      if (stance.status === "fulfilled") {
        setStanceData(stance.value);
        setStanceError("");
      } else {
        setStanceError(stance.reason instanceof Error ? stance.reason.message : "실히스토리 검증 결과를 불러오지 못했습니다.");
      }
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "엔진 트레이딩 데이터를 불러오지 못했습니다.");
    } finally {
      setLoading(false);
    }
  }, [loadWhales]);

  useEffect(() => { void load(); }, [load]);
  useEffect(() => {
    if (active !== "onchain") return;
    const timer = window.setInterval(() => { void loadWhales(); }, 30_000);
    return () => window.clearInterval(timer);
  }, [active, loadWhales]);

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

  async function refreshStance() {
    setRefreshingStance(true);
    setStanceError("");
    try {
      setStanceData(await api.refreshStanceBacktest());
    } catch (reason) {
      setStanceError(reason instanceof Error ? reason.message : "실히스토리 검증을 갱신하지 못했습니다.");
    } finally {
      setRefreshingStance(false);
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
      {whaleError ? <TerminalWarning tone="warning">고래 관측 갱신 실패 · {whaleError} · 페이퍼 엔진 화면은 계속 사용할 수 있습니다.</TerminalWarning> : null}
      {stockError && active === "stocks" ? <TerminalWarning tone="warning">{stockError}</TerminalWarning> : null}
      {stanceError && active === "status" ? <TerminalWarning tone="warning">{stanceError}</TerminalWarning> : null}
      {!data ? <EngineLoading /> : active === "battle" ? <BattleView data={data} whales={whales} starting={starting} onStart={startBenchmark} /> : active === "stocks" ? <StockPaperView data={stockData} /> : active === "positions" ? <PositionsView trades={data.open_trades} funnel={data.gate_funnel} activation={data.activation} /> : active === "journal" ? <JournalView trades={data.closed_trades} /> : active === "onchain" ? <OnchainView data={whales} onReload={loadWhales} /> : <EngineStatusView data={data} stance={stanceData} refreshingStance={refreshingStance} onRefreshStance={refreshStance} />}
    </div>
  );
}

function StockPaperView({ data }: { data: StockPaperDashboard | null }) {
  if (!data) return <EngineLoading />;
  const rejectionLedger = data.entry_rejection_distribution ?? { period_days: 7, total: 0, gates: [] };
  return (
    <div className="engineView stockPaperView" data-testid="engine-stock-paper-tab">
      <section className="stockPaperGate">
        <div><ShieldCheck size={18} /><span>PaperBroker only</span><strong>실주문 영구 봉인</strong></div>
        <p>{data.performance_gate}</p>
        <small>{data.sample_note}</small>
      </section>
      {!data.ready_to_start ? <TerminalWarning tone="warning">{data.start_block_reason || "Toss 관측 준비 대기"}</TerminalWarning> : null}
      <header className="stockPaperHeader">
        <div><span className="engineSectionLabel">독립 검증 시계 · 4주</span><h2>나스닥100 · 코스피100</h2><p>같은 판단 게이트, 시장별 실제 체결 제약. 크립토 성적과 합산하지 않습니다.</p></div>
        <div><Building2 size={17} /><strong>{data.universe.total}종목</strong><span>{data.universe.version} · {data.parameter_version}</span></div>
      </header>
      <section className="stockTrackGrid">
        {data.tracks.map((track) => <StockTrackCard key={track.market} track={track} />)}
      </section>
      <StockPaperEntryChart fills={data.recent_fills} />
      <section className="stockExecutionAudit" data-testid="stock-entry-rejection-ledger">
        <header><div><span className="engineSectionLabel">판단 원장 · 최근 {rejectionLedger.period_days}일</span><h3>진입 거부 게이트 분포</h3></div><strong>{rejectionLedger.total}건</strong></header>
        <div className="stockRejectionGrid">
          {rejectionLedger.gates.map((item) => (
            <div key={`${item.market}-${item.gate}`}><span>{item.market}</span><strong>{rejectionLabel(item.gate)}</strong><b>{item.count.toLocaleString("ko-KR")}</b></div>
          ))}
          {!rejectionLedger.total ? <p>인증 후 정상 관측이 시작되면 필수 게이트별 측정값과 임계가 이 원장에 기록됩니다.</p> : null}
        </div>
      </section>
      <section className="stockExecutionAudit">
        <header><div><span className="engineSectionLabel">체결 모델 감사</span><h3>미체결 사유 분포</h3></div><strong>{data.fill_count} fills</strong></header>
        <div className="stockRejectionGrid">
          {data.tracks.flatMap((track) => Object.entries(track.rejection_reasons).map(([reason, count]) => (
            <div key={`${track.market}-${reason}`}><span>{track.market}</span><strong>{rejectionLabel(reason)}</strong><b>{count.toLocaleString("ko-KR")}</b></div>
          )))}
          {!data.tracks.some((track) => Object.keys(track.rejection_reasons).length) ? <p>아직 미체결 관측이 없습니다. 장외·VI·가격제한·유동성·데이터 누락은 발생 즉시 이곳에 누적됩니다.</p> : null}
        </div>
      </section>
      <section className="stockFillAudit">
        <header><span>최근 체결 원장</span><small>원통화 · 수수료/세금 · 환율 관측 시점 보존</small></header>
        {data.recent_fills.length ? data.recent_fills.slice(0, 8).map((fill) => (
          <div key={fill.id}><strong>{fill.symbol}</strong><span>{fill.market} · {fill.side === "buy" ? "매수" : "매도"} {fill.quantity}주</span><b>{stockMoney(fill.price, fill.currency)}</b><small>수수료 {stockMoney(fill.commission, fill.currency)}{fill.transaction_tax ? ` · 세금 ${stockMoney(fill.transaction_tax, fill.currency)}` : ""}</small></div>
        )) : <p>정직한 체결 조건을 모두 통과한 주문이 아직 없습니다.</p>}
      </section>
    </div>
  );
}

function StockTrackCard({ track }: { track: StockPaperTrack }) {
  const rejectionCount = Object.values(track.rejection_reasons).reduce((sum, value) => sum + value, 0);
  return (
    <article className={`stockTrackCard ${track.status}`}>
      <header><div><span>{track.market === "KR" ? "한국" : "미국"}</span><strong>{track.benchmark_index}</strong></div><b>{track.elapsed_days}/28일</b></header>
      <div className="stockTrackReturns">
        <p><span>엔진</span><strong className={track.engine_return_pct === null ? "" : track.engine_return_pct >= 0 ? "positive" : "negative"}>{track.engine_return_pct === null ? "시가 데이터 대기" : signedPct(track.engine_return_pct)}</strong></p>
        <p><span>{track.benchmark_index} · {track.benchmark_proxy_symbol} 프록시</span><strong>{track.benchmark_return_pct === null ? "데이터 대기" : signedPct(track.benchmark_return_pct)}</strong></p>
      </div>
      <div className="stockTrackProgress"><i style={{ width: `${Math.min(100, track.elapsed_days / 28 * 100)}%` }} /></div>
      {!track.clock_valid ? <em>검증 시계 대기 · {track.clock_invalidation_reason || "인증 후 첫 정상 관측 필요"}</em> : null}
      <footer><span>{shortDate(track.started_at)} → {shortDate(track.ends_at)}</span><b>{stockMoney(track.cash, track.currency)}</b><small>미체결 {rejectionCount}건</small></footer>
      {track.status === "stopped" ? <em>체결 invariant 정지 · {rejectionLabel(track.stop_reason || "unknown")}</em> : null}
    </article>
  );
}

function rejectionLabel(reason: string): string {
  const labels: Record<string, string> = {
    session_closed: "정규장 밖",
    price_limit_locked: "가격제한 잠김",
    vi: "VI",
    trading_halted: "거래정지",
    warning_hard_gate: "위험종목 경고",
    liquidity_partial: "유동성 부분체결",
    liquidity_zero: "1분 유동성 부족",
    market_data_missing: "관측 데이터 누락",
    long_only_sell_exceeds_position: "보유 수량 초과 매도 차단",
    fill_price_outside_observed_range: "체결가 범위 위반",
    risk_reward: "R/R 근거 누락",
    validated_signature: "검증 표본 미달",
    earnings_clear: "실적 일정 미확인",
    liquidation_safety: "무효화선 근거 누락",
    confirmed_flip: "안정 롱 스탠스 미충족",
    analysis_available: "공용 분석 캔들 부족",
    entry_score: "진입 점수 미달",
    checklist: "합류 체크 미달",
    data_fresh: "관측 신선도 초과",
    universe_entry_blocked: "거래 유니버스 밖"
  };
  return labels[reason] || reason.replaceAll("_", " ");
}

function stockMoney(value: number, currency: "KRW" | "USD"): string {
  return new Intl.NumberFormat("ko-KR", { style: "currency", currency, maximumFractionDigits: currency === "KRW" ? 0 : 2 }).format(value);
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
          {competition.engine.policy_invalid_count ? <small>정책 오류 표본 {competition.engine.policy_invalid_count}건 성과 제외</small> : null}
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
  const validated = (data?.wallets ?? []).filter((wallet) => wallet.review.trust_status === "trusted");
  return (
    <section className="whaleBattleReference" data-testid="whale-battle-reference">
      <div><Waves size={17} /><span>고래 참고군</span><strong>{validated.length ? `검증 ${validated.length}지갑` : "검증 표본 대기"}</strong></div>
      <p>{validated.length ? validated.slice(0, 3).map((wallet) => `${wallet.label} 1R ${wallet.review.win_1r_pct}% · ${signedR(wallet.review.cumulative_return_r)} (N=${wallet.review.sample_size})`).join(" · ") : "28일·N≥30·1R CI 하한 55%를 모두 통과한 고래만 3자 참고군으로 표시됩니다."}</p>
    </section>
  );
}

function OnchainView({ data, onReload }: { data: OnchainWhaleDashboard | null; onReload: () => Promise<void> }) {
  const [address, setAddress] = useState("");
  const [label, setLabel] = useState("");
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const [filter, setFilter] = useState<"all" | "validating" | "trusted" | "excluded">("all");
  const wallets = useMemo(() => data?.wallets ?? [], [data?.wallets]);
  const filteredWallets = useMemo(
    () => wallets.filter((wallet) => filter === "all" || wallet.review.trust_status === filter || (filter === "validating" && wallet.review.trust_status === "review_ready")),
    [filter, wallets]
  );

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

  async function discoverNow() {
    setBusy(true); setMessage("리더보드 전체를 스캔하고 추적군을 갱신하고 있습니다.");
    try { await api.discoverOnchainWhales(); await api.collectOnchainWhales(); setMessage(""); await onReload(); }
    catch (reason) { setMessage(reason instanceof Error ? reason.message : "리더보드 자동 스캔에 실패했습니다."); }
    finally { setBusy(false); }
  }

  if (!data) return <EngineLoading />;
  return (
    <div className="engineView onchainView" data-testid="engine-onchain-tab">
      <section className="onchainToolbar">
        <div><span className="engineSectionLabel">Hyperliquid leaderboard · 발굴부터 엄선까지</span><strong>고래 검증 센터</strong><small>{Number(data.discovery.rows_scanned ?? 0).toLocaleString("ko-KR")}계정 발굴 · 추적 {data.wallet_count}/{data.max_wallets} · 마지막 {data.discovery.as_of ? shortDateTime(data.discovery.as_of) : "대기"}</small></div>
        <div className="onchainToolbarActions"><button className="button secondary" disabled={busy} onClick={collectNow} type="button"><RefreshCw size={15} />포지션 갱신</button><button className="button" disabled={busy} onClick={discoverNow} type="button"><Radar size={15} />리더보드 재스캔</button></div>
      </section>
      {message ? <TerminalWarning tone={message.startsWith("리더보드") ? "warning" : "error"}>{message}</TerminalWarning> : null}
      <WhaleDiscoveryAudit data={data} />
      <WhaleFlowOverview data={data} />
      <p className="onchainPolicy">{data.policy}</p>
      <WhaleValidationBoard wallets={wallets} filter={filter} onFilter={setFilter} />
      <details className="onchainManualPanel">
        <summary>특정 공개 계정 추가</summary>
        <form className="onchainAddForm" onSubmit={addWallet}>
          <label><span>공개 계정 주소</span><input aria-label="Hyperliquid 지갑 주소" onChange={(event) => setAddress(event.target.value)} placeholder="0x…" required value={address} /></label>
          <label><span>추정 별칭</span><input aria-label="고래 별칭" onChange={(event) => setLabel(event.target.value)} placeholder="예: BTC 스윙 A" value={label} /></label>
          <button className="button" disabled={busy || !address.trim()} type="submit"><Plus size={15} />추가</button>
        </form>
      </details>
      {!data.wallets.length ? <EngineEmpty title="자동 추적군 준비 중" body="리더보드 스캔이 끝나면 활동 고래의 공개 포지션과 체결을 자동 관측합니다." /> : !filteredWallets.length ? <EngineEmpty title="해당 검증군 없음" body="현재 조건을 충족한 고래가 없습니다. 검증 표본은 백그라운드에서 계속 축적됩니다." /> : (
        <div className="onchainWalletList">
          {filteredWallets.map((wallet) => (
            <section className={`onchainWalletRow ${wallet.review.state} trust-${wallet.review.trust_status}`} key={wallet.address}>
              <header><div><strong>{wallet.label}</strong><code>{wallet.address_short}</code><small>{wallet.leaderboard ? `리더보드 #${wallet.leaderboard.leaderboard_rank} · 계정 ${compactMoney(wallet.leaderboard.account_value_usd)} · ${selectionReason(wallet.leaderboard.selection_reason)}` : wallet.alias_disclaimer}</small></div><div><span>{whaleTrustState(wallet.review.trust_status)}</span><b>{wallet.review.win_1r_pct === null ? `추종 승률 대기 · N=${wallet.review.sample_size}` : `추종 승률 ${wallet.review.win_1r_pct}% · N=${wallet.review.sample_size}`}</b>{wallet.source === "discovery" ? <i className="onchainAutoTag">AUTO</i> : <button aria-label={`${wallet.label} 삭제`} disabled={busy} onClick={() => void removeWallet(wallet.address)} title="워치리스트에서 삭제" type="button"><Trash2 size={15} /></button>}</div></header>
              {wallet.leaderboard ? <WhaleLeaderboardPerformance leaderboard={wallet.leaderboard} /> : null}
              <WhaleReviewMetrics review={wallet.review} />
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

function WhaleDiscoveryAudit({ data }: { data: OnchainWhaleDashboard }) {
  const discovery = data.discovery;
  const scan = discovery.position_scan;
  const policy = discovery.selection_policy;
  const coverage = discovery.selected_coverage ?? {};
  const stages = [
    { label: "리더보드 원본", value: Number(discovery.rows_scanned ?? 0), detail: "공개 계정" },
    { label: "성과 조건 통과", value: Number(discovery.eligible_count ?? 0), detail: "ROI·PnL·회전율" },
    { label: "포지션 실사", value: Number(scan?.scanned_count ?? 0), detail: `BTC·ETH 보유 ${scan?.active_focus_count ?? 0}` },
    { label: "검증 추적군", value: Number(discovery.selected_count ?? 0), detail: `방향 슬롯 ${policy?.directional_slots ?? 0}` }
  ];
  return (
    <section className="whaleDiscoveryAudit" data-testid="whale-discovery-audit">
      <header><div><span>자동 발굴 감사</span><strong>성과 상위 + 방향 균형 선발</strong></div><small>상위 수익자만 고르지 않고 현재 BTC·ETH 롱/숏 포지션을 실사해 검증군을 구성합니다.</small></header>
      <div className="whaleDiscoveryStages">{stages.map((stage, index) => <div key={stage.label}><i>{index + 1}</i><span>{stage.label}</span><strong>{stage.value.toLocaleString("ko-KR")}</strong><small>{stage.detail}</small></div>)}</div>
      <div className="whaleCoverageGrid">
        {(policy?.focus_symbols ?? ["BTC", "ETH"]).map((symbol) => {
          const item = coverage[symbol] ?? { long_wallets: 0, short_wallets: 0, long_usd: 0, short_usd: 0 };
          return <div key={symbol}><strong>{symbol} 방향 커버리지</strong><span className="long">롱 {item.long_wallets}지갑 · {compactMoney(item.long_usd)}</span><span className="short">숏 {item.short_wallets}지갑 · {compactMoney(item.short_usd)}</span></div>;
        })}
      </div>
      {scan?.errors ? <small className="negative">포지션 실사 실패 {scan.errors}건 · 다음 스캔에서 재시도</small> : null}
    </section>
  );
}

function WhaleLeaderboardPerformance({ leaderboard }: { leaderboard: NonNullable<OnchainWhaleDashboard["wallets"][number]["leaderboard"]> }) {
  const metrics = [
    { label: "7일", pnl: leaderboard.week_pnl_usd, roi: leaderboard.week_roi },
    { label: "30일", pnl: leaderboard.month_pnl_usd, roi: leaderboard.month_roi },
    { label: "전체", pnl: leaderboard.all_time_pnl_usd, roi: leaderboard.all_time_roi }
  ];
  return <div className="whaleLeaderboardPerformance" aria-label="리더보드 성과">{metrics.map((metric) => <div key={metric.label}><span>{metric.label} 수익률</span><strong className={metric.roi >= 0 ? "positive" : "negative"}>{signedPercent(metric.roi * 100)}</strong><small>{signedCompactMoney(metric.pnl)}</small></div>)}</div>;
}

function WhaleValidationBoard({
  wallets,
  filter,
  onFilter
}: {
  wallets: OnchainWhaleDashboard["wallets"];
  filter: "all" | "validating" | "trusted" | "excluded";
  onFilter: (value: "all" | "validating" | "trusted" | "excluded") => void;
}) {
  const trusted = wallets.filter((wallet) => wallet.review.trust_status === "trusted").length;
  const validating = wallets.filter((wallet) => ["validating", "review_ready"].includes(wallet.review.trust_status)).length;
  const excluded = wallets.filter((wallet) => wallet.review.trust_status === "excluded").length;
  const options = [
    { id: "all" as const, label: `전체 ${wallets.length}` },
    { id: "validating" as const, label: `검증중 ${validating}` },
    { id: "trusted" as const, label: `엄선 ${trusted}` },
    { id: "excluded" as const, label: `제외 ${excluded}` }
  ];
  return (
    <section className="whaleValidationBoard" data-testid="whale-validation-board">
      <div><span>4주 고래 검증</span><strong>28일 + N≥30 + CI 하한 55%</strong><small>리더보드 ROI는 선별 입력, 추종 승률과 R 성과는 검증 결과</small></div>
      <div className="whaleValidationFilters" role="group" aria-label="고래 검증 필터">
        {options.map((option) => <button aria-pressed={filter === option.id} key={option.id} onClick={() => onFilter(option.id)} type="button">{option.label}</button>)}
      </div>
    </section>
  );
}

function WhaleReviewMetrics({ review }: { review: OnchainWhaleDashboard["wallets"][number]["review"] }) {
  const ciLow = review.win_1r_ci?.[0] ?? null;
  const progress = Math.max(0, Math.min(100, review.validation_progress_pct));
  return (
    <div className="whaleReviewMetrics" data-testid="whale-review-metrics">
      <div><span>추종 승률</span><strong>{review.win_1r_pct === null ? "대기" : `${review.win_1r_pct}%`}</strong><small>{ciLow === null ? `채점 N=${review.sample_size}` : `CI 하한 ${ciLow}% · N=${review.sample_size}`}</small></div>
      <div><span>추종 수익</span><strong className={review.cumulative_return_r >= 0 ? "positive" : "negative"}>{signedR(review.cumulative_return_r)}</strong><small>평균 {review.average_return_r === null ? "-" : signedR(review.average_return_r)} · PF {review.profit_factor_r === null ? "-" : ratio(review.profit_factor_r)}</small></div>
      <div><span>관측 표본</span><strong>{review.observed_count}건</strong><small>결과 확정 {review.sample_size}건 · 잔여 {review.remaining_samples}</small></div>
      <div className="whaleValidationProgress"><span>검증 기간</span><strong>{review.validation_days}/28일</strong><small>{review.validation_remaining_days ? `${review.validation_remaining_days}일 남음` : review.validation_calendar_complete ? "기간 충족" : "체결 관측 대기"}</small><i><b style={{ width: `${progress}%` }} /></i></div>
    </div>
  );
}

function WhaleFlowOverview({ data }: { data: OnchainWhaleDashboard }) {
  const flow = data.flow;
  return (
    <div className="whaleFlowOverview">
      <section className="whaleFlowMetrics" aria-label="고래 현재 노출">
        <div><span>현재 롱 노출</span><strong className="positive">{compactMoney(flow.current_long_usd)}</strong></div>
        <div><span>현재 숏 노출</span><strong className="negative">{compactMoney(flow.current_short_usd)}</strong></div>
        <div><span>순포지션</span><strong className={flow.current_net_usd >= 0 ? "positive" : "negative"}>{signedCompactMoney(flow.current_net_usd)}</strong></div>
        <div><span>24시간 순체결</span><strong className={flow.flow_24h_usd >= 0 ? "positive" : "negative"}>{signedCompactMoney(flow.flow_24h_usd)}</strong><small>{flow.event_count_24h}건</small></div>
      </section>
      <section className="whaleFlowChartSection">
        <header><div><Activity size={16} /><strong>고래 순체결 흐름</strong><span>2시간 단위 · 최근 {flow.window_hours}시간</span></div><div className="whaleLegend"><span><i className="long" />롱 유입·숏 청산</span><span><i className="short" />숏 유입·롱 청산</span></div></header>
        <WhaleFlowChart points={flow.timeline} />
      </section>
      <div className="whaleFlowLower">
        <section className="whaleSymbolExposure">
          <header><strong>종목별 현재 쏠림</strong><span>공개 포지션 명목가</span></header>
          {flow.symbols.length ? flow.symbols.slice(0, 8).map((item) => {
            const total = Math.max(1, item.long_usd + item.short_usd);
            return <div className="whaleSymbolRow" key={item.symbol}><div><strong>{item.symbol.replace("USDT", "")}</strong><span>{item.wallet_count}지갑 · 24h {item.event_count_24h}건</span><b className={item.net_usd >= 0 ? "positive" : "negative"}>{signedCompactMoney(item.net_usd)}</b></div><div className="whaleExposureTrack"><i className="long" style={{ width: `${item.long_usd / total * 100}%` }} /><i className="short" style={{ width: `${item.short_usd / total * 100}%` }} /></div><small><span>롱 {compactMoney(item.long_usd)}</span><span>숏 {compactMoney(item.short_usd)}</span></small></div>;
          }) : <p className="onchainEmptyInline">자동 추적군의 공개 포지션을 수집 중입니다.</p>}
        </section>
        <section className="whaleEventTape">
          <header><strong>최근 체결 이벤트</strong><span>10만 USDT 이상</span></header>
          {data.recent_events.length ? data.recent_events.slice(0, 10).map((event) => <div key={event.id}><i className={event.side} /><strong>{event.coin}</strong><span>{whaleEventLabel(event.event)} · {event.side === "long" ? "롱" : "숏"}</span><b>{compactMoney(event.size_usd)}</b><time>{shortDateTime(event.event_at)}</time></div>) : <p className="onchainEmptyInline">새 추적군의 확정 체결을 기다리고 있습니다.</p>}
        </section>
      </div>
    </div>
  );
}

function WhaleFlowChart({ points }: { points: OnchainWhaleDashboard["flow"]["timeline"] }) {
  const width = 1000; const height = 230; const mid = 110; const plotHeight = 88;
  const max = Math.max(1, ...points.map((point) => Math.abs(point.net_usd)));
  const step = width / Math.max(1, points.length); const barWidth = Math.max(3, step * 0.64);
  const labels = points.length ? [points[0], points[Math.floor(points.length / 2)], points[points.length - 1]] : [];
  return <div className="whaleFlowChart"><svg aria-label="고래 순체결 흐름 차트" preserveAspectRatio="none" role="img" viewBox={`0 0 ${width} ${height}`}><line className="whaleChartGrid" x1="0" x2={width} y1={mid - 44} y2={mid - 44} /><line className="whaleChartZero" x1="0" x2={width} y1={mid} y2={mid} /><line className="whaleChartGrid" x1="0" x2={width} y1={mid + 44} y2={mid + 44} />{points.map((point, index) => { const value = point.net_usd; const h = Math.max(value === 0 ? 0 : 2, Math.abs(value) / max * plotHeight); return <rect className={value >= 0 ? "long" : "short"} height={h} key={point.time} rx="1" width={barWidth} x={index * step + (step - barWidth) / 2} y={value >= 0 ? mid - h : mid} />; })}{labels.map((point, index) => <text key={point.time} textAnchor={index === 0 ? "start" : index === 2 ? "end" : "middle"} x={index === 0 ? 2 : index === 2 ? width - 2 : width / 2} y="220">{new Date(point.time * 1000).toLocaleString("ko-KR", { month: "numeric", day: "numeric", hour: "2-digit" })}</text>)}</svg><div className="whaleChartScale"><span>+{compactMoney(max)}</span><span>0</span><span>-{compactMoney(max)}</span></div></div>;
}

function PositionsView({ trades, funnel, activation }: { trades: PaperTrade[]; funnel: PaperGateFunnel; activation: PaperDashboard["activation"] }) {
  const slots = activation.validation_slots ?? { active: trades.length, target: 2 };
  return <div className="engineView" data-testid="engine-positions-tab">
    <section className="engineValidationStrip" data-testid="paper-validation-slots">
      <div><span>4주 검증 슬롯</span><strong>{slots.active}/{slots.target} 가동</strong></div>
      <p>{slots.active < slots.target ? `빈 슬롯 ${slots.target - slots.active}개 · 안전 게이트 통과 후보가 나오면 다음 확정 캔들에서 즉시 보충` : "검증 슬롯 가동 중 · 종료되면 다음 적격 후보로 자동 보충"}</p>
      <small>각 포지션 100 USDT · 3x · 무효화 손절 · 실주문 없음</small>
    </section>
    {!trades.length ? <EngineEmpty title="현재 엔진 포지션 없음" body={funnelSummary(funnel)} actionHref="/engine?tab=status" actionLabel="게이트 퍼널 보기" /> : <section className="enginePositionGrid">{trades.map((trade) => <PaperPositionCard key={trade.id} trade={trade} />)}</section>}
  </div>;
}

function PaperPositionCard({ trade }: { trade: PaperTrade }) {
  const evidence = evidenceLines(trade).slice(0, 3);
  const entryMode = String(record(trade.entry_evidence).entry_mode ?? "");
  const validationLabel = entryMode === "validation_bootstrap" ? "검증 시작 진입" : entryMode === "validation_bootstrap_recovery" ? "검증 복구 진입" : entryMode === "validation_sampler" ? "검증 슬롯 진입" : "";
  return (
    <article className="enginePositionCard">
      <header><div><strong>{trade.symbol}</strong><span>{direction(trade.direction)} · {trade.leverage}x{validationLabel ? ` · ${validationLabel}` : ""}</span></div><b>{pct(trade.net_return_pct)}</b></header>
      <dl><div><dt>진입</dt><dd>{price(trade.entry_price)}</dd></div><div><dt>무효화</dt><dd>{price(trade.invalidation_price)}</dd></div><div><dt>익절1</dt><dd>{price(trade.take_profit_price)}</dd></div><div><dt>익절2</dt><dd>{price(trade.take_profit_2_price)}</dd></div></dl>
      {trade.exit_monitor ? <p className="engineExitMonitor">자동 청산 감시 · 무효화까지 {signedPct(trade.exit_monitor.invalidation_distance_pct)} · 익절1까지 {signedPct(trade.exit_monitor.take_profit_distance_pct)}</p> : null}
      <div className="engineEvidence"><span>진입 근거</span>{evidence.map((line, index) => <p key={`${line}-${index}`}>{line}</p>)}</div>
    </article>
  );
}

function JournalView({ trades }: { trades: PaperTrade[] }) {
  const search = useSearchParams();
  const filter = search.get("filter") ?? "all";
  const rows = trades.filter((trade) => filter === "win" ? trade.net_pnl_usdt > 0 && !isNeutralTrade(trade) : filter === "loss" ? trade.net_pnl_usdt <= 0 && !isNeutralTrade(trade) : filter === "neutral" ? isNeutralTrade(trade) : filter === "all" ? true : trade.exit_reason === filter);
  return (
    <div className="engineView" data-testid="engine-journal-tab">
      <div className="engineFilters">{[["all","전체"],["win","승"],["loss","패"],["neutral","중립"],["invalidation_breach","무효화"],["opposite_stance_flip","반대 전환"],["time_decay","시간 감쇠"]].map(([id,label]) => <Link className={filter === id ? "active" : ""} href={`/engine?tab=journal&filter=${id}`} key={id}>{label}</Link>)}</div>
      {!rows.length ? <EngineEmpty title="표시할 거래 없음" body="선택한 조건의 종료 거래가 없습니다." /> : <div className="engineJournalList">{rows.map((trade) => <PaperJournalRow key={trade.id} trade={trade} />)}</div>}
    </div>
  );
}

function PaperJournalRow({ trade }: { trade: PaperTrade }) {
  const policyInvalid = isPolicyInvalid(trade);
  const reason = policyInvalid ? "정책 오류 표본 · 성과 제외" : exitReason(trade.exit_reason);
  return (
    <details className="engineJournalRow">
      <summary><strong>{trade.symbol}</strong><span>{direction(trade.direction)}</span><b className={policyInvalid ? "neutral" : trade.net_pnl_usdt >= 0 ? "positive" : "negative"}>{pct(trade.net_return_pct)}</b><span>{trade.holding_bars}캔들</span><span>{reason}</span></summary>
      <div className="engineJournalDetail"><section><h3>진입 당시</h3><p>스탠스 {stanceLabel(trade.stance_snapshot)}</p>{evidenceLines(trade).slice(0, 4).map((line) => <p key={line}>{line}</p>)}</section><section><h3>청산</h3><p>{reason} · {price(trade.exit_price)}</p><p>비용 차감 net {money(trade.net_pnl_usdt)} USDT</p><p>{trade.loss_tags.length ? trade.loss_tags.map(lossTagLabel).join(" · ") : "채점 결과는 판단 원장에 기록됨"}</p></section></div>
    </details>
  );
}

function EngineStatusView({ data, stance, refreshingStance, onRefreshStance }: { data: PaperDashboard; stance: StanceBacktestDashboard | null; refreshingStance: boolean; onRefreshStance: () => void }) {
  const calibration = data.calibration;
  const report = calibration.weekly_report;
  const digest = record(report.improvement_digest);
  const suggestions = calibration.suggestions.slice(0, 8);
  const counts = calibration.signature_state_counts;
  return (
    <div className="engineView engineStatusGrid" data-testid="engine-status-tab">
      <StanceBacktestCard data={stance} refreshing={refreshingStance} onRefresh={onRefreshStance} />
      <GateFunnel funnel={data.gate_funnel} />
      <JudgmentCoverageCard coverage={data.judgment_coverage} />
      <section className="engineStatusCard engineDigest"><span className="engineSectionLabel">이번 주 개선</span><h2>{String(digest.headline ?? digest.summary ?? "이번 주 유의미한 개선 없음")}</h2><p>{String(digest.honesty_line ?? report.sample_warning ?? "표본과 조치 이력을 같은 주 단위로 비교합니다.")}</p></section>
      {data.performance_action.poor ? <section className="engineCausalRow"><div><span>페이퍼 부진</span><strong>{data.performance_action.summary}</strong></div><i>→</i><div><span>같은 기간 엔진 조치</span><strong>{actionSummary(data.performance_action.actions)}</strong></div></section> : null}
      <section className="engineStatusCard"><header><h2>파라미터 자율 피드</h2><span>예정 {calibration.suggestion_status_counts.scheduled ?? 0} · 실험 {calibration.suggestion_status_counts.experiment ?? 0}</span></header>{suggestions.length ? suggestions.map((item) => <div className="engineFeedRow" key={item.id}><span>{item.title}</span><b>{statusLabel(item.status)}</b></div>) : <p className="engineEmptyLine">진행 중인 변경이 없습니다.</p>}</section>
      <section className="engineStatusCard"><header><h2>시그니처 상태</h2><span>변동만 추적</span></header><div className="signatureCounts"><div><strong>{counts.validated ?? 0}</strong><span>검증됨</span></div><div><strong>{counts.degraded ?? 0}</strong><span>저하</span></div><div><strong>{counts.quarantined ?? 0}</strong><span>격리</span></div><div><strong>{counts.candidate ?? 0}</strong><span>표본 축적</span></div></div></section>
      <CandidateReviewCard review={calibration.candidate_review} />
    </div>
  );
}

function JudgmentCoverageCard({ coverage }: { coverage: PaperDashboard["judgment_coverage"] }) {
  const unscorable = Object.entries(coverage.unscorable_types).map(([type, count]) => `${type} ${count}`).join(" · ");
  return (
    <section className="engineStatusCard" data-testid="judgment-coverage-card">
      <header><div><span className="engineSectionLabel">최근 {coverage.period_days}일</span><h2>판단 원장 커버리지</h2></div><strong>{coverage.coverage_pct.toFixed(1)}%</strong></header>
      <div className="signatureCounts"><div><strong>{coverage.total}</strong><span>전체 판단</span></div><div><strong>{coverage.recorded}</strong><span>원장 기록</span></div><div><strong>{coverage.pending}</strong><span>채점 대기</span></div><div><strong>{coverage.unscorable}</strong><span>채점 불가</span></div></div>
      <p className="engineEmptyLine">{unscorable || "채점 불가 유형 없음"}{coverage.unclassified_types.length ? ` · 미분류 ${coverage.unclassified_types.join(", ")}` : " · 기록 누락 유형 0"}</p>
    </section>
  );
}

function StanceBacktestCard({ data, refreshing, onRefresh }: { data: StanceBacktestDashboard | null; refreshing: boolean; onRefresh: () => void }) {
  return (
    <section className="engineStatusCard engineStanceBacktest" data-testid="real-history-stance-backtest">
      <header>
        <div><span className="engineSectionLabel">실데이터 검증 · 합성 성적과 분리</span><h2>방향 엔진 v1 ↔ v2 · 동일 표본 T+24h</h2></div>
        <button className="button secondary" type="button" onClick={onRefresh} disabled={refreshing}><RefreshCw size={14} />{refreshing ? "재판정 중" : "실데이터 갱신"}</button>
      </header>
      <div className="stanceBacktestRows">
        {(data?.items ?? []).map((item) => (
          <article className={item.publishable ? "published" : "withheld"} key={item.symbol}>
            <div><strong>{item.symbol}</strong><span>Bitget 4h · {item.generated_at ? shortDateTime(item.generated_at) : "수집 대기"}</span></div>
            <p><b>{formatVariantRate(item.v1?.directional_hit_pct)} → {formatVariantRate(item.v2?.directional_hit_pct)}</b><span>v1 → v2 · {item.directional_hit_ci ? `v2 CI ${item.directional_hit_ci[0]}~${item.directional_hit_ci[1]}%` : "CI 대기"}</span></p>
            <div><strong>N={item.sample_size}</strong><span>{item.comparison?.claim ?? (item.publishable ? "발행" : item.decision === "pending" ? "수집 대기" : "결론 유보")}</span></div>
          </article>
        ))}
        {!data?.items.length ? <p className="engineEmptyLine">실제 히스토리 수집을 기다리는 중입니다.</p> : null}
      </div>
      <footer>
        <span>확정 4h봉만 · 6봉 간격 비중첩 · 수수료/슬리피지 차감 net</span>
        <span>과거 펀딩·OI·청산 미포함 · 합성 80.8%와 합산 안 함</span>
      </footer>
    </section>
  );
}

function formatVariantRate(value: number | null | undefined) {
  return value === null || value === undefined ? "—" : `${value.toFixed(1)}%`;
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
function isPolicyInvalid(trade: PaperTrade): boolean { return trade.loss_tags.includes("policy_invalid:pre_tp_pressure_exit"); }
function isNeutralTrade(trade: PaperTrade): boolean { return isNeutralExit(trade.exit_reason) || isPolicyInvalid(trade); }
function lossTagLabel(value: string): string { if (value === "policy_invalid:pre_tp_pressure_exit") return "TP1 전 익절압력 오발동"; if (value === "exit:take_profit_pressure") return "기존 청산 기록 보존"; return value; }
function price(value: number | null): string { if (value === null || !Number.isFinite(value)) return "-"; return value >= 100 ? value.toLocaleString(undefined, { maximumFractionDigits: 2 }) : value.toFixed(value >= 1 ? 4 : 6); }
function money(value: number): string { return `${value > 0 ? "+" : ""}${Number(value).toFixed(2)}`; }
function compactMoney(value: number): string { return value >= 1_000_000 ? `${(value / 1_000_000).toFixed(1)}M` : value >= 1_000 ? `${Math.round(value / 1_000)}K` : value.toFixed(0); }
function signedCompactMoney(value: number): string { return `${value >= 0 ? "+" : "-"}${compactMoney(Math.abs(value))}`; }
function whaleEventLabel(value: string): string { return ({ open: "신규 진입", increase: "증액", reduce: "감액", close: "청산", flip: "방향 전환" } as Record<string,string>)[value] ?? value; }
function whaleTrustState(value: string): string { return ({ trusted: "엄선 고래", review_ready: "승격 심사", validating: "4주 검증 중", excluded: "검증 제외" } as Record<string,string>)[value] ?? "4주 검증 중"; }
function signedR(value: number): string { return `${value >= 0 ? "+" : ""}${value.toFixed(2)}R`; }
function signedPercent(value: number): string { return `${value >= 0 ? "+" : ""}${value.toFixed(1)}%`; }
function selectionReason(value: string): string { const [kind, coin, side] = value.split(":"); return kind === "coverage" ? `${coin} ${side === "short" ? "숏" : "롱"} 커버리지 선발` : "성과 품질 선발"; }
function metricTone(value: string, inverse: boolean): string { const number = Number(value.replace(/[+%,]/g, "")); if (!Number.isFinite(number) || number === 0) return "neutral"; const positive = inverse ? number < 0 : number > 0; return positive ? "positive" : "negative"; }
function shortDate(value: string | null): string { return value ? new Date(value).toLocaleDateString("ko-KR", { month: "numeric", day: "numeric" }) : "-"; }
function shortDateTime(value: string): string { return new Date(value).toLocaleString("ko-KR", { month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit" }); }
