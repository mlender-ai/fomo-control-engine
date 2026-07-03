"use client";

import Link from "next/link";
import {
  Activity,
  BrainCircuit,
  FileClock,
  NotebookPen,
  RefreshCw,
  ShieldCheck,
  Target,
  TestTube2,
  UploadCloud
} from "lucide-react";
import { FormEvent, useEffect, useState } from "react";
import { TerminalMetric, TerminalPanel, TerminalTable, TerminalWarning } from "@/components/terminal";
import { PositionChart } from "@/components/position/PositionChart";
import { VolumeProfilePanel } from "@/components/position/VolumeProfilePanel";
import { VolumeXrayPanel } from "@/components/position/VolumeXrayPanel";
import {
  api,
  type BitgetConnectionTest,
  type LivePositionDetail,
  type LivePositionPayload,
  type LivePositionsResponse,
  type PositionChartAnalysis,
  type PositionEvent,
  type PositionState
} from "@/lib/api";
import { formatPrice, signedPercent } from "@/lib/format";

type PanelStatus = "ok" | "warning" | "error" | "neutral" | "accent";
type MetricTone = "positive" | "negative" | "warning" | "neutral" | "info" | "agent";
type DetailTab = "insight" | "wyckoff" | "technical" | "risk" | "timeline";

const detailTabs: Array<{ id: DetailTab; label: string }> = [
  { id: "insight", label: "Insight" },
  { id: "wyckoff", label: "Wyckoff" },
  { id: "technical", label: "Technical" },
  { id: "risk", label: "Risk" },
  { id: "timeline", label: "Timeline" }
];

export function LivePositionCockpit() {
  const [data, setData] = useState<LivePositionsResponse | null>(null);
  const [selectedId, setSelectedId] = useState("");
  const [connectionTest, setConnectionTest] = useState<BitgetConnectionTest | null>(null);
  const [loading, setLoading] = useState(true);
  const [actionLoading, setActionLoading] = useState("");
  const [notice, setNotice] = useState("");
  const [error, setError] = useState("");
  const [activeTab, setActiveTab] = useState<DetailTab>("insight");

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
      setNotice("포지션 인사이트가 생성되었습니다.");
    } catch (err) {
      setError(err instanceof Error ? err.message : "AI 인사이트 생성에 실패했습니다. 데이터 상태 또는 OpenAI API 설정을 확인해주세요.");
    } finally {
      setActionLoading("");
    }
  }

  const positions = data?.positions ?? [];
  const selected = positions.find((item) => item.position.id === selectedId) ?? positions[0];

  return (
    <div className="page cockpitPage">
      <header className="cockpitToolbar">
        <div>
          <p className="eyebrow">Live Position Cockpit</p>
          <h1>내 포지션 관제</h1>
        </div>
        <div className="cockpitToolbarActions">
          <span className="lastSyncText">{data?.timestamp ? `Last Sync ${new Date(data.timestamp).toLocaleTimeString()}` : "Last Sync -"}</span>
          <button className="button" onClick={syncPositions} disabled={actionLoading === "sync"}>
            <UploadCloud size={16} />
            {actionLoading === "sync" ? "Syncing" : "Sync Live"}
          </button>
          <button className="iconButton secondary" onClick={() => void load(false)} disabled={loading} title="Refresh local view">
            <RefreshCw size={16} />
          </button>
          <button className="iconButton secondary" onClick={testConnection} disabled={actionLoading === "test"} title="Test Bitget connection">
            <TestTube2 size={16} />
          </button>
        </div>
      </header>

      {error ? <TerminalWarning tone="error">{error}</TerminalWarning> : null}
      {notice ? <TerminalWarning tone="info">{notice}</TerminalWarning> : null}

      {connectionTest ? (
        <div className={`connectionNotice ${connectionTest.private_positions.ok ? "ok" : "warn"}`}>
          Bitget public {connectionTest.public_market_data.ok ? "OK" : "ERROR"} · private {connectionTest.private_positions.status} · positions {connectionTest.private_positions.count}
        </div>
      ) : null}

      {loading && !data ? (
        <TerminalPanel title="Loading Live Positions" subtitle="Bitget sync and deterministic analysis are starting" status="neutral">
          <div className="terminalEmpty">Loading live position cockpit...</div>
        </TerminalPanel>
      ) : positions.length ? (
        <>
          <PositionStrip positions={positions} selectedId={selected?.position.id ?? ""} onSelect={setSelectedId} />
          {selected ? (
            <>
              <SelectedPositionHeader payload={selected} />
              <section className="cockpitMainGrid">
                <ChartPanel payload={selected} />
                <InsightSummaryPanel payload={selected} onCreateInsight={createInsight} busy={actionLoading === `insight:${selected.position.id}`} />
              </section>
              <PositionDetailTabs payload={selected} activeTab={activeTab} onTabChange={setActiveTab} onCreateInsight={createInsight} busy={actionLoading === `insight:${selected.position.id}`} />
            </>
          ) : null}
        </>
      ) : (
        <NoPositionsState onSync={syncPositions} syncing={actionLoading === "sync"} />
      )}
    </div>
  );
}

