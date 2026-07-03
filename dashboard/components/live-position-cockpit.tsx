"use client";

import Link from "next/link";
import {
  Activity,
  AlertTriangle,
  BrainCircuit,
  FileClock,
  NotebookPen,
  RefreshCw,
  ShieldCheck,
  Target,
  TestTube2,
  UploadCloud
} from "lucide-react";
import { FormEvent, useEffect, useMemo, useState } from "react";
import { TerminalMetric, TerminalPanel, TerminalTable, TerminalWarning } from "@/components/terminal";
import {
  api,
  type BitgetConnectionTest,
  type LivePositionDetail,
  type LivePositionPayload,
  type LivePositionsResponse,
  type Position,
  type PositionEvent,
  type PositionState
} from "@/lib/api";
import { formatPrice, signedPercent } from "@/lib/format";

type PanelStatus = "ok" | "warning" | "error" | "neutral" | "accent";
type MetricTone = "positive" | "negative" | "warning" | "neutral" | "info" | "agent";

export function LivePositionCockpit() {
  const [data, setData] = useState<LivePositionsResponse | null>(null);
  const [selectedId, setSelectedId] = useState("");
  const [connectionTest, setConnectionTest] = useState<BitgetConnectionTest | null>(null);
  const [loading, setLoading] = useState(true);
  const [actionLoading, setActionLoading] = useState("");
  const [notice, setNotice] = useState("");
  const [error, setError] = useState("");

  async function load(sync = false) {
    setError("");
    try {
      const next = sync ? await api.syncLivePositions() : await api.livePositions();
      const normalized = "positions" in next && "open_count" in next
        ? next
        : {
            provider: next.provider,
            positions: next.positions ?? [],
            open_count: next.positions?.filter((item) => item.position.status === "open").length ?? 0,
            needs_exit_record_count: next.positions?.filter((item) => item.position.status !== "open").length ?? 0,
            timestamp: next.timestamp ?? new Date().toISOString()
          };
      setData(normalized);
      setSelectedId((current) => current || normalized.positions[0]?.position.id || "");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Live position data load failed");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    void load(true);
    const interval = window.setInterval(() => {
      void load(true);
    }, 30000);
    return () => window.clearInterval(interval);
  }, []);

  async function syncPositions() {
    setActionLoading("sync");
    setNotice("");
    await load(true);
    setNotice("Bitget read-only 포지션 동기화와 상태 분석을 갱신했습니다.");
    setActionLoading("");
  }

  async function testConnection() {
    setActionLoading("test");
    setError("");
    setNotice("");
    try {
      const result = await api.testBitgetConnection();
      setConnectionTest(result);
      setNotice(`Bitget public ${result.public_market_data.ok ? "OK" : "ERROR"} · private ${result.private_positions.status}`);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Bitget connection test failed");
    } finally {
      setActionLoading("");
    }
  }

  async function createInsight(positionId: string) {
    setActionLoading(`insight:${positionId}`);
    setError("");
    setNotice("");
    try {
      const result = await api.createPositionInsight(positionId);
      setData((current) => {
        if (!current) return current;
        return {
          ...current,
          positions: current.positions.map((item) => (item.position.id === positionId ? result : item))
        };
      });
      setNotice("AI Position Insight를 생성했습니다. 점수 계산은 deterministic JSON을 그대로 사용합니다.");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Insight creation failed");
    } finally {
      setActionLoading("");
    }
  }

  const positions = data?.positions ?? [];
  const selected = positions.find((item) => item.position.id === selectedId) ?? positions[0];
  const metrics = useMemo(() => summarizePositions(positions, data?.needs_exit_record_count ?? 0), [positions, data?.needs_exit_record_count]);

  return (
    <div className="page">
      <header className="pageHeader cockpitHeader">
        <div>
          <p className="eyebrow">Live Position Intelligence Cockpit</p>
          <h1>
            <span>지금 들고 있는 포지션,</span>
            <span>계속 들고 있어도 되는 상태인가?</span>
          </h1>
          <p className="subtle">Bitget read-only 포지션을 추적하고, 리스크/차트/와이코프/진입 논리 유지 여부를 한 화면에서 점검합니다.</p>
        </div>
        <div className="actionGroup">
          <button className="button secondary" onClick={testConnection} disabled={actionLoading === "test"}>
            <TestTube2 size={16} />
            Test Bitget
          </button>
          <button className="button" onClick={syncPositions} disabled={actionLoading === "sync"}>
            <UploadCloud size={16} />
            {actionLoading === "sync" ? "Syncing" : "Sync Live"}
          </button>
          <button className="button secondary" onClick={() => void load(false)} disabled={loading}>
            <RefreshCw size={16} />
            Refresh
          </button>
        </div>
      </header>

      {error ? <TerminalWarning tone="error">{error}</TerminalWarning> : null}
      {notice ? <TerminalWarning tone="info">{notice}</TerminalWarning> : null}

      <section className="grid four">
        <TerminalMetric label="Open Positions" value={data?.open_count ?? 0} delta={data?.provider ?? "provider"} tone={(data?.open_count ?? 0) ? "warning" : "neutral"} />
        <TerminalMetric label="Unrealized PnL" value={`${metrics.totalPnl.toFixed(2)} USDT`} delta={signedPercent(metrics.avgPnlPercent)} tone={metrics.totalPnl >= 0 ? "positive" : "negative"} />
        <TerminalMetric label="Highest Risk" value={metrics.highestRisk ? `${metrics.highestRisk}/100` : "-"} delta={metrics.highestRiskSymbol || "no active risk"} tone={metrics.highestRisk >= 70 ? "negative" : metrics.highestRisk >= 55 ? "warning" : "neutral"} />
        <TerminalMetric label="Exit Record Needed" value={metrics.needsExitRecord} delta={data?.timestamp ? `updated ${new Date(data.timestamp).toLocaleTimeString()}` : "not synced"} tone={metrics.needsExitRecord ? "warning" : "neutral"} />
      </section>

      {connectionTest ? (
        <TerminalPanel title="Bitget Connection" subtitle="Read-only public market data and private position boundary" status={connectionTest.private_positions.ok ? "ok" : "warning"}>
          <div className="statusGrid">
            <StatusItem label="Provider" value={connectionTest.provider} tone={connectionTest.provider === "bitget" ? "ok" : "warn"} />
            <StatusItem label="Public Data" value={connectionTest.public_market_data.ok ? "ok" : "error"} tone={connectionTest.public_market_data.ok ? "ok" : "error"} />
            <StatusItem label="Private Positions" value={connectionTest.private_positions.status} tone={connectionTest.private_positions.ok ? "ok" : "warn"} />
            <StatusItem label="Private Count" value={String(connectionTest.private_positions.count)} tone="muted" />
            <StatusItem label="Candles" value={String(connectionTest.public_market_data.candles)} tone="muted" />
            <StatusItem label="Funding" value={connectionTest.funding_rate.ok ? String(connectionTest.funding_rate.value) : "n/a"} tone="muted" />
          </div>
        </TerminalPanel>
      ) : null}

      {loading && !data ? (
        <TerminalPanel title="Loading Live Positions" subtitle="Bitget sync and deterministic analysis are starting" status="neutral">
          <div className="terminalEmpty">Loading live position cockpit...</div>
        </TerminalPanel>
      ) : positions.length ? (
        <section className="cockpitLayout">
          <TerminalPanel title="Live Position Tape" subtitle="실제 보유/추적 포지션만 표시합니다" status={metrics.highestRisk >= 70 ? "warning" : "ok"}>
            <div className="positionTape">
              {positions.map((item) => (
                <button
                  className={`positionCard ${selected?.position.id === item.position.id ? "selected" : ""}`}
                  key={item.position.id}
                  onClick={() => setSelectedId(item.position.id)}
                  type="button"
                >
                  <div className="positionCardTop">
                    <div>
                      <strong>{item.position.symbol}</strong>
                      <span>{item.position.direction.toUpperCase()} · {item.position.leverage}x · {item.position.source}</span>
                    </div>
                    <StatusPill status={item.state.status} label={item.state.status_label} />
                  </div>
                  <div className="positionCardMetrics">
                    <MiniMetric label="PnL" value={signedPercent(item.state.pnl_percent)} tone={item.state.pnl_percent >= 0 ? "positive" : "negative"} />
                    <MiniMetric label="Health" value={`${item.state.health_score}/100`} tone={healthTone(item.state.health_score)} />
                    <MiniMetric label="Liq Dist" value={formatDistance(item.state.liquidation_distance_pct)} tone={liquidationTone(item.state.liquidation_distance_pct)} />
                  </div>
                  <div className="positionCardFooter">
                    <span>Entry {formatPrice(item.position.entry_price)}</span>
                    <span>Mark {formatNullablePrice(item.state.mark_price)}</span>
                  </div>
                </button>
              ))}
            </div>
          </TerminalPanel>

          {selected ? (
            <div className="cockpitDetail">
              <PositionDecisionPanel payload={selected} onCreateInsight={createInsight} busy={actionLoading === `insight:${selected.position.id}`} />
              <section className="grid two">
                <PositionRiskPanel payload={selected} />
                <PositionTechnicalPanel state={selected.state} />
              </section>
              <section className="grid two">
                <PositionLevelsPanel payload={selected} />
                <PositionInsightPanel payload={selected} onCreateInsight={createInsight} busy={actionLoading === `insight:${selected.position.id}`} />
              </section>
              <TerminalPanel title="Recent Position Events" subtitle="점수/리스크/인사이트 변경 기록" status={selected.recent_events.length ? "warning" : "neutral"}>
                <EventList events={selected.recent_events} />
              </TerminalPanel>
            </div>
          ) : null}
        </section>
      ) : (
        <NoPositionsState onSync={syncPositions} syncing={actionLoading === "sync"} />
      )}
    </div>
  );
}

