"use client";

import Link from "next/link";
import {
  Activity,
  BrainCircuit,
  FileClock,
  NotebookPen,
  RefreshCw,
  ShieldCheck,
  TestTube2,
  UploadCloud
} from "lucide-react";
import { FormEvent, useEffect, useState } from "react";
import { TerminalPanel, TerminalWarning } from "@/components/terminal";
import { PositionChart } from "@/components/position/PositionChart";
import { hiddenPriceLinesForAnalysis } from "@/components/position/PriceLevelOverlay";
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
import {
  atrRiskLabel,
  bollingerLabel,
  connectionStatusLabel,
  criticalLevelTypeLabel,
  directionLabel,
  genericMarketStateLabel,
  localizeMarketCodes,
  macdLabel,
  phaseHintLabel,
  resistanceStatusLabel,
  rsiLabel,
  supportStatusLabel,
  timeframeLabel,
  trendLabel,
  volumeStateLabel,
  yesNoLabel
} from "@/lib/labels/marketStateLabels";

type MetricTone = "positive" | "negative" | "warning" | "neutral" | "info" | "agent";
type DetailTab = "insight" | "wyckoff" | "technical" | "risk" | "timeline";

const detailTabs: Array<{ id: DetailTab; label: string }> = [
  { id: "insight", label: "요약" },
  { id: "wyckoff", label: "와이코프" },
  { id: "technical", label: "기술분석" },
  { id: "risk", label: "리스크" },
  { id: "timeline", label: "기록" }
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
  const [selectedChartAnalysis, setSelectedChartAnalysis] = useState<PositionChartAnalysis | null>(null);
  const [selectedChartLoading, setSelectedChartLoading] = useState(false);
  const [selectedChartError, setSelectedChartError] = useState("");

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
      setError(err instanceof Error ? err.message : "라이브 포지션 데이터를 불러오지 못했습니다.");
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
      setNotice(`Bitget 공개 시세 ${result.public_market_data.ok ? "OK" : "ERROR"} · 포지션 권한 ${connectionStatusLabel(result.private_positions.status)}`);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Bitget 연결 테스트에 실패했습니다.");
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

  async function loadSelectedChart(positionId: string) {
    setSelectedChartLoading(true);
    setSelectedChartError("");
    try {
      setSelectedChartAnalysis(await api.positionChartAnalysis(positionId, "4h"));
    } catch (err) {
      setSelectedChartAnalysis(null);
      setSelectedChartError(err instanceof Error ? err.message : "차트 분석 데이터를 불러오지 못했습니다.");
    } finally {
      setSelectedChartLoading(false);
    }
  }

  useEffect(() => {
    if (!selected?.position.id) return;
    void loadSelectedChart(selected.position.id);
  }, [selected?.position.id, data?.timestamp]);

  return (
    <div className="page cockpitPage">
      <header className="cockpitToolbar">
        <div>
          <p className="eyebrow">라이브 포지션 관제</p>
          <h1>내 포지션 관제</h1>
        </div>
        <div className="cockpitToolbarActions">
          <span className="lastSyncText">{data?.timestamp ? `마지막 동기화 ${new Date(data.timestamp).toLocaleTimeString()}` : "마지막 동기화 -"}</span>
          <button className="button" onClick={syncPositions} disabled={actionLoading === "sync"}>
            <UploadCloud size={16} />
            {actionLoading === "sync" ? "동기화 중" : "실시간 동기화"}
          </button>
          <button className="iconButton secondary" onClick={() => void load(false)} disabled={loading} title="화면 새로고침">
            <RefreshCw size={16} />
          </button>
          <button className="iconButton secondary" onClick={testConnection} disabled={actionLoading === "test"} title="Bitget 연결 테스트">
            <TestTube2 size={16} />
          </button>
        </div>
      </header>

      {error ? <TerminalWarning tone="error">{error}</TerminalWarning> : null}
      {notice ? <TerminalWarning tone="info">{notice}</TerminalWarning> : null}

      {connectionTest ? (
        <div className={`connectionNotice ${connectionTest.private_positions.ok ? "ok" : "warn"}`}>
          Bitget 공개 시세 {connectionTest.public_market_data.ok ? "OK" : "ERROR"} · 포지션 권한 {connectionStatusLabel(connectionTest.private_positions.status)} · 포지션 {connectionTest.private_positions.count}
        </div>
      ) : null}

      {loading && !data ? (
        <TerminalPanel title="라이브 포지션 로딩" subtitle="Bitget 동기화와 결정론적 분석을 시작합니다" status="neutral">
          <div className="terminalEmpty">라이브 포지션 관제 화면을 불러오는 중입니다.</div>
        </TerminalPanel>
      ) : positions.length ? (
        <>
          <PositionStrip positions={positions} selectedId={selected?.position.id ?? ""} onSelect={setSelectedId} />
          {selected ? (
            <>
              <SelectedPositionHeader payload={selected} />
              <section className="cockpitMainGrid">
                <PositionChart
                  analysis={selectedChartAnalysis}
                  loading={selectedChartLoading}
                  error={selectedChartError}
                  onRetry={() => void loadSelectedChart(selected.position.id)}
                  trendSummary={trendLabel(selected.state.analysis.technical.trend)}
                />
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
    <section className="positionStrip" aria-label="보유 포지션">
      {positions.map((item) => (
        <button
          className={`positionStripCard ${item.position.id === selectedId ? "selected" : ""}`}
          key={item.position.id}
          onClick={() => onSelect(item.position.id)}
          type="button"
        >
          <strong>{item.position.symbol}</strong>
          <span>{directionLabel(item.position.direction)} · {item.position.leverage}x</span>
          <em className={item.state.pnl_percent >= 0 ? "successText" : "dangerText"}>{signedPercent(item.state.pnl_percent)}</em>
          <small>건강도 {item.state.health_score}</small>
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
        <span>선택 포지션</span>
        <strong>{position.symbol} · {directionLabel(position.direction)} {position.leverage}x</strong>
      </div>
      <div className="selectedPositionMetrics">
        <PositionHeaderMetric label="손익률" value={signedPercent(state.pnl_percent)} tone={state.pnl_percent >= 0 ? "positive" : "negative"} />
        <PositionHeaderMetric label="진입가" value={formatPrice(position.entry_price)} />
        <PositionHeaderMetric label="현재가" value={formatNullablePrice(state.mark_price)} tone="info" />
        <PositionHeaderMetric label="청산가" value={formatNullablePrice(position.liquidation_price)} tone="warning" />
        <PositionHeaderMetric label="청산가 거리" value={formatDistance(state.liquidation_distance_pct)} tone={liquidationTone(state.liquidation_distance_pct)} />
        <PositionHeaderMetric label="건강도" value={`${state.health_score}/100`} tone={healthTone(state.health_score)} />
      </div>
      <div className="selectedPositionState">
        <span>상태</span>
        <strong>{state.status_label}</strong>
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
  const { position, state, latest_insight: insight, insight_status: insightStatus } = payload;
  const insightIsFresh = Boolean(insight && !insightStatus.is_stale);
  return (
    <section className="focusPanel insightFocusPanel">
      <div className="focusPanelHeader">
        <div>
          <h2>AI 포지션 인사이트</h2>
          <p>현재 판단과 다음 확인 지점</p>
        </div>
        <button className="button secondary" onClick={() => onCreateInsight(position.id)} disabled={busy}>
          <BrainCircuit size={16} />
          {busy ? "생성 중" : "인사이트 생성"}
        </button>
      </div>
      <div className={`insightJudgement status-${state.status}`}>
        <span>현재 판단</span>
        <strong>{state.status_label}</strong>
        <p>{verdictForState(state)}</p>
      </div>
      {insightIsFresh && insight ? (
        <div className="insightPreview">
          <p>{localizeMarketCodes(firstInsightParagraph(insight.insight_text))}</p>
          <small>{insightTimestampLabel(payload)} · 건강도 {insight.health_score}/100</small>
        </div>
      ) : insight ? (
        <InsightStaleNotice payload={payload} compact />
      ) : (
        <div className="insightEmpty">
          <strong>아직 인사이트가 없습니다.</strong>
          <span>인사이트 생성 버튼을 눌러 현재 포지션 상태를 분석하세요.</span>
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
      <div className="detailTabList" role="tablist" aria-label="포지션 상세 탭">
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
  const insightIsFresh = Boolean(insight && !payload.insight_status.is_stale);
  return (
    <div className="tabContentGrid">
      <div className={`tabJudgement status-${payload.state.status}`}>
        <span>현재 판단</span>
        <strong>{payload.state.status_label}</strong>
        <p>{verdictForState(payload.state)}</p>
      </div>
      <div className="tabTextBlock">
        {insightIsFresh && insight ? (
          <>
            <p>{localizeMarketCodes(insight.insight_text)}</p>
            <small>{insightTimestampLabel(payload)} · 건강도 {insight.health_score}/100</small>
          </>
        ) : insight ? (
          <InsightStaleNotice payload={payload} />
        ) : (
          <div className="insightEmpty">
            <strong>아직 인사이트가 없습니다.</strong>
            <span>인사이트 생성 버튼을 눌러 현재 포지션 상태를 분석하세요.</span>
            <button className="button" onClick={() => onCreateInsight(payload.position.id)} disabled={busy}>
              <BrainCircuit size={16} />
              {busy ? "생성 중" : "인사이트 생성"}
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
      <PositionHeaderMetric label="매집 점수" value={wyckoff.accumulation_score} tone="info" />
      <PositionHeaderMetric label="분산 점수" value={wyckoff.distribution_score} tone="warning" />
      <PositionHeaderMetric label="국면 힌트" value={phaseHintLabel(wyckoff.phase_hint)} />
      <PositionHeaderMetric label="Spring 후보" value={yesNoLabel(wyckoff.spring_candidate)} />
      <PositionHeaderMetric label="SOS 후보" value={yesNoLabel(wyckoff.sos_candidate)} />
      <PositionHeaderMetric label="LPS 후보" value={yesNoLabel(wyckoff.lps_candidate)} />
      <p className="tabExplanation">{wyckoff.structure_comment}</p>
    </div>
  );
}

function TechnicalTab({ state }: { state: PositionState }) {
  const technical = state.analysis.technical;
  return (
    <div className="tabMetricLayout">
      <PositionHeaderMetric label="추세" value={trendLabel(technical.trend)} tone={technical.trend_alignment.includes("against") ? "negative" : "positive"} />
      <PositionHeaderMetric label="RSI" value={rsiLabel(technical.rsi_state)} />
      <PositionHeaderMetric label="MACD" value={macdLabel(technical.macd_state)} tone={technical.macd_state.includes("bearish") ? "negative" : "positive"} />
      <PositionHeaderMetric label="볼린저" value={bollingerLabel(technical.bollinger_state)} />
      <PositionHeaderMetric label="거래량" value={volumeStateLabel(technical.volume_state)} tone={technical.volume_state.includes("declining") ? "warning" : "positive"} />
      <PositionHeaderMetric label="지지" value={supportStatusLabel(technical.support_status)} tone={technical.support_status === "at_risk" ? "negative" : "positive"} />
      <PositionHeaderMetric label="저항" value={resistanceStatusLabel(technical.resistance_status)} />
    </div>
  );
}

function RiskTab({ payload }: { payload: LivePositionPayload }) {
  const { position, state } = payload;
  return (
    <div className="tabRiskGrid">
      <div className="tabMetricLayout">
        <PositionHeaderMetric label="청산가 거리" value={formatDistance(state.liquidation_distance_pct)} tone={liquidationTone(state.liquidation_distance_pct)} />
        <PositionHeaderMetric label="리스크 점수" value={`${state.risk_score}/100`} tone={state.risk_score >= 70 ? "negative" : state.risk_score >= 55 ? "warning" : "neutral"} />
        <PositionHeaderMetric label="손익률" value={signedPercent(state.pnl_percent)} tone={state.pnl_percent >= 0 ? "positive" : "negative"} />
        <PositionHeaderMetric label="수익 반납" value={formatDistance(state.analysis.risk.profit_giveback_pct)} />
        <PositionHeaderMetric label="손절 기준" value={formatNullablePrice(position.planned_stop_price)} tone="warning" />
        <PositionHeaderMetric label="ATR 리스크" value={atrRiskLabel(state.analysis.risk.atr_risk)} />
      </div>
      <div className="tabLevelsList">
        <strong>주의할 가격</strong>
        {state.analysis.risk.critical_levels.length ? (
          state.analysis.risk.critical_levels.map((level) => (
            <div key={`${level.type}-${level.price}`}>
              <span>{criticalLevelTypeLabel(level.type)}</span>
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
        <PositionHeaderMetric label="최근 건강도" value={`${payload.latest_snapshot.health_score}/100`} tone={healthTone(payload.latest_snapshot.health_score)} />
        <PositionHeaderMetric label="리스크" value={`${payload.latest_snapshot.risk_score}/100`} />
        <PositionHeaderMetric label="스냅샷" value={new Date(payload.latest_snapshot.created_at).toLocaleTimeString()} />
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
      setError(err instanceof Error ? err.message : "포지션 상세 데이터를 불러오지 못했습니다.");
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
      setChartError(err instanceof Error ? err.message : "차트 분석 데이터를 불러오지 못했습니다.");
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
      setError(err instanceof Error ? err.message : "상태 갱신에 실패했습니다.");
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
      setError(err instanceof Error ? err.message : "메모 저장에 실패했습니다.");
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
      setNotice(`청산 기록을 저장했습니다. 거래 ${trade.symbol} ${signedPercent(trade.pnl_percent)}`);
      await load();
      await loadChart(timeframe);
    } catch (err) {
      setError(err instanceof Error ? err.message : "청산 기록 저장에 실패했습니다.");
    } finally {
      setBusy("");
    }
  }

  if (loading && !detail) {
    return (
      <div className="page">
        <TerminalPanel title="포지션 로딩" subtitle={positionId} status="neutral">
          <div className="terminalEmpty">포지션 상세 화면을 불러오는 중입니다.</div>
        </TerminalPanel>
      </div>
    );
  }

  if (!detail) {
    return (
      <div className="page">
        {error ? <TerminalWarning tone="error">{error}</TerminalWarning> : <TerminalWarning tone="error">포지션을 찾을 수 없습니다.</TerminalWarning>}
      </div>
    );
  }

  const exitDefault = detail.state.mark_price ?? detail.position.current_price ?? detail.position.entry_price;

  return (
    <div className="page positionDetailPage">
      <header className="cockpitToolbar positionDetailToolbar">
        <div>
          <p className="eyebrow">포지션 상세 차트 분석</p>
          <h1>{detail.position.symbol} {directionLabel(detail.position.direction)} 차트 관제</h1>
        </div>
        <div className="cockpitToolbarActions">
          <label className="timeframeSelect">
            <span>봉 주기</span>
            <select value={timeframe} onChange={(event) => setTimeframe(event.target.value)}>
              <option value="15m">15분봉</option>
              <option value="1h">1시간봉</option>
              <option value="4h">4시간봉</option>
              <option value="1d">1일봉</option>
            </select>
          </label>
          <Link className="button secondary" href="/">
            <Activity size={16} />
            관제 화면
          </Link>
          <button className="button secondary" onClick={analyze} disabled={busy === "analyze"}>
            <RefreshCw size={16} />
            상태 갱신
          </button>
          <button className="button" onClick={createInsight} disabled={busy === "insight"}>
            <BrainCircuit size={16} />
            인사이트
          </button>
        </div>
      </header>

      {error ? <TerminalWarning tone="error">{error}</TerminalWarning> : null}
      {notice ? <TerminalWarning tone="info">{notice}</TerminalWarning> : null}

      <SelectedPositionHeader payload={detail} />

      <section className="positionDetailMain">
        <PositionChart
          analysis={chartAnalysis}
          loading={chartLoading}
          error={chartError}
          onRetry={() => void loadChart(timeframe)}
          trendSummary={trendLabel(detail.state.analysis.technical.trend)}
        />
        <PositionInsightRail payload={detail} chartAnalysis={chartAnalysis} onCreateInsight={() => createInsight()} busy={busy === "insight"} />
      </section>

      <section className="positionBottomAnalysis">
        {chartAnalysis ? <VolumeProfilePanel analysis={chartAnalysis} /> : <AnalysisUnavailable title="추정 볼륨 프로파일" />}
        {chartAnalysis ? <VolumeXrayPanel analysis={chartAnalysis} /> : <AnalysisUnavailable title="거래량 엑스레이" />}
        <TechnicalSummaryCard payload={detail} chartAnalysis={chartAnalysis} />
      </section>

      <PositionDetailTabs payload={detail} activeTab={detailTab} onTabChange={setDetailTab} onCreateInsight={() => createInsight()} busy={busy === "insight"} />

      <section className="grid two">
        <TerminalPanel title="진입 논리 메모" subtitle="진입 논리와 무효화/익절 기준은 AI가 점수를 계산하지 않고 비교 설명에만 사용합니다" status="accent">
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
              메모 저장
            </button>
          </form>
        </TerminalPanel>

        <TerminalPanel title="이탈 기록" subtitle="거래소 주문이 아니라 내부 복기용 청산 기록만 생성합니다" status={detail.position.status === "closed" ? "neutral" : "warning"}>
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
              내부 이탈 기록
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
  const { position, state, latest_insight: insight, insight_status: insightStatus } = payload;
  const insightIsFresh = Boolean(insight && !insightStatus.is_stale);
  const [showInputJson, setShowInputJson] = useState(false);
  const [copied, setCopied] = useState(false);
  const support = chartAnalysis?.price_levels.support[0];
  const resistance = chartAnalysis?.price_levels.resistance[0];
  const invalidation = chartAnalysis?.price_levels.invalidation[0];
  const liquidationOutOfRange = chartAnalysis ? hiddenPriceLinesForAnalysis(chartAnalysis).length > 0 : false;
  async function copyInsight() {
    if (!insight || insightStatus.is_stale) return;
    try {
      await navigator.clipboard.writeText(localizeMarketCodes(insight.insight_text));
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
          <small>{timeframeLabel(chartAnalysis?.timeframe ?? "4h")}</small>
        </div>
        <RailPrice label="진입가" value={formatPrice(position.entry_price)} />
        <RailPrice label="현재가" value={formatNullablePrice(state.mark_price)} tone="info" />
        <RailPrice label="청산가" value={formatNullablePrice(position.liquidation_price)} tone="danger" />
        {liquidationOutOfRange ? <div className="railPriceNotice">청산가가 현재 차트 범위 밖에 있습니다.</div> : null}
        <RailPrice label="지지선" value={support ? formatPrice(support.price) : "-"} />
        <RailPrice label="저항선" value={resistance ? formatPrice(resistance.price) : "-"} tone="warning" />
        <RailPrice label="무효화 가격" value={invalidation ? formatPrice(invalidation.price) : formatNullablePrice(position.planned_stop_price)} tone="danger" />
      </div>
      <div className="railSection">
        <div className="railSectionHeader">
          <strong>리스크 요약</strong>
          <small>주문 실행 없음</small>
        </div>
        <RailPrice label="건강도" value={`${state.health_score}/100`} tone={healthTone(state.health_score)} />
        <RailPrice label="리스크" value={`${state.risk_score}/100`} tone={state.risk_score >= 70 ? "negative" : state.risk_score >= 55 ? "warning" : "neutral"} />
        <RailPrice label="청산가 거리" value={formatDistance(state.liquidation_distance_pct)} tone={liquidationTone(state.liquidation_distance_pct)} />
        <RailPrice label="수익 반납" value={formatDistance(state.analysis.risk.profit_giveback_pct)} />
      </div>
      <div className="railSection aiInsightRailSection">
        <div className="railSectionHeader aiInsightHeader">
          <div>
            <strong>AI 포지션 인사이트</strong>
            {insight ? <small>{insightStatus.is_stale ? "재생성 필요" : `갱신 ${new Date(insight.created_at).toLocaleTimeString()}`}</small> : null}
          </div>
          <button className="button secondary" onClick={onCreateInsight} disabled={busy}>
            <BrainCircuit size={16} />
            {busy ? "인사이트 생성 중" : insight ? "다시 생성" : "인사이트 생성"}
          </button>
        </div>
        {insightIsFresh && insight ? (
          <>
            <div className="railInsightText full">{localizeMarketCodes(insight.insight_text)}</div>
            <div className="insightActionRow">
              <button className="button secondary" onClick={copyInsight} type="button">{copied ? "복사됨" : "복사"}</button>
              <button className="button secondary" onClick={() => setShowInputJson((value) => !value)} type="button">
                {showInputJson ? "입력 JSON 숨기기" : "입력 JSON 보기"}
              </button>
            </div>
            {showInputJson ? (
              <pre className="insightInputJson">{JSON.stringify(insight.input_json, null, 2)}</pre>
            ) : null}
          </>
        ) : insight ? (
          <InsightStaleNotice payload={payload} />
        ) : (
          <div className="railInsightEmpty">
            <strong>아직 생성된 포지션 인사이트가 없습니다.</strong>
            <p>현재 포지션 상태를 분석하려면 인사이트 생성을 눌러주세요.</p>
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

function InsightStaleNotice({ payload, compact = false }: { payload: LivePositionPayload; compact?: boolean }) {
  const status = payload.insight_status;
  const generated = status.generated_for;
  return (
    <div className={`insightStaleNotice ${compact ? "compact" : ""}`}>
      <strong>인사이트 재생성이 필요합니다.</strong>
      <p>{status.message}</p>
      <div className="insightStaleMeta">
        <span>생성 {status.insight_created_at ? new Date(status.insight_created_at).toLocaleString() : "-"}</span>
        <span>현재 기준 {new Date(status.current_snapshot_created_at).toLocaleString()}</span>
        {status.age_minutes !== null ? <span>경과 {status.age_minutes.toFixed(1)}분</span> : null}
      </div>
      {!compact ? (
        <>
          <div className="insightStaleGrid">
            <RailPrice label="생성 당시 손익률" value={generated?.pnl_percent === null || generated?.pnl_percent === undefined ? "-" : signedPercent(generated.pnl_percent)} />
            <RailPrice label="현재 손익률" value={signedPercent(status.current.pnl_percent)} tone={status.current.pnl_percent >= 0 ? "positive" : "negative"} />
            <RailPrice label="손익률 변화" value={status.current.pnl_delta_points === null || status.current.pnl_delta_points === undefined ? "-" : signedPercent(status.current.pnl_delta_points)} />
            <RailPrice label="건강도 변화" value={status.current.health_delta === null || status.current.health_delta === undefined ? "-" : `${status.current.health_delta > 0 ? "+" : ""}${status.current.health_delta}`} />
          </div>
          <div className="insightStaleReasons">
            {status.reasons.map((reason) => (
              <span key={reason}>{insightStaleReasonLabel(reason)}</span>
            ))}
          </div>
        </>
      ) : null}
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
          <h2>기술분석 요약</h2>
          <p>차트 아래 보조 요약</p>
        </div>
        <span>{payload.state.status_label}</span>
      </div>
      <div className="technicalSummaryGrid">
        <RailPrice label="추세" value={trendLabel(technical.trend)} tone={technical.trend_alignment.includes("against") ? "negative" : "positive"} />
        <RailPrice label="RSI" value={rsiLabel(technical.rsi_state)} />
        <RailPrice label="MACD" value={macdLabel(technical.macd_state)} tone={technical.macd_state.includes("bearish") ? "negative" : "positive"} />
        <RailPrice label="거래량" value={volumeStateLabel(chartAnalysis?.volume_xray.volume_state ?? technical.volume_state)} tone={chartAnalysis?.volume_xray.spike_detected ? "warning" : "neutral"} />
        <RailPrice label="와이코프" value={phaseHintLabel(wyckoff.phase_hint)} />
        <RailPrice label="마커 수" value={String(chartAnalysis?.wyckoff_markers.length ?? 0)} />
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

function EventList({ events }: { events: PositionEvent[] }) {
  if (!events.length) {
    return <div className="terminalEmpty">아직 포지션 이벤트가 없습니다.</div>;
  }
  return (
    <div className="eventTimeline">
      {events.map((event) => (
        <div className={`eventItem severity-${event.severity}`} key={event.id}>
          <div>
            <strong>{event.title}</strong>
            <span>{new Date(event.created_at).toLocaleString()} · {genericMarketStateLabel(event.event_type)}</span>
          </div>
          <p>{event.description}</p>
        </div>
      ))}
    </div>
  );
}

function NoPositionsState({ onSync, syncing }: { onSync: () => void; syncing: boolean }) {
  return (
    <TerminalPanel title="라이브 포지션 없음" subtitle="현재 열린 포지션이 없거나 Bitget read-only 동기화가 아직 연결되지 않았습니다" status="neutral">
      <div className="emptyStateAction">
        <ShieldCheck size={28} />
        <div>
          <strong>실제 보유 포지션이 감지되면 이 화면이 관제석으로 전환됩니다.</strong>
          <p>API 키는 read-only 권한만 사용하며, 이 제품에는 주문 실행 기능이 없습니다.</p>
        </div>
        <button className="button" onClick={onSync} disabled={syncing}>
          <UploadCloud size={16} />
          {syncing ? "동기화 중" : "Bitget 포지션 동기화"}
        </button>
      </div>
    </TerminalPanel>
  );
}

function StatusPill({ status, label }: { status: string; label: string }) {
  return <span className={`statusPill status-${status}`}>{label}</span>;
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
      : "롱 기준 지지 후보가 부족합니다. 진입가와 현재가 관계를 먼저 확인해야 합니다.";
  }
  return resistance
    ? `숏 기준 핵심은 ${formatPrice(resistance.price)} 저항 유지입니다. 무효화 기준은 ${invalidation ? formatPrice(invalidation.price) : "미지정"}로 봅니다.`
    : "숏 기준 저항 후보가 부족합니다. 진입가 위 반등 거래량을 먼저 확인해야 합니다.";
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

function numberOrNull(value: FormDataEntryValue | null): number | null {
  const parsed = Number(value);
  return Number.isFinite(parsed) && parsed > 0 ? parsed : null;
}

function firstInsightParagraph(text: string): string {
  const paragraph = text.split("\n\n").find((part) => part.trim().length > 0)?.trim() ?? text.trim();
  return paragraph.length > 260 ? `${paragraph.slice(0, 257)}...` : paragraph;
}

function insightTimestampLabel(payload: LivePositionPayload): string {
  const generatedAt = payload.insight_status.insight_created_at ?? payload.latest_insight?.created_at;
  const currentAt = payload.insight_status.current_snapshot_created_at;
  return `생성 ${generatedAt ? new Date(generatedAt).toLocaleString() : "-"} · 기준 ${new Date(currentAt).toLocaleString()}`;
}

function insightStaleReasonLabel(reason: string): string {
  if (reason === "NO_INSIGHT") return "인사이트 없음";
  if (reason === "INSIGHT_OLDER_THAN_30M") return "30분 초과";
  if (reason === "PNL_CHANGED") return "손익률 변화";
  if (reason === "MARK_PRICE_CHANGED") return "현재가 변화";
  if (reason === "HEALTH_CHANGED") return "건강도 변화";
  if (reason === "STATUS_CHANGED") return "상태 변경";
  return reason;
}