function PositionStrip({
  positions,
  selectedId,
  onSelect
}: {
  positions: LivePositionPayload[];
  selectedId: string;
  onSelect: (positionId: string) => void;
}) {
  return (
    <section className="positionStrip" aria-label="Open positions">
      {positions.map((item) => (
        <button
          className={`positionStripCard ${item.position.id === selectedId ? "selected" : ""}`}
          key={item.position.id}
          onClick={() => onSelect(item.position.id)}
          type="button"
        >
          <strong>{item.position.symbol}</strong>
          <span>{item.position.direction.toUpperCase()} · {item.position.leverage}x</span>
          <em className={item.state.pnl_percent >= 0 ? "successText" : "dangerText"}>{signedPercent(item.state.pnl_percent)}</em>
          <small>Health {item.state.health_score}</small>
          <StatusPill status={item.state.status} label={item.state.status_label} />
        </button>
      ))}
    </section>
  );
}

function SelectedPositionHeader({ payload }: { payload: LivePositionPayload }) {
  const { position, state } = payload;
  return (
    <section className={`selectedPositionHeader status-${state.status}`}>
      <div className="selectedPositionTitle">
        <span>Selected Position</span>
        <strong>{position.symbol} · {position.direction.toUpperCase()} {position.leverage}x</strong>
      </div>
      <div className="selectedPositionMetrics">
        <PositionHeaderMetric label="PnL" value={signedPercent(state.pnl_percent)} tone={state.pnl_percent >= 0 ? "positive" : "negative"} />
        <PositionHeaderMetric label="Entry" value={formatPrice(position.entry_price)} />
        <PositionHeaderMetric label="Mark" value={formatNullablePrice(state.mark_price)} tone="info" />
        <PositionHeaderMetric label="Liq" value={formatNullablePrice(position.liquidation_price)} tone="warning" />
        <PositionHeaderMetric label="Liq Dist" value={formatDistance(state.liquidation_distance_pct)} tone={liquidationTone(state.liquidation_distance_pct)} />
        <PositionHeaderMetric label="Health" value={`${state.health_score}/100`} tone={healthTone(state.health_score)} />
      </div>
      <div className="selectedPositionState">
        <span>상태</span>
        <strong>{state.status_label}</strong>
      </div>
    </section>
  );
}