export function PositionDetailShell({ positionId }: { positionId: string }) {
  const [detail, setDetail] = useState<LivePositionDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState("");
  const [notice, setNotice] = useState("");
  const [error, setError] = useState("");

  async function load() {
    setError("");
    try {
      setDetail(await api.livePosition(positionId));
    } catch (err) {
      setError(err instanceof Error ? err.message : "Position detail load failed");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    void load();
  }, [positionId]);

  async function analyze() {
    setBusy("analyze");
    setNotice("");
    setError("");
    try {
      await api.analyzeLivePosition(positionId);
      await load();
      setNotice("새 포지션 스냅샷을 저장했습니다.");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Analyze failed");
    } finally {
      setBusy("");
    }
  }

  async function createInsight() {
    setBusy("insight");
    setNotice("");
    setError("");
    try {
      await api.createPositionInsight(positionId);
      await load();
      setNotice("AI Position Insight를 생성했습니다.");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Insight failed");
    } finally {
      setBusy("");
    }
  }

  async function saveMemo(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!detail) return;
    const form = new FormData(event.currentTarget);
    setBusy("memo");
    setError("");
    setNotice("");
    try {
      await api.updatePositionMemo(detail.position.id, {
        memo: String(form.get("memo") ?? ""),
        entry_memo: String(form.get("entry_memo") ?? ""),
        thesis_text: String(form.get("thesis_text") ?? ""),
        planned_stop_price: numberOrNull(form.get("planned_stop_price")),
        planned_take_profit_price: numberOrNull(form.get("planned_take_profit_price"))
      });
      await load();
      setNotice("포지션 메모와 계획 가격을 저장했습니다.");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Memo save failed");
    } finally {
      setBusy("");
    }
  }

  async function recordExit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!detail) return;
    const form = new FormData(event.currentTarget);
    const exitPrice = Number(form.get("exit_price"));
    if (!Number.isFinite(exitPrice) || exitPrice <= 0) {
      setError("청산 기록 가격을 입력해야 합니다.");
      return;
    }
    setBusy("exit");
    setError("");
    setNotice("");
    try {
      const trade = await api.recordLiveExit(detail.position.id, {
        exit_price: exitPrice,
        exit_reason: String(form.get("exit_reason") || "사용자 내부 청산 기록"),
        memo: String(form.get("exit_memo") || "")
      });
      setNotice(`청산 기록을 저장했습니다. Trade ${trade.symbol} ${signedPercent(trade.pnl_percent)}`);
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Exit record failed");
    } finally {
      setBusy("");
    }
  }

  if (loading && !detail) {
    return (
      <div className="page">
        <TerminalPanel title="Loading Position" subtitle={positionId} status="neutral">
          <div className="terminalEmpty">Loading position detail...</div>
        </TerminalPanel>
      </div>
    );
  }

  if (!detail) {
    return (
      <div className="page">
        {error ? <TerminalWarning tone="error">{error}</TerminalWarning> : <TerminalWarning tone="error">Position not found</TerminalWarning>}
      </div>
    );
  }

  const exitDefault = detail.state.mark_price ?? detail.position.current_price ?? detail.position.entry_price;

  return (
    <div className="page">
      <header className="pageHeader cockpitHeader">
        <div>
          <p className="eyebrow">Position Detail</p>
          <h1>{detail.position.symbol} {detail.position.direction.toUpperCase()} 관제 기록</h1>
          <p className="subtle">스냅샷, 이벤트, 메모, 내부 청산 기록을 한 포지션 단위로 관리합니다.</p>
        </div>
        <div className="actionGroup">
          <Link className="button secondary" href="/">
            <Activity size={16} />
            Cockpit
          </Link>
          <button className="button secondary" onClick={analyze} disabled={busy === "analyze"}>
            <RefreshCw size={16} />
            Analyze
          </button>
          <button className="button" onClick={createInsight} disabled={busy === "insight"}>
            <BrainCircuit size={16} />
            Insight
          </button>
        </div>
      </header>

      {error ? <TerminalWarning tone="error">{error}</TerminalWarning> : null}
      {notice ? <TerminalWarning tone="info">{notice}</TerminalWarning> : null}

      <PositionDecisionPanel payload={detail} onCreateInsight={() => createInsight()} busy={busy === "insight"} />

      <section className="grid two">
        <PositionRiskPanel payload={detail} />
        <PositionTechnicalPanel state={detail.state} />
      </section>

      <section className="grid two">
        <PositionLevelsPanel payload={detail} />
        <PositionInsightPanel payload={detail} onCreateInsight={() => createInsight()} busy={busy === "insight"} />
      </section>

      <section className="grid two">
        <TerminalPanel title="Entry Thesis Memo" subtitle="진입 논리와 무효화/익절 기준은 AI가 점수를 계산하지 않고 비교 설명에만 사용합니다" status="accent">
          <form className="positionMemoForm" onSubmit={saveMemo}>
            <label>
              <span>진입 당시 논리</span>
              <textarea name="entry_memo" defaultValue={detail.position.entry_memo || detail.position.memo} rows={4} />
            </label>
            <label>
              <span>현재 유지해야 하는 핵심 가설</span>
              <textarea name="thesis_text" defaultValue={detail.position.thesis_text} rows={4} />
            </label>
            <div className="memoPriceGrid">
              <label>
                <span>손절/무효화 기준</span>
                <input name="planned_stop_price" type="number" step="0.0001" defaultValue={detail.position.planned_stop_price ?? ""} />
              </label>
              <label>
                <span>익절/분할 기준</span>
                <input name="planned_take_profit_price" type="number" step="0.0001" defaultValue={detail.position.planned_take_profit_price ?? ""} />
              </label>
            </div>
            <label>
              <span>메모</span>
              <textarea name="memo" defaultValue={detail.position.memo} rows={3} />
            </label>
            <button className="button" type="submit" disabled={busy === "memo"}>
              <NotebookPen size={16} />
              Save Memo
            </button>
          </form>
        </TerminalPanel>

        <TerminalPanel title="Record Exit" subtitle="거래소 주문이 아니라 내부 복기용 청산 기록만 생성합니다" status={detail.position.status === "closed" ? "neutral" : "warning"}>
          <form className="positionMemoForm" onSubmit={recordExit}>
            <label>
              <span>청산 기록 가격</span>
              <input name="exit_price" type="number" step="0.0001" defaultValue={exitDefault} disabled={detail.position.status === "closed"} />
            </label>
            <label>
              <span>이탈 이유</span>
              <textarea name="exit_reason" rows={4} defaultValue="포지션 관제 후 사용자가 수동으로 이탈 기록" disabled={detail.position.status === "closed"} />
            </label>
            <label>
              <span>복기 메모</span>
              <textarea name="exit_memo" rows={4} placeholder="왜 나갔는지, 진입 논리가 언제 약해졌는지 기록" disabled={detail.position.status === "closed"} />
            </label>
            <button className="button" type="submit" disabled={detail.position.status === "closed" || busy === "exit"}>
              <FileClock size={16} />
              Record Internal Exit
            </button>
          </form>
        </TerminalPanel>
      </section>

      <section className="grid two">
        <TerminalPanel title="Event Timeline" subtitle="포지션 상태 변화 로그" status={detail.events.length ? "warning" : "neutral"}>
          <EventList events={detail.events} />
        </TerminalPanel>
        <TerminalPanel title="Snapshot History" subtitle="Health/Risk/PnL 재현 가능한 상태 기록" status={detail.snapshots.length ? "ok" : "neutral"}>
          <TerminalTable
            data={detail.snapshots}
            idKey="id"
            emptyLabel="No snapshots yet"
            columns={[
              { key: "created_at", header: "Time", render: (snapshot) => new Date(snapshot.created_at).toLocaleString() },
              { key: "health_score", header: "Health", align: "end", render: (snapshot) => `${snapshot.health_score}/100` },
              { key: "risk_score", header: "Risk", align: "end", render: (snapshot) => `${snapshot.risk_score}/100` },
              { key: "pnl_percent", header: "PnL", align: "end", render: (snapshot) => signedPercent(snapshot.pnl_percent) },
              { key: "status_label", header: "Status", render: (snapshot) => snapshot.status_label }
            ]}
          />
        </TerminalPanel>
      </section>
    </div>
  );
}

