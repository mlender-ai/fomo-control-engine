"use client";

import Link from "next/link";
import {
  Activity,
  BrainCircuit,
  FileClock,
  Focus,
  NotebookPen,
  RefreshCw,
  ShieldCheck,
  TestTube2,
  UploadCloud
} from "lucide-react";
import { FormEvent, useEffect, useState } from "react";
import { TerminalPanel, TerminalWarning } from "@/components/terminal";
import { HealthScoreBreakdownView } from "@/components/score-breakdown";
import { PositionChart } from "@/components/position/PositionChart";
import { DEFAULT_TA_LAYER, type TaLayer } from "@/components/position/taLayers";
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
  trendLabel,
  volumeStateLabel,
  yesNoLabel
} from "@/lib/labels/marketStateLabels";

type MetricTone = "positive" | "negative" | "warning" | "neutral" | "info" | "agent";

const LIVE_POSITION_SYNC_INTERVAL_SECONDS = 30;

export function LivePositionCockpit() {
  const [data, setData] = useState<LivePositionsResponse | null>(null);
  const [selectedId, setSelectedId] = useState("");
  const [connectionTest, setConnectionTest] = useState<BitgetConnectionTest | null>(null);
  const [loading, setLoading] = useState(true);
  const [actionLoading, setActionLoading] = useState("");
  const [notice, setNotice] = useState("");
  const [error, setError] = useState("");
  const [activeTaLayer, setActiveTaLayer] = useState<TaLayer>(DEFAULT_TA_LAYER);
  const [selectedChartAnalysis, setSelectedChartAnalysis] = useState<PositionChartAnalysis | null>(null);
  const [selectedChartLoading, setSelectedChartLoading] = useState(false);
  const [selectedChartError, setSelectedChartError] = useState("");
  const [selectedDetail, setSelectedDetail] = useState<LivePositionDetail | null>(null);

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
    }, LIVE_POSITION_SYNC_INTERVAL_SECONDS * 1000);
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
  const selectedDetailPayload = selectedDetail?.position.id === selected?.position.id ? selectedDetail : null;
  const selectedPayload = selectedDetailPayload ?? selected;

  async function loadSelectedDetail(positionId: string) {
    try {
      setSelectedDetail(await api.livePosition(positionId));
    } catch {
      setSelectedDetail(null);
    }
  }

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
    void loadSelectedDetail(selected.position.id);
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
          {selectedPayload ? (
            <>
              <PositionVerdictBar payload={selectedPayload} chartAnalysis={selectedChartAnalysis} />
              <section className="cockpitMainGrid">
                <PositionChart
                  analysis={selectedChartAnalysis}
                  loading={selectedChartLoading}
                  error={selectedChartError}
                  onRetry={() => void loadSelectedChart(selectedPayload.position.id)}
                  trendSummary={trendLabel(selectedPayload.state.analysis.technical.trend)}
                  activeLayer={activeTaLayer}
                  onLayerChange={setActiveTaLayer}
                />
                <ActionPlanPanel payload={selectedPayload} />
              </section>
              <EvidenceAccordion
                payload={selectedPayload}
                chartAnalysis={selectedChartAnalysis}
                onCreateInsight={() => createInsight(selectedPayload.position.id)}
                busy={actionLoading === `insight:${selectedPayload.position.id}`}
                onFocusLayer={focusTaLayer(setActiveTaLayer)}
              />
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
  const sortedPositions = [...positions].sort((left, right) => {
    const severityDelta = right.state.severity_rank - left.state.severity_rank;
    if (severityDelta !== 0) return severityDelta;
    return Math.abs(right.state.pnl_percent) - Math.abs(left.state.pnl_percent);
  });
  return (
    <section className="positionStrip" aria-label="보유 포지션">
      {sortedPositions.map((item) => (
        <button
          className={`positionStripCard severity-${item.state.severity_rank} ${item.position.id === selectedId ? "selected" : ""}`}
          key={item.position.id}
          onClick={() => onSelect(item.position.id)}
          type="button"
        >
          <strong>{item.position.symbol}</strong>
          <span>{directionLabel(item.position.direction)} · {item.position.leverage}x</span>
          <em className={item.state.pnl_percent >= 0 ? "successText" : "dangerText"}>{signedPercent(item.state.pnl_percent)}</em>
          <small>건강도 {item.state.health_score}</small>
          {liquidationMissing(item) ? <span className="missingLiqBadge">청산가 미수신</span> : null}
          <StatusPill status={item.state.status} label={item.state.status_label} />
        </button>
      ))}
    </section>
  );
}

function PositionVerdictBar({ payload, chartAnalysis }: { payload: LivePositionPayload; chartAnalysis: PositionChartAnalysis | null }) {
  const { position, state } = payload;
  return (
    <section className={`verdictBar status-${state.status}`}>
      <div className="verdictBarMain">
        <div className="verdictIdentity">
          <span>선택 포지션</span>
          <strong>{position.symbol} · {directionLabel(position.direction)} {position.leverage}x</strong>
          <StatusPill status={state.status} label={state.status_label} />
        </div>
        <div className="verdictStatement">
          <span>현재 판단</span>
          <p>{directionalChartVerdict(payload, chartAnalysis)}</p>
        </div>
        <div className={`verdictHealth tone-${healthTone(state.health_score)}`}>
          <span>건강도</span>
          <strong>{state.health_score}</strong>
          <small>/100</small>
        </div>
      </div>
      <div className="verdictMetrics">
        <PositionHeaderMetric label={`손익률 (${pnlSourceLabel(state.pnl_source)})`} value={signedPercent(state.pnl_percent)} tone={state.pnl_percent >= 0 ? "positive" : "negative"} />
        <PositionHeaderMetric label="진입가" value={formatPrice(position.entry_price)} />
        <PositionHeaderMetric label="현재가" value={formatNullablePrice(state.mark_price)} tone="info" />
        <PositionHeaderMetric label="청산가" value={liquidationMissing(payload) ? "청산가 미수신" : formatNullablePrice(position.liquidation_price)} tone={liquidationMissing(payload) ? "warning" : "neutral"} />
        <PositionHeaderMetric label="청산가 거리" value={formatDistance(state.liquidation_distance_pct)} tone={liquidationTone(state.liquidation_distance_pct)} />
      </div>
      {liquidationMissing(payload) ? (
        <div className="selectedPositionWarning">청산가가 거래소에서 수신되지 않았습니다. {position.leverage}x 포지션은 수동으로 청산가와 증거금 상태를 확인하세요.</div>
      ) : null}
    </section>
  );
}

function ActionPlanPanel({ payload }: { payload: LivePositionPayload }) {
  const plan = actionPlanForPayload(payload);
  const rows = actionPlanRows(plan);
  const liquidationWarning = typeof plan?.liquidation?.warning === "string" ? plan.liquidation.warning : "";
  return (
    <section className="focusPanel actionPlanPanel">
      <div className="focusPanelHeader">
        <div>
          <h2>액션 플랜</h2>
          <p>지금 볼 가격과 발생 시 행동</p>
        </div>
        <span>{plan?.as_of ? `기준 ${new Date(plan.as_of).toLocaleString()}` : "데이터 부족"}</span>
      </div>
      {rows.length ? (
        <div className="actionPlanRows">
          {rows.map((row) => (
            <div className={`actionPlanRow tone-${row.tone}`} key={`${row.kind}-${row.price}-${row.condition}`}>
              <span>{row.kind}</span>
              <strong>{row.price ?? row.condition}</strong>
              <em>{row.action}</em>
              <small>{row.basis}</small>
            </div>
          ))}
        </div>
      ) : (
        <div className="terminalEmpty">액션 플랜을 만들 가격 근거가 부족합니다.</div>
      )}
      {liquidationWarning ? <div className="actionPlanWarning">{liquidationWarning}</div> : null}
    </section>
  );
}

function EvidenceAccordion({
  payload,
  chartAnalysis,
  onCreateInsight,
  busy,
  onFocusLayer
}: {
  payload: LivePositionPayload;
  chartAnalysis: PositionChartAnalysis | null;
  onCreateInsight: () => Promise<void> | void;
  busy: boolean;
  onFocusLayer: (layer: TaLayer) => void;
}) {
  return (
    <section className="evidenceAccordion" aria-label="판단 근거">
      <div className="evidenceAccordionHeader">
        <strong>판단 근거</strong>
        <span>필요할 때만 펼쳐 보세요. 위 판단과 액션 플랜은 아래 데이터에서 계산됩니다.</span>
      </div>
      <details className="evidenceSection">
        <summary>
          <strong>AI 해설</strong>
          <small>{insightSummaryHint(payload)}</small>
        </summary>
        <div className="evidenceBody">
          <InsightEvidence payload={payload} onCreateInsight={onCreateInsight} busy={busy} />
        </div>
      </details>
      <details className="evidenceSection">
        <summary>
          <strong>와이코프 근거</strong>
          <small>{phaseHintLabel(payload.state.analysis.wyckoff.phase ?? payload.state.analysis.wyckoff.phase_hint)}</small>
        </summary>
        <div className="evidenceBody">
          <ChartFocusButton layer="wyckoff" onFocusLayer={onFocusLayer} />
          <WyckoffEvidence state={payload.state} />
        </div>
      </details>
      <details className="evidenceSection">
        <summary>
          <strong>기술지표 근거</strong>
          <small>{trendLabel(payload.state.analysis.technical.trend)}</small>
        </summary>
        <div className="evidenceBody">
          <ChartFocusButton layer="structure" onFocusLayer={onFocusLayer} />
          <TechnicalEvidence state={payload.state} />
        </div>
      </details>
      <details className="evidenceSection">
        <summary>
          <strong>수급 근거</strong>
          <small>{chartAnalysis ? volumeStateLabel(chartAnalysis.volume_xray.volume_state) : "차트 데이터 대기 중"}</small>
        </summary>
        <div className="evidenceBody">
          <ChartFocusButton layer="flow" onFocusLayer={onFocusLayer} />
          {chartAnalysis ? (
            <div className="evidenceVolumeGrid">
              <VolumeProfilePanel analysis={chartAnalysis} />
              <VolumeXrayPanel analysis={chartAnalysis} />
            </div>
          ) : (
            <div className="terminalEmpty">차트 데이터가 준비되면 표시됩니다.</div>
          )}
        </div>
      </details>
      <details className="evidenceSection">
        <summary>
          <strong>리스크 분해</strong>
          <small>리스크 점수 {payload.state.risk_score}/100</small>
        </summary>
        <div className="evidenceBody">
          <RiskEvidence payload={payload} />
        </div>
      </details>
      <details className="evidenceSection">
        <summary>
          <strong>기록</strong>
          <small>{payload.recent_events.length ? `이벤트 ${payload.recent_events.length}건` : "이벤트 없음"}</small>
        </summary>
        <div className="evidenceBody">
          <TimelineEvidence payload={payload} />
        </div>
      </details>
    </section>
  );
}

function ChartFocusButton({ layer, onFocusLayer }: { layer: TaLayer; onFocusLayer: (layer: TaLayer) => void }) {
  const label = layer === "wyckoff" ? "와이코프" : layer === "harmonic" ? "하모닉" : layer === "flow" ? "수급" : "지지·저항";
  return (
    <button className="chartFocusButton" onClick={() => onFocusLayer(layer)} type="button">
      <Focus size={14} />
      차트에서 {label} 단독으로 보기
    </button>
  );
}

function InsightEvidence({
  payload,
  onCreateInsight,
  busy
}: {
  payload: LivePositionPayload;
  onCreateInsight: () => Promise<void> | void;
  busy: boolean;
}) {
  const insight = payload.latest_insight;
  const insightIsFresh = Boolean(insight && !payload.insight_status.is_stale);
  if (insightIsFresh && insight) {
    return (
      <div className="insightEvidence">
        <div className="insightEvidenceHeader">
          <small>{insightTimestampLabel(payload)} · {insightSourceLabel(insight.insight_source)}</small>
          <button className="button secondary" onClick={onCreateInsight} disabled={busy} type="button">
            <BrainCircuit size={16} />
            {busy ? "생성 중" : "다시 생성"}
          </button>
        </div>
        <p>{localizeMarketCodes(insight.insight_text)}</p>
      </div>
    );
  }
  if (insight) {
    return <InsightStaleNotice payload={payload} onCreateInsight={onCreateInsight} busy={busy} />;
  }
  return (
    <div className="insightEmpty">
      <strong>아직 인사이트가 없습니다.</strong>
      <span>판단과 액션 플랜은 현재 데이터로 이미 표시되어 있습니다. 해설이 필요하면 인사이트 생성을 누르세요.</span>
      <button className="button" onClick={onCreateInsight} disabled={busy}>
        <BrainCircuit size={16} />
        {busy ? "생성 중" : "인사이트 생성"}
      </button>
    </div>
  );
}

function WyckoffEvidence({ state }: { state: PositionState }) {
  const wyckoff = state.analysis.wyckoff;
  const mtf = wyckoff.mtf;
  return (
    <div className="tabMetricLayout">
      <PositionHeaderMetric label="매집 점수" value={wyckoff.accumulation_score} tone="info" />
      <PositionHeaderMetric label="분산 점수" value={wyckoff.distribution_score} tone="warning" />
      <PositionHeaderMetric label="국면" value={phaseHintLabel(wyckoff.phase ?? wyckoff.phase_hint)} />
      <PositionHeaderMetric label="상위 정합" value={phaseHintLabel(mtf?.alignment)} tone={mtf?.alignment === "conflicting" ? "negative" : mtf?.alignment === "aligned" ? "positive" : "neutral"} />
      <PositionHeaderMetric label="Spring 후보" value={yesNoLabel(wyckoff.spring_candidate)} />
      <PositionHeaderMetric label="SOS 후보" value={yesNoLabel(wyckoff.sos_candidate)} />
      <PositionHeaderMetric label="LPS 후보" value={yesNoLabel(wyckoff.lps_candidate)} />
      <PositionHeaderMetric label="UTAD 후보" value={yesNoLabel(Boolean(wyckoff.utad_candidate))} tone={wyckoff.utad_candidate ? "warning" : "neutral"} />
      <PositionHeaderMetric label="SOW 후보" value={yesNoLabel(Boolean(wyckoff.sow_candidate))} tone={wyckoff.sow_candidate ? "warning" : "neutral"} />
      <PositionHeaderMetric label="LPSY 후보" value={yesNoLabel(Boolean(wyckoff.lpsy_candidate))} tone={wyckoff.lpsy_candidate ? "warning" : "neutral"} />
      <p className="tabExplanation">{wyckoff.structure_comment}</p>
    </div>
  );
}

function TechnicalEvidence({ state }: { state: PositionState }) {
  const technical = state.analysis.technical;
  return (
    <div className="tabMetricLayout">
      <PositionHeaderMetric label="추세" value={trendLabel(technical.trend)} tone={technical.trend_alignment.includes("against") ? "negative" : "positive"} />
      <PositionHeaderMetric label="RSI" value={rsiLabel(technical.rsi_state)} />
      <PositionHeaderMetric label="MACD" value={macdLabel(technical.macd_state)} tone={technical.macd_state.includes("bearish") ? "negative" : "positive"} />
      <PositionHeaderMetric label="볼린저" value={bollingerLabel(technical.bollinger_state)} />
      <PositionHeaderMetric label="거래량" value={volumeStateLabel(technical.volume_state)} tone={technical.volume_state.includes("declining") ? "warning" : "positive"} />
      <PositionHeaderMetric
        label="지지"
        value={supportStatusLabel(technical.support_status)}
        tone={technical.support_status === "broken" || technical.support_status === "at_risk" ? "negative" : technical.support_status === "near" ? "warning" : "positive"}
      />
      <PositionHeaderMetric
        label="저항"
        value={resistanceStatusLabel(technical.resistance_status)}
        tone={technical.resistance_status === "broken" ? "negative" : technical.resistance_status === "testing" ? "warning" : "neutral"}
      />
    </div>
  );
}

function RiskEvidence({ payload }: { payload: LivePositionPayload }) {
  const { position, state } = payload;
  return (
    <div className="tabRiskGrid">
      <div className="tabRiskStack">
        <HealthScoreBreakdownView components={state.score_json.health_components} />
        <div className="tabMetricLayout compact">
          <PositionHeaderMetric label="리스크 점수" value={`${state.risk_score}/100`} tone={state.risk_score >= 70 ? "negative" : state.risk_score >= 55 ? "warning" : "neutral"} />
          <PositionHeaderMetric label="방향 논리" value={`${state.thesis_delta > 0 ? "+" : ""}${state.thesis_delta}`} tone={state.thesis_delta < -20 ? "negative" : state.thesis_delta < -10 ? "warning" : "neutral"} />
          <PositionHeaderMetric label="수익 반납" value={formatDistance(state.analysis.risk.profit_giveback_pct)} />
          <PositionHeaderMetric label="손절 기준" value={formatNullablePrice(position.planned_stop_price)} tone="warning" />
          <PositionHeaderMetric label="ATR 리스크" value={atrRiskLabel(state.analysis.risk.atr_risk)} />
          <PositionHeaderMetric label="심각도" value={state.severity_rank} tone={state.severity_rank >= 3 ? "negative" : state.severity_rank >= 2 ? "warning" : "neutral"} />
        </div>
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

function TimelineEvidence({ payload }: { payload: LivePositionPayload }) {
  return (
    <div className="timelineTab">
      <div className="snapshotSummary">
        <PositionHeaderMetric label="마지막 스냅샷" value={new Date(payload.latest_snapshot.created_at).toLocaleString()} />
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
  const [activeTaLayer, setActiveTaLayer] = useState<TaLayer>(DEFAULT_TA_LAYER);
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
        </div>
      </header>

      {error ? <TerminalWarning tone="error">{error}</TerminalWarning> : null}
      {notice ? <TerminalWarning tone="info">{notice}</TerminalWarning> : null}

      <PositionVerdictBar payload={detail} chartAnalysis={chartAnalysis} />

      <section className="positionDetailMain">
        <PositionChart
          analysis={chartAnalysis}
          loading={chartLoading}
          error={chartError}
          onRetry={() => void loadChart(timeframe)}
          trendSummary={trendLabel(detail.state.analysis.technical.trend)}
          activeLayer={activeTaLayer}
          onLayerChange={setActiveTaLayer}
        />
        <ActionPlanPanel payload={detail} />
      </section>

      <EvidenceAccordion
        payload={detail}
        chartAnalysis={chartAnalysis}
        onCreateInsight={() => createInsight()}
        busy={busy === "insight"}
        onFocusLayer={focusTaLayer(setActiveTaLayer)}
      />

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

function InsightStaleNotice({
  payload,
  onCreateInsight,
  busy,
  compact = false
}: {
  payload: LivePositionPayload;
  onCreateInsight: () => Promise<void> | void;
  busy: boolean;
  compact?: boolean;
}) {
  const status = payload.insight_status;
  const generated = status.generated_for;
  const basisPrice = payload.latest_insight?.basis_mark_price ?? generated?.mark_price ?? null;
  const priceDrift = payload.latest_insight?.price_drift_pct ?? status.price_drift_pct;
  return (
    <div className={`insightStaleNotice ${compact ? "compact" : ""}`}>
      <div className="insightStaleHeader">
        <strong>과거 판단 (재생성 필요)</strong>
        <button className="button secondary" onClick={onCreateInsight} disabled={busy} type="button">
          <BrainCircuit size={16} />
          {busy ? "재생성 중" : "인사이트 재생성"}
        </button>
      </div>
      <p>이 인사이트는 {status.age_minutes ?? "-"}분 전 가격({formatNullablePrice(basisPrice)}) 기준입니다. 현재가와 {priceDrift === null || priceDrift === undefined ? "-" : signedPercent(priceDrift)} 차이입니다.</p>
      <div className="insightStaleMeta">
        <span>기준 {generated?.as_of ? new Date(generated.as_of).toLocaleString() : "-"}</span>
        <span>현재 기준 {new Date(status.current_as_of).toLocaleString()}</span>
        <span>{status.message}</span>
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

function focusTaLayer(setActiveTaLayer: (layer: TaLayer) => void) {
  return (layer: TaLayer) => {
    setActiveTaLayer(layer);
    document.querySelector(".positionChartPanel")?.scrollIntoView({ behavior: "smooth", block: "start" });
  };
}

function actionPlanForPayload(payload: LivePositionPayload) {
  return payload.action_plan ?? payload.latest_insight?.action_plan ?? null;
}

function actionPlanRows(plan: LivePositionPayload["action_plan"]) {
  if (!plan) return [];
  const rows: Array<{ kind: string; price?: string; condition?: string; action: string; basis: string; tone: "danger" | "positive" | "warning" | "neutral" }> = [];
  if (plan.invalidation) {
    rows.push({
      kind: "무효화",
      price: `${formatNullablePrice(plan.invalidation.price)} · ${formatDistance(plan.invalidation.distance_pct)}`,
      action: plan.invalidation.action ?? "조건 확인",
      basis: plan.invalidation.basis ?? "무효화 기준",
      tone: "danger"
    });
  }
  const takeProfitTargets = Array.isArray(plan.take_profit) ? plan.take_profit : [];
  const watchTriggers = Array.isArray(plan.watch_triggers) ? plan.watch_triggers : [];
  for (const target of takeProfitTargets.slice(0, 3)) {
    rows.push({
      kind: "익절",
      price: `${formatNullablePrice(target.price)} · ${formatDistance(target.distance_pct)}`,
      action: target.action ?? "부분 익절 검토",
      basis: target.basis ?? "익절 후보",
      tone: "positive"
    });
  }
  for (const trigger of watchTriggers.slice(0, 3)) {
    rows.push({
      kind: "감시",
      condition: trigger.condition ?? "조건 확인",
      action: "조건 확인",
      basis: trigger.meaning ?? "추가 확인 필요",
      tone: "warning"
    });
  }
  if (typeof plan.liquidation?.price === "number") {
    rows.push({
      kind: "청산가",
      price: formatNullablePrice(plan.liquidation.price),
      action: "거리 확인",
      basis: "거래소 수신 청산가",
      tone: "neutral"
    });
  }
  return rows.slice(0, 6);
}

function insightSourceLabel(source: string): string {
  if (source === "llm") return "LLM 해설";
  if (source === "fallback_template") return "템플릿 폴백";
  return "템플릿 해설";
}

function insightSummaryHint(payload: LivePositionPayload): string {
  const insight = payload.latest_insight;
  if (!insight) return "아직 생성 안 됨";
  if (payload.insight_status.is_stale) return "과거 판단 · 재생성 필요";
  return `갱신 ${new Date(insight.created_at).toLocaleTimeString()}`;
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
  const invalidationLabel = invalidation && typeof invalidation.price === "number" ? formatPrice(invalidation.price) : "사용자 기준 필요";
  if (!chartAnalysis) return verdictForState(payload.state);
  if (direction === "long") {
    return support
      ? `롱 기준 핵심은 ${formatPrice(support.price)} 지지 유지입니다. 무효화 기준은 ${invalidationLabel}로 봅니다.`
      : "롱 기준 지지 후보가 부족합니다. 진입가와 현재가 관계를 먼저 확인해야 합니다.";
  }
  return resistance
    ? `숏 기준 핵심은 ${formatPrice(resistance.price)} 저항 유지입니다. 무효화 기준은 ${invalidationLabel}로 봅니다.`
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

function pnlSourceLabel(source: "exchange" | "computed"): string {
  return source === "exchange" ? "거래소" : "계산";
}

function liquidationMissing(payload: LivePositionPayload): boolean {
  return payload.position.status === "open" && payload.position.liquidation_price === null && payload.position.leverage >= 5;
}

function numberOrNull(value: FormDataEntryValue | null): number | null {
  const parsed = Number(value);
  return Number.isFinite(parsed) && parsed > 0 ? parsed : null;
}

function insightTimestampLabel(payload: LivePositionPayload): string {
  const basisAt = payload.latest_insight?.as_of ?? payload.insight_status.generated_for?.as_of;
  const basisPrice = payload.latest_insight?.basis_mark_price ?? payload.insight_status.generated_for?.mark_price ?? null;
  return `기준 ${basisAt ? new Date(basisAt).toLocaleString() : "-"} · 당시 가격 ${formatNullablePrice(basisPrice)}`;
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