function ChartPanel({ payload }: { payload: LivePositionPayload }) {
  const { position, state } = payload;
  const technical = state.analysis.technical;
  const levels = chartLevels(payload);
  const prices = levels.map((level) => level.price).filter((price) => Number.isFinite(price));
  const min = Math.min(...prices);
  const max = Math.max(...prices);
  const candles = [44, 58, 46, 64, 52, 72, 68, 61, 49, 56, 42, 48, 54, 51];
  return (
    <section className="focusPanel chartFocusPanel">
      <div className="focusPanelHeader">
        <div>
          <h2>{position.symbol} Chart</h2>
          <p>Entry / Mark / Liquidation / Support / Resistance</p>
        </div>
        <span>{technical.trend.replaceAll("_", " ")}</span>
      </div>
      <div className="chartMock" aria-label="Position chart placeholder">
        <div className="chartGridLayer" />
        <div className="mockCandleLayer">
          {candles.map((height, index) => (
            <i
              className={index % 3 === 0 ? "down" : "up"}
              key={index}
              style={{ height: `${height}%`, left: `${7 + index * 6.5}%` }}
            />
          ))}
        </div>
        {levels.map((level) => (
          <div className={`chartLevel chartLevel-${level.className}`} key={`${level.type}-${level.price}`} style={{ top: `${priceToTop(level.price, min, max)}%` }}>
            <span>{level.label}</span>
            <strong>{formatPrice(level.price)}</strong>
          </div>
        ))}
        <div className="volumePlaceholder">
          <span>Volume</span>
          {candles.slice(0, 12).map((height, index) => (
            <i key={index} style={{ height: `${Math.max(18, height / 1.8)}%` }} />
          ))}
        </div>
      </div>
    </section>
  );
}

function InsightSummaryPanel({
  payload,
  onCreateInsight,
  busy
}: {
  payload: LivePositionPayload;
  onCreateInsight: (positionId: string) => Promise<void> | void;
  busy: boolean;
}) {
  const { position, state, latest_insight: insight } = payload;
  return (
    <section className="focusPanel insightFocusPanel">
      <div className="focusPanelHeader">
        <div>
          <h2>AI Position Insight</h2>
          <p>현재 판단과 다음 확인 지점</p>
        </div>
        <button className="button secondary" onClick={() => onCreateInsight(position.id)} disabled={busy}>
          <BrainCircuit size={16} />
          {busy ? "Generating" : "Generate"}
        </button>
      </div>
      <div className={`insightJudgement status-${state.status}`}>
        <span>현재 판단</span>
        <strong>{state.status_label}</strong>
        <p>{verdictForState(state)}</p>
      </div>
      {insight ? (
        <div className="insightPreview">
          <p>{firstInsightParagraph(insight.insight_text)}</p>
          <small>{new Date(insight.created_at).toLocaleString()} · Health {insight.health_score}/100</small>
        </div>
      ) : (
        <div className="insightEmpty">
          <strong>아직 인사이트가 없습니다.</strong>
          <span>Generate Insight 버튼을 눌러 현재 포지션 상태를 분석하세요.</span>
        </div>
      )}
    </section>
  );
}

function PositionDetailTabs({
  payload,
  activeTab,
  onTabChange,
  onCreateInsight,
  busy
}: {
  payload: LivePositionPayload;
  activeTab: DetailTab;
  onTabChange: (tab: DetailTab) => void;
  onCreateInsight: (positionId: string) => Promise<void> | void;
  busy: boolean;
}) {
  return (
    <section className="detailTabsPanel">
      <div className="detailTabList" role="tablist" aria-label="Position detail tabs">
        {detailTabs.map((tab) => (
          <button
            aria-selected={activeTab === tab.id}
            className={activeTab === tab.id ? "active" : ""}
            key={tab.id}
            onClick={() => onTabChange(tab.id)}
            role="tab"
            type="button"
          >
            {tab.label}
          </button>
        ))}
      </div>
      <div className="detailTabBody">
        {activeTab === "insight" ? <InsightTab payload={payload} onCreateInsight={onCreateInsight} busy={busy} /> : null}
        {activeTab === "wyckoff" ? <WyckoffTab state={payload.state} /> : null}
        {activeTab === "technical" ? <TechnicalTab state={payload.state} /> : null}
        {activeTab === "risk" ? <RiskTab payload={payload} /> : null}
        {activeTab === "timeline" ? <TimelineTab payload={payload} /> : null}
      </div>
    </section>
  );
}