function PositionDecisionPanel({
  payload,
  onCreateInsight,
  busy
}: {
  payload: LivePositionPayload;
  onCreateInsight: (positionId: string) => Promise<void> | void;
  busy: boolean;
}) {
  const { position, state } = payload;
  const verdict = verdictForState(state);
  return (
    <TerminalPanel
      title="Position State"
      subtitle="자동 주문 없이, 현재 포지션 유지 논리가 살아있는지만 점검합니다"
      status={statusPanelTone(state.status)}
      actions={
        <>
          <Link className="button secondary" href={`/positions/${position.id}`}>
            <Target size={16} />
            Detail
          </Link>
          <button className="button" onClick={() => onCreateInsight(position.id)} disabled={busy}>
            <BrainCircuit size={16} />
            {busy ? "Generating" : "AI Insight"}
          </button>
        </>
      }
    >
      <div className="positionDecisionGrid">
        <div className={`verdictBox status-${state.status}`}>
          <span>현재 판단</span>
          <strong>{state.status_label}</strong>
          <p>{verdict}</p>
        </div>
        <div className="terminalMetricGrid">
          <TerminalMetric label="Symbol" value={position.symbol} delta={`${position.direction.toUpperCase()} · ${position.leverage}x`} tone="info" />
          <TerminalMetric label="Health" value={`${state.health_score}/100`} delta={state.status_label} tone={healthTone(state.health_score)} />
          <TerminalMetric label="Risk" value={`${state.risk_score}/100`} delta="position risk" tone={state.risk_score >= 70 ? "negative" : state.risk_score >= 55 ? "warning" : "neutral"} />
          <TerminalMetric label="PnL" value={signedPercent(state.pnl_percent)} delta={state.pnl_amount === null ? "amount n/a" : `${state.pnl_amount.toFixed(2)} USDT`} tone={state.pnl_percent >= 0 ? "positive" : "negative"} />
          <TerminalMetric label="Score Δ" value={`${state.score_change >= 0 ? "+" : ""}${state.score_change}`} delta={`${state.entry_score} -> ${state.current_score}`} tone={state.score_change >= 0 ? "positive" : state.score_change <= -15 ? "negative" : "warning"} />
        </div>
      </div>
    </TerminalPanel>
  );
}

function PositionRiskPanel({ payload }: { payload: LivePositionPayload }) {
  const { position, state } = payload;
  return (
    <TerminalPanel title="Risk Console" subtitle="청산가, 손익, 스코어 하락, 수익 반납을 같이 봅니다" status={state.risk_score >= 70 ? "warning" : "ok"}>
      <div className="terminalMetricGrid riskMetricGrid">
        <TerminalMetric label="Entry" value={formatPrice(position.entry_price)} tone="neutral" />
        <TerminalMetric label="Mark" value={formatNullablePrice(state.mark_price)} tone="info" />
        <TerminalMetric label="Liq" value={formatNullablePrice(position.liquidation_price)} tone="warning" />
        <TerminalMetric label="Liq Dist" value={formatDistance(state.liquidation_distance_pct)} tone={liquidationTone(state.liquidation_distance_pct)} />
        <TerminalMetric label="Entry Delta" value={formatDistance(state.analysis.risk.price_distance_from_entry_pct)} tone={state.analysis.risk.price_distance_from_entry_pct === null || state.analysis.risk.price_distance_from_entry_pct >= 0 ? "positive" : "negative"} />
      </div>
      <HealthBars components={state.score_json.health_components} />
      <div className="reasonCodeGrid">
        {state.analysis.reason_codes.map((code) => (
          <span key={code}>{code}</span>
        ))}
      </div>
    </TerminalPanel>
  );
}