function InsightTab({
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
    <div className="tabContentGrid">
      <div className={`tabJudgement status-${payload.state.status}`}>
        <span>현재 판단</span>
        <strong>{payload.state.status_label}</strong>
        <p>{verdictForState(payload.state)}</p>
      </div>
      <div className="tabTextBlock">
        {insight ? (
          <>
            <p>{insight.insight_text}</p>
            <small>{new Date(insight.created_at).toLocaleString()} · Health {insight.health_score}/100</small>
          </>
        ) : (
          <div className="insightEmpty">
            <strong>아직 인사이트가 없습니다.</strong>
            <span>Generate Insight 버튼을 눌러 현재 포지션 상태를 분석하세요.</span>
            <button className="button" onClick={() => onCreateInsight(payload.position.id)} disabled={busy}>
              <BrainCircuit size={16} />
              {busy ? "Generating" : "Generate Insight"}
            </button>
          </div>
        )}
      </div>
    </div>
  );
}

function WyckoffTab({ state }: { state: PositionState }) {
  const wyckoff = state.analysis.wyckoff;
  return (
    <div className="tabMetricLayout">
      <PositionHeaderMetric label="Accumulation" value={wyckoff.accumulation_score} tone="info" />
      <PositionHeaderMetric label="Distribution" value={wyckoff.distribution_score} tone="warning" />
      <PositionHeaderMetric label="Phase" value={humanizeToken(wyckoff.phase_hint)} />
      <PositionHeaderMetric label="Spring" value={wyckoff.spring_candidate ? "Yes" : "No"} />
      <PositionHeaderMetric label="SOS" value={wyckoff.sos_candidate ? "Yes" : "No"} />
      <PositionHeaderMetric label="LPS" value={wyckoff.lps_candidate ? "Yes" : "No"} />
      <p className="tabExplanation">{wyckoff.structure_comment}</p>
    </div>
  );
}

function TechnicalTab({ state }: { state: PositionState }) {
  const technical = state.analysis.technical;
  return (
    <div className="tabMetricLayout">
      <PositionHeaderMetric label="Trend" value={humanizeToken(technical.trend)} tone={technical.trend_alignment.includes("against") ? "negative" : "positive"} />
      <PositionHeaderMetric label="RSI" value={humanizeToken(technical.rsi_state)} />
      <PositionHeaderMetric label="MACD" value={humanizeToken(technical.macd_state)} tone={technical.macd_state.includes("bearish") ? "negative" : "positive"} />
      <PositionHeaderMetric label="Bollinger" value={humanizeToken(technical.bollinger_state)} />
      <PositionHeaderMetric label="Volume" value={humanizeToken(technical.volume_state)} tone={technical.volume_state.includes("declining") ? "warning" : "positive"} />
      <PositionHeaderMetric label="Support" value={humanizeToken(technical.support_status)} tone={technical.support_status === "at_risk" ? "negative" : "positive"} />
      <PositionHeaderMetric label="Resistance" value={humanizeToken(technical.resistance_status)} />
    </div>
  );
}