function PositionTechnicalPanel({ state }: { state: PositionState }) {
  const technical = state.analysis.technical;
  const wyckoff = state.analysis.wyckoff;
  return (
    <TerminalPanel title="Chart Structure" subtitle="와이코프/기술적 상태는 포지션 논리 유지 여부를 설명합니다" status={technical.break_of_structure ? "warning" : "ok"}>
      <div className="technicalGrid">
        <TechnicalItem label="Trend" value={technical.trend} tone={technical.trend_alignment.includes("against") ? "danger" : "ok"} />
        <TechnicalItem label="RSI" value={technical.rsi_state} />
        <TechnicalItem label="MACD" value={technical.macd_state} tone={technical.macd_state.includes("bearish") ? "danger" : "ok"} />
        <TechnicalItem label="Volume" value={technical.volume_state} tone={technical.volume_state.includes("declining") ? "warn" : "ok"} />
        <TechnicalItem label="Support" value={technical.support_status} tone={technical.support_status === "at_risk" ? "danger" : "ok"} />
        <TechnicalItem label="Resistance" value={technical.resistance_status} />
      </div>
      <div className="wyckoffBox">
        <div>
          <span>Wyckoff Phase</span>
          <strong>{humanizeToken(wyckoff.phase_hint)}</strong>
        </div>
        <p>{wyckoff.structure_comment}</p>
        <div className="wyckoffTags">
          <span>ACC {wyckoff.accumulation_score}</span>
          <span>DIST {wyckoff.distribution_score}</span>
          {wyckoff.spring_candidate ? <span>SPRING</span> : null}
          {wyckoff.sos_candidate ? <span>SOS</span> : null}
          {wyckoff.lps_candidate ? <span>LPS</span> : null}
        </div>
      </div>
    </TerminalPanel>
  );
}

function PositionLevelsPanel({ payload }: { payload: LivePositionPayload }) {
  const { position, state } = payload;
  const levels = [
    { label: "Entry", price: position.entry_price, type: "entry" },
    { label: "Mark", price: state.mark_price, type: "mark" },
    { label: "Liquidation", price: position.liquidation_price, type: "liquidation" },
    { label: "Stop", price: position.planned_stop_price, type: "stop" },
    { label: "Take Profit", price: position.planned_take_profit_price, type: "take_profit" },
    ...state.analysis.risk.critical_levels.map((level) => ({ label: level.type, price: level.price, type: level.type }))
  ].filter((level): level is { label: string; price: number; type: string } => Number.isFinite(level.price));
  const prices = levels.map((level) => level.price);
  const min = Math.min(...prices);
  const max = Math.max(...prices);

  return (
    <TerminalPanel title="Critical Price Map" subtitle="손절/익절/지지/저항/청산가를 한 축에서 확인합니다" status={state.analysis.risk.critical_levels.length ? "accent" : "neutral"}>
      {levels.length ? (
        <div className="levelChart">
          {levels
            .sort((a, b) => b.price - a.price)
            .map((level) => (
              <div className={`levelRow level-${level.type}`} key={`${level.type}-${level.price}`}>
                <span>{level.label}</span>
                <div className="levelTrack">
                  <i style={{ left: `${levelPosition(level.price, min, max)}%` }} />
                </div>
                <strong>{formatPrice(level.price)}</strong>
              </div>
            ))}
        </div>
      ) : (
        <div className="terminalEmpty">중요 가격대 데이터가 아직 충분하지 않습니다.</div>
      )}
    </TerminalPanel>
  );
}