function RiskTab({ payload }: { payload: LivePositionPayload }) {
  const { position, state } = payload;
  return (
    <div className="tabRiskGrid">
      <div className="tabMetricLayout">
        <PositionHeaderMetric label="Liq Distance" value={formatDistance(state.liquidation_distance_pct)} tone={liquidationTone(state.liquidation_distance_pct)} />
        <PositionHeaderMetric label="Risk Score" value={`${state.risk_score}/100`} tone={state.risk_score >= 70 ? "negative" : state.risk_score >= 55 ? "warning" : "neutral"} />
        <PositionHeaderMetric label="PnL" value={signedPercent(state.pnl_percent)} tone={state.pnl_percent >= 0 ? "positive" : "negative"} />
        <PositionHeaderMetric label="Giveback" value={formatDistance(state.analysis.risk.profit_giveback_pct)} />
        <PositionHeaderMetric label="Stop" value={formatNullablePrice(position.planned_stop_price)} tone="warning" />
        <PositionHeaderMetric label="ATR Risk" value={state.analysis.risk.atr_risk} />
      </div>
      <div className="tabLevelsList">
        <strong>주의할 가격</strong>
        {state.analysis.risk.critical_levels.length ? (
          state.analysis.risk.critical_levels.map((level) => (
            <div key={`${level.type}-${level.price}`}>
              <span>{level.type}</span>
              <em>{formatPrice(level.price)}</em>
              <p>{level.meaning}</p>
            </div>
          ))
        ) : (
          <p>중요 가격대 데이터가 아직 충분하지 않습니다.</p>
        )}
      </div>
    </div>
  );
}

function TimelineTab({ payload }: { payload: LivePositionPayload }) {
  return (
    <div className="timelineTab">
      <div className="snapshotSummary">
        <PositionHeaderMetric label="Latest Health" value={`${payload.latest_snapshot.health_score}/100`} tone={healthTone(payload.latest_snapshot.health_score)} />
        <PositionHeaderMetric label="Risk" value={`${payload.latest_snapshot.risk_score}/100`} />
        <PositionHeaderMetric label="Snapshot" value={new Date(payload.latest_snapshot.created_at).toLocaleTimeString()} />
      </div>
      <EventList events={payload.recent_events} />
    </div>
  );
}

function PositionHeaderMetric({
  label,
  value,
  tone = "neutral"
}: {
  label: string;
  value: string | number;
  tone?: MetricTone;
}) {
  return (
    <div className={`positionHeaderMetric tone-${tone}`}>
      <span>{label}</span>
      <strong>{value}</strong>
    </div>
  );
}