function PositionInsightPanel({
  payload,
  onCreateInsight,
  busy
}: {
  payload: LivePositionPayload;
  onCreateInsight: (positionId: string) => Promise<void> | void;
  busy: boolean;
}) {
  const insight = payload.latest_insight;
  return (
    <TerminalPanel
      title="AI Position Insight"
      subtitle="LLM이 점수를 계산하지 않고 deterministic JSON을 자연어로 설명합니다"
      status={insight ? "ok" : "neutral"}
      actions={
        <button className="button secondary" onClick={() => onCreateInsight(payload.position.id)} disabled={busy}>
          <BrainCircuit size={16} />
          {busy ? "Generating" : "Generate"}
        </button>
      }
    >
      {insight ? (
        <div className="insightText">
          <p>{insight.insight_text}</p>
          <small>{new Date(insight.created_at).toLocaleString()} · {insight.status_label} · Health {insight.health_score}/100</small>
        </div>
      ) : (
        <div className="terminalEmpty">아직 생성된 포지션 인사이트가 없습니다.</div>
      )}
    </TerminalPanel>
  );
}

function EventList({ events }: { events: PositionEvent[] }) {
  if (!events.length) {
    return <div className="terminalEmpty">No position events yet</div>;
  }
  return (
    <div className="eventTimeline">
      {events.map((event) => (
        <div className={`eventItem severity-${event.severity}`} key={event.id}>
          <div>
            <strong>{event.title}</strong>
            <span>{new Date(event.created_at).toLocaleString()} · {event.event_type}</span>
          </div>
          <p>{event.description}</p>
        </div>
      ))}
    </div>
  );
}

function NoPositionsState({ onSync, syncing }: { onSync: () => void; syncing: boolean }) {
  return (
    <TerminalPanel title="No Live Positions" subtitle="현재 열린 포지션이 없거나 Bitget private read-only sync가 아직 연결되지 않았습니다" status="neutral">
      <div className="emptyStateAction">
        <ShieldCheck size={28} />
        <div>
          <strong>실제 보유 포지션이 감지되면 이 화면이 관제석으로 전환됩니다.</strong>
          <p>API 키는 read-only 권한만 사용하며, 이 제품에는 주문 실행 기능이 없습니다.</p>
        </div>
        <button className="button" onClick={onSync} disabled={syncing}>
          <UploadCloud size={16} />
          {syncing ? "Syncing" : "Sync Bitget Positions"}
        </button>
      </div>
    </TerminalPanel>
  );
}

function HealthBars({ components }: { components: PositionState["score_json"]["health_components"] }) {
  const rows = [
    ["Thesis", components.thesis_integrity],
    ["Chart", components.chart_structure],
    ["Risk Safety", components.risk_safety],
    ["Momentum/Volume", components.momentum_volume],
    ["Liquidity/Funding", components.liquidity_funding]
  ] as const;
  return (
    <div className="healthBars">
      {rows.map(([label, value]) => (
        <div className="healthBarRow" key={label}>
          <span>{label}</span>
          <div><i style={{ width: `${value}%` }} /></div>
          <strong>{value}</strong>
        </div>
      ))}
    </div>
  );
}

function StatusPill({ status, label }: { status: string; label: string }) {
  return <span className={`statusPill status-${status}`}>{label}</span>;
}

function MiniMetric({ label, value, tone }: { label: string; value: string; tone: MetricTone }) {
  return (
    <div className={`miniMetric tone-${tone}`}>
      <span>{label}</span>
      <strong>{value}</strong>
    </div>
  );
}

function TechnicalItem({ label, value, tone = "neutral" }: { label: string; value: string; tone?: "ok" | "warn" | "danger" | "neutral" }) {
  return (
    <div className={`technicalItem ${tone}`}>
      <span>{label}</span>
      <strong>{humanizeToken(value)}</strong>
    </div>
  );
}

function StatusItem({ label, value, tone }: { label: string; value: string; tone: string }) {
  return (
    <div className={`statusItem ${tone}`}>
      <span>{label}</span>
      <strong>{value}</strong>
    </div>
  );
}

function summarizePositions(positions: LivePositionPayload[], needsExitRecord: number) {
  const totalPnl = positions.reduce((sum, item) => sum + (item.state.pnl_amount ?? 0), 0);
  const avgPnlPercent = positions.length ? positions.reduce((sum, item) => sum + item.state.pnl_percent, 0) / positions.length : 0;
  const riskiest = [...positions].sort((a, b) => b.state.risk_score - a.state.risk_score)[0];
  return {
    totalPnl,
    avgPnlPercent,
    highestRisk: riskiest?.state.risk_score ?? 0,
    highestRiskSymbol: riskiest?.position.symbol ?? "",
    needsExitRecord
  };
}

function verdictForState(state: PositionState): string {
  if (state.status === "healthy") return "데이터상 진입 논리는 유지 중입니다. 다만 계획한 손절/익절 기준과 수익 반납 기준을 계속 확인해야 합니다.";
  if (state.status === "watch") return "유지 근거가 완전히 깨진 상태는 아니지만, 다음 지지/저항 반응과 점수 변화를 확인해야 합니다.";
  if (state.status === "risk_rising") return "리스크가 상승했습니다. 청산가 거리, 변동성, 수익 반납폭을 우선 점검해야 합니다.";
  if (state.status === "thesis_weakening") return "진입 논리가 약해지고 있습니다. 처음 들어간 이유가 아직 유효한지 메모와 차트 구조를 비교해야 합니다.";
  if (state.status === "critical") return "긴급 점검 구간입니다. 청산가 거리와 손실 제한 기준을 즉시 확인해야 합니다.";
  return "데이터가 충분하지 않아 판단을 보류해야 합니다. 포지션/시세 동기화 상태를 먼저 확인하세요.";
}

function statusPanelTone(status: PositionState["status"]): PanelStatus {
  if (status === "healthy") return "ok";
  if (status === "critical") return "error";
  if (status === "risk_rising" || status === "thesis_weakening" || status === "watch") return "warning";
  return "neutral";
}

function healthTone(score: number): MetricTone {
  if (score >= 80) return "positive";
  if (score >= 65) return "warning";
  if (score >= 50) return "agent";
  return "negative";
}

function liquidationTone(value: number | null): MetricTone {
  if (value === null) return "neutral";
  if (value < 5) return "negative";
  if (value < 10) return "warning";
  return "positive";
}

function formatNullablePrice(value: number | null): string {
  return value === null ? "-" : formatPrice(value);
}

function formatDistance(value: number | null): string {
  return value === null ? "-" : signedPercent(value);
}

function levelPosition(price: number, min: number, max: number): number {
  if (max <= min) return 50;
  return Math.max(2, Math.min(98, ((price - min) / (max - min)) * 100));
}

function numberOrNull(value: FormDataEntryValue | null): number | null {
  const parsed = Number(value);
  return Number.isFinite(parsed) && parsed > 0 ? parsed : null;
}

function humanizeToken(value: string): string {
  return value.replaceAll("_", " ");
}