export function PositionDetailShell({ positionId }: { positionId: string }) {
  const [detail, setDetail] = useState<LivePositionDetail | null>(null);
  const [chartAnalysis, setChartAnalysis] = useState<PositionChartAnalysis | null>(null);
  const [loading, setLoading] = useState(true);
  const [chartLoading, setChartLoading] = useState(true);
  const [chartError, setChartError] = useState("");
  const [timeframe, setTimeframe] = useState("4h");
  const [detailTab, setDetailTab] = useState<DetailTab>("risk");
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

  async function loadChart(nextTimeframe = timeframe) {
    setChartError("");
    setChartLoading(true);
    try {
      setChartAnalysis(await api.positionChartAnalysis(positionId, nextTimeframe));
    } catch (err) {
      setChartAnalysis(null);
      setChartError(err instanceof Error ? err.message : "Chart analysis load failed");
    } finally {
      setChartLoading(false);
    }
  }

  useEffect(() => {
    void load();
  }, [positionId]);

  useEffect(() => {
    void loadChart(timeframe);
  }, [positionId, timeframe]);

  async function analyze() {
    setBusy("analyze");
    setNotice("");
    setError("");
    try {
      await api.analyzeLivePosition(positionId);
      await load();
      await loadChart(timeframe);
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
      setNotice("포지션 인사이트가 생성되었습니다.");
    } catch (err) {
      setError(err instanceof Error ? err.message : "AI 인사이트 생성에 실패했습니다. 데이터 상태 또는 OpenAI API 설정을 확인해주세요.");
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
      await loadChart(timeframe);
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
      await loadChart(timeframe);
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
    <div className="page positionDetailPage">
      <header className="cockpitToolbar positionDetailToolbar">
        <div>
          <p className="eyebrow">Position Detail Chart Analysis</p>
          <h1>{detail.position.symbol} {detail.position.direction.toUpperCase()} 차트 관제</h1>
        </div>
        <div className="cockpitToolbarActions">
          <label className="timeframeSelect">
            <span>Timeframe</span>
            <select value={timeframe} onChange={(event) => setTimeframe(event.target.value)}>
              <option value="15m">15m</option>
              <option value="1h">1h</option>
              <option value="4h">4h</option>
              <option value="1d">1d</option>
            </select>
          </label>
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

      <SelectedPositionHeader payload={detail} />

      <section className="positionDetailMain">
        <PositionChart analysis={chartAnalysis} loading={chartLoading} error={chartError} onRetry={() => void loadChart(timeframe)} />
        <PositionInsightRail payload={detail} chartAnalysis={chartAnalysis} onCreateInsight={() => createInsight()} busy={busy === "insight"} />
      </section>

      <section className="positionBottomAnalysis">
        {chartAnalysis ? <VolumeProfilePanel analysis={chartAnalysis} /> : <AnalysisUnavailable title="Estimated Volume Profile" />}
        {chartAnalysis ? <VolumeXrayPanel analysis={chartAnalysis} /> : <AnalysisUnavailable title="Volume X-Ray" />}
        <TechnicalSummaryCard payload={detail} chartAnalysis={chartAnalysis} />
      </section>

      <PositionDetailTabs payload={detail} activeTab={detailTab} onTabChange={setDetailTab} onCreateInsight={() => createInsight()} busy={busy === "insight"} />

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
    </div>
  );
}

function PositionInsightRail({
  payload,
  chartAnalysis,
  onCreateInsight,
  busy
}: {
  payload: LivePositionPayload;
  chartAnalysis: PositionChartAnalysis | null;
  onCreateInsight: () => Promise<void> | void;
  busy: boolean;
}) {
  const { position, state, latest_insight: insight } = payload;
  const [showInputJson, setShowInputJson] = useState(false);
  const [copied, setCopied] = useState(false);
  const support = chartAnalysis?.price_levels.support[0];
  const resistance = chartAnalysis?.price_levels.resistance[0];
  const invalidation = chartAnalysis?.price_levels.invalidation[0];
  async function copyInsight() {
    if (!insight) return;
    try {
      await navigator.clipboard.writeText(insight.insight_text);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1800);
    } catch {
      setCopied(false);
    }
  }
  return (
    <aside className="positionInsightRail">
      <div className={`railJudgement status-${state.status}`}>
        <span>현재 판단</span>
        <strong>{state.status_label}</strong>
        <p>{directionalChartVerdict(payload, chartAnalysis)}</p>
      </div>
      <div className="railSection">
        <div className="railSectionHeader">
          <strong>주요 가격대</strong>
          <small>{chartAnalysis?.timeframe ?? "4h"}</small>
        </div>
        <RailPrice label="Entry" value={formatPrice(position.entry_price)} />
        <RailPrice label="Mark" value={formatNullablePrice(state.mark_price)} tone="info" />
        <RailPrice label="Liq" value={formatNullablePrice(position.liquidation_price)} tone="danger" />
        <RailPrice label="Support" value={support ? formatPrice(support.price) : "-"} />
        <RailPrice label="Resistance" value={resistance ? formatPrice(resistance.price) : "-"} tone="warning" />
        <RailPrice label="Invalidation" value={invalidation ? formatPrice(invalidation.price) : formatNullablePrice(position.planned_stop_price)} tone="danger" />
      </div>
      <div className="railSection">
        <div className="railSectionHeader">
          <strong>Risk Summary</strong>
          <small>no order execution</small>
        </div>
        <RailPrice label="Health" value={`${state.health_score}/100`} tone={healthTone(state.health_score)} />
        <RailPrice label="Risk" value={`${state.risk_score}/100`} tone={state.risk_score >= 70 ? "negative" : state.risk_score >= 55 ? "warning" : "neutral"} />
        <RailPrice label="Liq Dist" value={formatDistance(state.liquidation_distance_pct)} tone={liquidationTone(state.liquidation_distance_pct)} />
        <RailPrice label="Giveback" value={formatDistance(state.analysis.risk.profit_giveback_pct)} />
      </div>
      <div className="railSection aiInsightRailSection">
        <div className="railSectionHeader aiInsightHeader">
          <div>
            <strong>AI Position Insight</strong>
            {insight ? <small>Updated {new Date(insight.created_at).toLocaleTimeString()}</small> : null}
          </div>
          <button className="button secondary" onClick={onCreateInsight} disabled={busy}>
            <BrainCircuit size={16} />
            {busy ? "Generating insight..." : insight ? "Regenerate" : "Generate Insight"}
          </button>
        </div>
        {insight ? (
          <>
            <div className="railInsightText full">{insight.insight_text}</div>
            <div className="insightActionRow">
              <button className="button secondary" onClick={copyInsight} type="button">{copied ? "Copied" : "Copy"}</button>
              <button className="button secondary" onClick={() => setShowInputJson((value) => !value)} type="button">
                {showInputJson ? "Hide Input JSON" : "View Input JSON"}
              </button>
            </div>
            {showInputJson ? (
              <pre className="insightInputJson">{JSON.stringify(insight.input_json, null, 2)}</pre>
            ) : null}
          </>
        ) : (
          <div className="railInsightEmpty">
            <strong>아직 생성된 포지션 인사이트가 없습니다.</strong>
            <p>현재 포지션 상태를 분석하려면 Generate Insight를 눌러주세요.</p>
          </div>
        )}
      </div>
    </aside>
  );
}

function RailPrice({
  label,
  value,
  tone = "neutral"
}: {
  label: string;
  value: string;
  tone?: MetricTone | "danger";
}) {
  return (
    <div className={`railPrice tone-${tone}`}>
      <span>{label}</span>
      <strong>{value}</strong>
    </div>
  );
}

function TechnicalSummaryCard({
  payload,
  chartAnalysis
}: {
  payload: LivePositionPayload;
  chartAnalysis: PositionChartAnalysis | null;
}) {
  const technical = payload.state.analysis.technical;
  const wyckoff = payload.state.analysis.wyckoff;
  return (
    <section className="analysisPanel technicalSummaryPanel">
      <div className="analysisPanelHeader">
        <div>
          <h2>Technical Summary</h2>
          <p>차트 아래 보조 요약</p>
        </div>
        <span>{payload.state.status_label}</span>
      </div>
      <div className="technicalSummaryGrid">
        <RailPrice label="Trend" value={humanizeToken(technical.trend)} tone={technical.trend_alignment.includes("against") ? "negative" : "positive"} />
        <RailPrice label="RSI" value={humanizeToken(technical.rsi_state)} />
        <RailPrice label="MACD" value={humanizeToken(technical.macd_state)} tone={technical.macd_state.includes("bearish") ? "negative" : "positive"} />
        <RailPrice label="Volume" value={humanizeToken(chartAnalysis?.volume_xray.volume_state ?? technical.volume_state)} tone={chartAnalysis?.volume_xray.spike_detected ? "warning" : "neutral"} />
        <RailPrice label="Wyckoff" value={humanizeToken(wyckoff.phase_hint)} />
        <RailPrice label="Markers" value={String(chartAnalysis?.wyckoff_markers.length ?? 0)} />
      </div>
      <p className="technicalSummaryText">{wyckoff.structure_comment}</p>
    </section>
  );
}

function AnalysisUnavailable({ title }: { title: string }) {
  return (
    <section className="analysisPanel analysisUnavailable">
      <div className="analysisPanelHeader">
        <div>
          <h2>{title}</h2>
          <p>차트 분석 데이터를 기다리는 중입니다.</p>
        </div>
      </div>
      <div className="terminalEmpty">차트 데이터가 준비되면 표시됩니다.</div>
    </section>
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

function TechnicalItem({ label, value, tone = "neutral" }: { label: string; value: string; tone?: "ok" | "warn" | "danger" | "neutral" }) {
  return (
    <div className={`technicalItem ${tone}`}>
      <span>{label}</span>
      <strong>{humanizeToken(value)}</strong>
    </div>
  );
}

function verdictForState(state: PositionState): string {
  if (state.status === "healthy") return "데이터상 진입 논리는 유지 중입니다. 다만 계획한 손절/익절 기준과 수익 반납 기준을 계속 확인해야 합니다.";
  if (state.status === "watch") return "유지 근거가 완전히 깨진 상태는 아니지만, 다음 지지/저항 반응과 점수 변화를 확인해야 합니다.";
  if (state.status === "risk_rising") return "리스크가 상승했습니다. 청산가 거리, 변동성, 수익 반납폭을 우선 점검해야 합니다.";
  if (state.status === "thesis_weakening") return "진입 논리가 약해지고 있습니다. 처음 들어간 이유가 아직 유효한지 메모와 차트 구조를 비교해야 합니다.";
  if (state.status === "critical") return "긴급 점검 구간입니다. 청산가 거리와 손실 제한 기준을 즉시 확인해야 합니다.";
  return "데이터가 충분하지 않아 판단을 보류해야 합니다. 포지션/시세 동기화 상태를 먼저 확인하세요.";
}

function directionalChartVerdict(payload: LivePositionPayload, chartAnalysis: PositionChartAnalysis | null): string {
  const direction = payload.position.direction;
  const support = chartAnalysis?.price_levels.support[0];
  const resistance = chartAnalysis?.price_levels.resistance[0];
  const invalidation = chartAnalysis?.price_levels.invalidation[0];
  if (!chartAnalysis) return verdictForState(payload.state);
  if (direction === "long") {
    return support
      ? `롱 기준 핵심은 ${formatPrice(support.price)} 지지 유지입니다. 무효화 기준은 ${invalidation ? formatPrice(invalidation.price) : "미지정"}로 봅니다.`
      : "롱 기준 지지 후보가 부족합니다. Entry와 Mark 관계를 먼저 확인해야 합니다.";
  }
  return resistance
    ? `숏 기준 핵심은 ${formatPrice(resistance.price)} 저항 유지입니다. 무효화 기준은 ${invalidation ? formatPrice(invalidation.price) : "미지정"}로 봅니다.`
    : "숏 기준 저항 후보가 부족합니다. Entry 위 반등 거래량을 먼저 확인해야 합니다.";
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

function chartLevels(payload: LivePositionPayload): Array<{ label: string; price: number; type: string; className: string }> {
  const { position, state } = payload;
  const base = [
    { label: "Entry", price: position.entry_price, type: "entry" },
    { label: "Mark", price: state.mark_price, type: "mark" },
    { label: "Liq", price: position.liquidation_price, type: "liquidation" },
    ...state.analysis.risk.critical_levels.map((level) => ({
      label: level.type,
      price: level.price,
      type: level.type
    }))
  ];
  const valid = base
    .filter((level): level is { label: string; price: number; type: string } => Number.isFinite(level.price))
    .map((level) => ({
      ...level,
      className: level.type.replace(/[^a-z0-9_-]/gi, "_").toLowerCase()
    }));
  if (valid.length) return valid;
  return [{ label: "Mark", price: state.mark_price ?? position.entry_price, type: "mark", className: "mark" }];
}

function priceToTop(price: number, min: number, max: number): number {
  if (!Number.isFinite(min) || !Number.isFinite(max) || max <= min) return 50;
  return Math.max(8, Math.min(92, 100 - ((price - min) / (max - min)) * 100));
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

function firstInsightParagraph(text: string): string {
  const paragraph = text.split("\n\n").find((part) => part.trim().length > 0)?.trim() ?? text.trim();
  return paragraph.length > 260 ? `${paragraph.slice(0, 257)}...` : paragraph;
}
