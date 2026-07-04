"use client";

import Link from "next/link";
import {
  Activity,
  AlertTriangle,
  BrainCircuit,
  Calculator,
  FileClock,
  Landmark,
  NotebookPen,
  RefreshCw,
  ShieldCheck,
  TestTube2,
  UploadCloud
} from "lucide-react";
import { FormEvent, ReactNode, useEffect, useState } from "react";
import { TerminalPanel, TerminalWarning } from "@/components/terminal";
import { HealthScoreBreakdownView } from "@/components/score-breakdown";
import { PositionChart } from "@/components/position/PositionChart";
import { VolumeProfilePanel } from "@/components/position/VolumeProfilePanel";
import { VolumeXrayPanel } from "@/components/position/VolumeXrayPanel";
import {
  DEFAULT_LAYER_STATE,
  focusedTaLayer,
  isTaLayer,
  loadLayerState,
  saveLayerState,
  toggleLayer,
  type ChartLayerId,
  type ChartLayerState,
  type TaFocusLayer
} from "@/lib/chartLayers";
import {
  api,
  type BitgetConnectionTest,
  type LivePositionDetail,
  type LivePositionPayload,
  type LivePositionsResponse,
  type PositionActionPlan,
  type PositionChartAnalysis,
  type PositionEvent,
  type PositionState
} from "@/lib/api";
import { confidenceLabel, DEFAULT_DENSITY, eventDisplayLimit, loadDensity, type Density } from "@/lib/density";
import { formatPrice, signedPercent } from "@/lib/format";
import { plainifyTaText, splitWyckoffEvents, taPlainTooltip, taShortLabel } from "@/lib/labels/taGlossary";
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

type EvidenceModuleId = "wyckoff" | "harmonic" | "volume" | "indicators" | "risk" | "history";

const MODULE_LAYER: Record<EvidenceModuleId, TaFocusLayer | null> = {
  wyckoff: "wyckoff",
  harmonic: "harmonic",
  volume: "volume_profile",
  indicators: "indicators",
  risk: null,
  history: null
};

function moduleForLayer(layer: TaFocusLayer): EvidenceModuleId | null {
  const entry = (Object.entries(MODULE_LAYER) as Array<[EvidenceModuleId, TaFocusLayer | null]>).find(([, value]) => value === layer);
  return entry ? entry[0] : null;
}

const LIVE_POSITION_SYNC_INTERVAL_SECONDS = 30;

function usePositionWorkspace() {
  const [layers, setLayers] = useState<ChartLayerState>(DEFAULT_LAYER_STATE);
  const [highlightPrice, setHighlightPrice] = useState<number | null>(null);
  const [openModule, setOpenModule] = useState<EvidenceModuleId | null>(null);
  const [density, setDensity] = useState<Density>(DEFAULT_DENSITY);

  useEffect(() => {
    setLayers(loadLayerState());
    setDensity(loadDensity());
  }, []);

  useEffect(() => {
    saveLayerState(layers);
  }, [layers]);

  function handleToggleLayer(id: ChartLayerId, additive: boolean) {
    const next = toggleLayer(layers, id, additive);
    setLayers(next);
    if (isTaLayer(id) && next.ta.includes(id) && focusedTaLayer(next) === id) {
      const moduleId = moduleForLayer(id);
      if (moduleId) {
        setOpenModule(moduleId);
        window.setTimeout(() => {
          document.getElementById(`evidence-${moduleId}`)?.scrollIntoView({ behavior: "smooth", block: "nearest" });
        }, 0);
      }
    }
  }

  function handleModuleToggle(id: EvidenceModuleId) {
    const layer = MODULE_LAYER[id];
    if (openModule === id) {
      setOpenModule(null);
      if (layer) setLayers((current) => ({ ...current, ta: current.ta.filter((item) => item !== layer) }));
      return;
    }
    setOpenModule(id);
    if (layer) setLayers((current) => ({ ...current, ta: [layer] }));
  }

  return { layers, highlightPrice, setHighlightPrice, openModule, handleToggleLayer, handleModuleToggle, density };
}

export function LivePositionCockpit() {
  const [data, setData] = useState<LivePositionsResponse | null>(null);
  const [selectedId, setSelectedId] = useState("");
  const [connectionTest, setConnectionTest] = useState<BitgetConnectionTest | null>(null);
  const [loading, setLoading] = useState(true);
  const [actionLoading, setActionLoading] = useState("");
  const [notice, setNotice] = useState("");
  const [error, setError] = useState("");
  const [selectedChartAnalysis, setSelectedChartAnalysis] = useState<PositionChartAnalysis | null>(null);
  const [selectedChartLoading, setSelectedChartLoading] = useState(false);
  const [selectedChartError, setSelectedChartError] = useState("");
  const [selectedDetail, setSelectedDetail] = useState<LivePositionDetail | null>(null);
  const workspace = usePositionWorkspace();

  async function load(sync = false) {
    setError("");
    try {
      const next = sync ? await api.syncLivePositions() : await api.livePositions();
      const positions = next.positions ?? [];
      const normalized: LivePositionsResponse = {
        provider: next.provider,
        positions,
        open_count: next.open_count ?? positions.filter((item) => item.position.status === "open").length,
        needs_exit_record_count: next.needs_exit_record_count ?? positions.filter((item) => item.position.status !== "open").length,
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

  async function refreshSelected(positionId: string) {
    setActionLoading(`refresh:${positionId}`);
    setError("");
    setNotice("");
    try {
      await api.analyzeLivePosition(positionId);
      await Promise.all([loadSelectedDetail(positionId), loadSelectedChart(positionId), load(false)]);
      setNotice("포지션 상태와 액션 플랜을 갱신했습니다.");
    } catch (err) {
      setError(err instanceof Error ? err.message : "상태 갱신에 실패했습니다.");
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
              <PositionVerdictBar
                payload={selectedPayload}
                onRefresh={() => void refreshSelected(selectedPayload.position.id)}
                refreshing={actionLoading === `refresh:${selectedPayload.position.id}`}
              />
              <section className="cockpitMainGrid">
                <PositionChart
                  analysis={selectedChartAnalysis}
                  loading={selectedChartLoading}
                  error={selectedChartError}
                  onRetry={() => void loadSelectedChart(selectedPayload.position.id)}
                  trendSummary={trendLabel(selectedPayload.state.analysis.technical.trend)}
                  plan={actionPlanForPayload(selectedPayload)}
                  layers={workspace.layers}
                  onToggleLayer={workspace.handleToggleLayer}
                  highlightPrice={workspace.highlightPrice}
                />
                <ActionPlanPanel
                  payload={selectedPayload}
                  highlightPrice={workspace.highlightPrice}
                  onSelectPrice={workspace.setHighlightPrice}
                  onCreateInsight={() => createInsight(selectedPayload.position.id)}
                  busy={actionLoading === `insight:${selectedPayload.position.id}`}
                  density={workspace.density}
                />
              </section>
              <EvidenceAccordion
                payload={selectedPayload}
                chartAnalysis={selectedChartAnalysis}
                openModule={workspace.openModule}
                onModuleToggle={workspace.handleModuleToggle}
                density={workspace.density}
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
  const sortedPositions = [...positions].sort((left, right) => right.state.severity_rank - left.state.severity_rank);
  return (
    <section className="positionStrip" aria-label="보유 포지션">
      {sortedPositions.map((item) => (
        <button
          className={`positionStripCard severity-${item.state.severity_rank} ${item.position.id === selectedId ? "selected" : ""}`}
          key={item.position.id}
          onClick={() => onSelect(item.position.id)}
          type="button"
        >
          <strong>
            {item.position.symbol}
            {liquidationMissing(item) ? <AlertTriangle className="liqMissingIcon" size={12} aria-label="청산가 미수신" /> : null}
          </strong>
          <span>{directionLabel(item.position.direction)} · {item.position.leverage}x</span>
          <em className={item.state.pnl_percent >= 0 ? "successText" : "dangerText"}>{signedPercent(item.state.pnl_percent)}</em>
          <small>건강도 {item.state.health_score}</small>
          <StatusPill status={item.state.status} label={item.state.status_label} />
          <span className="stripHeadline">{plainifyTaText(headlineForPayload(item))}</span>
        </button>
      ))}
    </section>
  );
}

function PositionVerdictBar({
  payload,
  onRefresh,
  refreshing
}: {
  payload: LivePositionPayload;
  onRefresh: () => void;
  refreshing: boolean;
}) {
  const { position, state } = payload;
  const plan = actionPlanForPayload(payload);
  const asOf = plan?.as_of ? new Date(plan.as_of) : null;
  // 벽시계 대신 최신 분석 시각(state.as_of) 대비 나이로 신선도를 판정 (렌더 순수성 유지)
  const ageMinutes = asOf ? (new Date(payload.state.as_of).getTime() - asOf.getTime()) / 60000 : null;
  const freshness = ageMinutes === null ? "기준 데이터 없음" : ageMinutes <= 30 ? "신선" : "오래됨 · 갱신 권장";
  return (
    <section className={`verdictBar status-${state.status}`}>
      <div className="verdictTopRow">
        <strong className="verdictSymbol">{position.symbol} {directionLabel(position.direction)} {position.leverage}x</strong>
        <em
          className={`verdictPnl ${state.pnl_percent >= 0 ? "successText" : "dangerText"}`}
          title={`손익률 출처: ${pnlSourceLabel(state.pnl_source)}`}
        >
          {signedPercent(state.pnl_percent)}
          {state.pnl_source === "exchange" ? <Landmark size={12} /> : <Calculator size={12} />}
        </em>
        <StatusPill status={state.status} label={`${state.status_label} (${state.health_score}/100)`} />
      </div>
      <p className="verdictAction">→ {plainifyTaText(headlineForPayload(payload))}</p>
      <div className="verdictMeta">
        <span>{asOf ? `기준 ${asOf.toLocaleTimeString("ko-KR", { hour: "2-digit", minute: "2-digit" })}` : "기준 -"} · {freshness}</span>
        {liquidationMissing(payload) ? (
          <span className="verdictLiqWarning">
            <AlertTriangle size={12} /> 청산가 미수신 — {position.leverage}x 포지션은 청산가·증거금을 수동 확인하세요
          </span>
        ) : null}
        <button className="button secondary" onClick={onRefresh} disabled={refreshing} type="button">
          <RefreshCw size={14} />
          {refreshing ? "갱신 중" : "갱신"}
        </button>
      </div>
    </section>
  );
}

function ActionPlanPanel({
  payload,
  highlightPrice,
  onSelectPrice,
  onCreateInsight,
  busy,
  density
}: {
  payload: LivePositionPayload;
  highlightPrice: number | null;
  onSelectPrice: (price: number | null) => void;
  onCreateInsight: () => Promise<void> | void;
  busy: boolean;
  density: Density;
}) {
  const plan = actionPlanForPayload(payload);
  const rows = actionPlanRows(plan);
  const liquidationWarning = typeof plan?.liquidation?.warning === "string" ? plan.liquidation.warning : "";
  return (
    <section className="focusPanel actionPlanPanel">
      <div className="focusPanelHeader">
        <div>
          <h2>액션 플랜</h2>
          <p>지금 볼 가격과 발생 시 행동 · 행 클릭 시 차트 강조</p>
        </div>
        <span>{plan?.as_of ? `기준 ${new Date(plan.as_of).toLocaleString()}` : "데이터 부족"}</span>
      </div>
      {rows.length ? (
        <div className="actionPlanRows">
          {rows.map((row) => (
            <button
              className={`actionPlanRow tone-${row.tone} ${row.priceValue !== null && row.priceValue === highlightPrice ? "selected" : ""}`}
              disabled={row.priceValue === null}
              key={`${row.kind}-${row.price}-${row.condition}`}
              onClick={() => onSelectPrice(row.priceValue === highlightPrice ? null : row.priceValue)}
              type="button"
            >
              <span>{row.kind}</span>
              <strong>{row.price ?? plainifyTaText(row.condition)}</strong>
              <em>{plainifyTaText(row.action)}</em>
              <small>{formatBasis(row.basis, density)}</small>
            </button>
          ))}
        </div>
      ) : (
        <div className="terminalEmpty">액션 플랜을 만들 가격 근거가 부족합니다.</div>
      )}
      {liquidationWarning ? <div className="actionPlanWarning">{liquidationWarning}</div> : null}
      <details className="planInsightDetails">
        <summary>해설 보기 · {insightSummaryHint(payload)}</summary>
        <InsightEvidence payload={payload} onCreateInsight={onCreateInsight} busy={busy} />
      </details>
    </section>
  );
}

function EvidenceAccordion({
  payload,
  chartAnalysis,
  openModule,
  onModuleToggle,
  density,
  historyExtras
}: {
  payload: LivePositionPayload;
  chartAnalysis: PositionChartAnalysis | null;
  openModule: EvidenceModuleId | null;
  onModuleToggle: (id: EvidenceModuleId) => void;
  density: Density;
  historyExtras?: ReactNode;
}) {
  const wyckoff = payload.state.analysis.wyckoff;
  const direction = payload.position.direction;
  const wyckoffEvents = splitWyckoffEvents(chartAnalysis?.wyckoff_markers ?? [], chartAnalysis?.wyckoff_markers_low_confidence);
  const modules: Array<{ id: EvidenceModuleId; title: string; badge: string; content: ReactNode }> = [
    {
      id: "wyckoff",
      title: "와이코프",
      badge: density === "simple"
        ? phaseHintLabel(wyckoff.phase ?? wyckoff.phase_hint)
        : `${phaseHintLabel(wyckoff.phase ?? wyckoff.phase_hint)} · 유효 이벤트 ${wyckoffEvents.events.length}개`,
      content: <WyckoffEvidence state={payload.state} chartAnalysis={chartAnalysis} direction={direction} density={density} />
    },
    {
      id: "harmonic",
      title: "하모닉",
      badge: chartAnalysis?.harmonic_patterns.length
        ? density === "simple"
          ? `패턴 ${chartAnalysis.harmonic_patterns.length}개`
          : `패턴 ${chartAnalysis.harmonic_patterns.length}개 · 최고 ${confidenceLabel(Math.max(...chartAnalysis.harmonic_patterns.map((item) => item.confidence)), density)}`
        : "판정 보류",
      content: <HarmonicEvidence chartAnalysis={chartAnalysis} density={density} />
    },
    {
      id: "volume",
      title: "볼륨",
      badge: chartAnalysis ? volumeStateLabel(chartAnalysis.volume_xray.volume_state) : "차트 데이터 대기",
      content: chartAnalysis ? (
        <div className="evidenceVolumeGrid">
          <VolumeProfilePanel analysis={chartAnalysis} />
          <VolumeXrayPanel analysis={chartAnalysis} />
        </div>
      ) : (
        <div className="terminalEmpty">차트 데이터가 준비되면 표시됩니다.</div>
      )
    },
    {
      id: "indicators",
      title: "지표",
      badge: trendLabel(payload.state.analysis.technical.trend),
      content: <TechnicalEvidence state={payload.state} />
    },
    {
      id: "risk",
      title: "리스크",
      badge: `리스크 ${payload.state.risk_score}/100`,
      content: <RiskEvidence payload={payload} />
    },
    {
      id: "history",
      title: "기록",
      badge: payload.recent_events.length ? `이벤트 ${payload.recent_events.length}건` : "이벤트 없음",
      content: <TimelineEvidence payload={payload} extras={historyExtras} />
    }
  ];
  return (
    <section className="evidenceAccordion" aria-label="판단 근거">
      <div className="evidenceAccordionHeader">
        <strong>판단 근거</strong>
        <span>펼치면 차트가 해당 분석 레이어로 전환됩니다.</span>
      </div>
      {modules.map((module) => (
        <details className="evidenceSection" id={`evidence-${module.id}`} key={module.id} open={openModule === module.id}>
          <summary
            onClick={(event) => {
              event.preventDefault();
              onModuleToggle(module.id);
            }}
          >
            <strong>{module.title}</strong>
            <small>{module.badge}</small>
          </summary>
          <div className="evidenceBody">{openModule === module.id ? module.content : null}</div>
        </details>
      ))}
    </section>
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
      <strong>아직 해설이 없습니다.</strong>
      <span>판단과 액션 플랜은 현재 데이터로 이미 표시되어 있습니다. 배경 해설이 필요하면 생성하세요.</span>
      <button className="button" onClick={onCreateInsight} disabled={busy}>
        <BrainCircuit size={16} />
        {busy ? "생성 중" : "해설 생성"}
      </button>
    </div>
  );
}

function WyckoffEvidence({
  state,
  chartAnalysis,
  direction,
  density
}: {
  state: PositionState;
  chartAnalysis: PositionChartAnalysis | null;
  direction: "long" | "short";
  density: Density;
}) {
  const wyckoff = state.analysis.wyckoff;
  const mtf = wyckoff.mtf;
  const { events, lowConfidence } = splitWyckoffEvents(chartAnalysis?.wyckoff_markers ?? [], chartAnalysis?.wyckoff_markers_low_confidence);
  const visibleEvents = events.slice(-eventDisplayLimit(density));
  const phase = chartAnalysis?.wyckoff_phase?.phase ?? wyckoff.phase ?? "undetermined";
  const emptyMessage = phase === "trending"
    ? "추세 구간이라 레인지 기반 와이코프 판정을 보류 중입니다."
    : "레인지가 형성되지 않아 와이코프 판정을 보류 중입니다.";
  return (
    <div className="tabMetricLayout">
      <PositionHeaderMetric label="매집 점수" value={wyckoff.accumulation_score} tone="info" />
      <PositionHeaderMetric label="분산 점수" value={wyckoff.distribution_score} tone="warning" />
      <PositionHeaderMetric label="국면" value={phaseHintLabel(wyckoff.phase ?? wyckoff.phase_hint)} />
      <PositionHeaderMetric label="상위 정합" value={phaseHintLabel(mtf?.alignment)} tone={mtf?.alignment === "conflicting" ? "negative" : mtf?.alignment === "aligned" ? "positive" : "neutral"} />
      <PositionHeaderMetric label={taShortLabel("Spring")} title={taPlainTooltip("Spring", direction)} value={yesNoLabel(wyckoff.spring_candidate)} />
      <PositionHeaderMetric label={taShortLabel("SOS")} title={taPlainTooltip("SOS", direction)} value={yesNoLabel(wyckoff.sos_candidate)} />
      <PositionHeaderMetric label={taShortLabel("LPS")} title={taPlainTooltip("LPS", direction)} value={yesNoLabel(wyckoff.lps_candidate)} />
      <PositionHeaderMetric label={taShortLabel("UTAD")} title={taPlainTooltip("UTAD", direction)} value={yesNoLabel(Boolean(wyckoff.utad_candidate))} tone={wyckoff.utad_candidate ? "warning" : "neutral"} />
      <PositionHeaderMetric label={taShortLabel("SOW")} title={taPlainTooltip("SOW", direction)} value={yesNoLabel(Boolean(wyckoff.sow_candidate))} tone={wyckoff.sow_candidate ? "warning" : "neutral"} />
      <PositionHeaderMetric label={taShortLabel("LPSY")} title={taPlainTooltip("LPSY", direction)} value={yesNoLabel(Boolean(wyckoff.lpsy_candidate))} tone={wyckoff.lpsy_candidate ? "warning" : "neutral"} />
      <p className="tabExplanation">{plainifyTaText(wyckoff.structure_comment)}</p>
      {visibleEvents.length ? (
        <div className="wyckoffEventList">
          {visibleEvents.map((marker) => (
            <span key={`${marker.type}-${marker.time}`} title={taPlainTooltip(marker.label, direction)}>
              {taShortLabel(marker.label)} · {confidenceLabel(marker.confidence, density)}
            </span>
          ))}
        </div>
      ) : (
        <p className="tabExplanation">{emptyMessage}</p>
      )}
      {lowConfidence.length ? (
        <details className="lowConfidenceEvents">
          <summary>저신뢰 이벤트 {lowConfidence.length}개 보기</summary>
          <div className="wyckoffEventList">
            {lowConfidence.map((marker) => (
              <span key={`low-${marker.type}-${marker.time}`} title={taPlainTooltip(marker.label, direction)}>
                {taShortLabel(marker.label)} · {confidenceLabel(marker.confidence, density)}
              </span>
            ))}
          </div>
        </details>
      ) : null}
    </div>
  );
}

function HarmonicEvidence({ chartAnalysis, density }: { chartAnalysis: PositionChartAnalysis | null; density: Density }) {
  const patterns = chartAnalysis?.harmonic_patterns ?? [];
  if (!patterns.length) {
    return <div className="terminalEmpty">반전 패턴 조건을 충족한 구간이 없어 하모닉 판정을 보류 중입니다.</div>;
  }
  return (
    <div className="harmonicEvidenceList">
      {patterns.map((pattern) => (
        <div className="harmonicEvidenceItem" key={pattern.id}>
          <div>
            <strong title={taPlainTooltip(pattern.name ?? pattern.label)}>{taShortLabel(pattern.name ?? pattern.label)}</strong>
            <span>{pattern.direction === "bearish" ? "하락 반전 후보" : "상승 반전 후보"} · {pattern.status === "forming" ? "형성 중" : "완성"} · {confidenceLabel(pattern.confidence, density)}</span>
          </div>
          <em title={taPlainTooltip("PRZ")}>반전 후보 구간(PRZ) {formatPrice(pattern.prz.low)} ~ {formatPrice(pattern.prz.high)}</em>
          <p>{plainifyTaText(pattern.basis)}</p>
        </div>
      ))}
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
          <PositionHeaderMetric label="청산가 거리" value={formatDistance(state.liquidation_distance_pct)} tone={state.liquidation_distance_pct !== null && state.liquidation_distance_pct < 5 ? "negative" : state.liquidation_distance_pct !== null && state.liquidation_distance_pct < 10 ? "warning" : "neutral"} />
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

function TimelineEvidence({ payload, extras }: { payload: LivePositionPayload; extras?: ReactNode }) {
  return (
    <div className="timelineTab">
      <div className="snapshotSummary">
        <PositionHeaderMetric label="마지막 스냅샷" value={new Date(payload.latest_snapshot.created_at).toLocaleString()} />
      </div>
      <EventList events={payload.recent_events} />
      {extras}
    </div>
  );
}

function PositionHeaderMetric({
  label,
  value,
  tone = "neutral",
  title
}: {
  label: string;
  value: string | number;
  tone?: MetricTone;
  title?: string;
}) {
  return (
    <div className={`positionHeaderMetric tone-${tone}`} title={title}>
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
  const [busy, setBusy] = useState("");
  const [notice, setNotice] = useState("");
  const [error, setError] = useState("");
  const workspace = usePositionWorkspace();

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

  const recordForms = (
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
  );

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
        </div>
      </header>

      {error ? <TerminalWarning tone="error">{error}</TerminalWarning> : null}
      {notice ? <TerminalWarning tone="info">{notice}</TerminalWarning> : null}

      <PositionVerdictBar payload={detail} onRefresh={() => void analyze()} refreshing={busy === "analyze"} />

      <section className="positionDetailMain">
        <PositionChart
          analysis={chartAnalysis}
          loading={chartLoading}
          error={chartError}
          onRetry={() => void loadChart(timeframe)}
          trendSummary={trendLabel(detail.state.analysis.technical.trend)}
          plan={actionPlanForPayload(detail)}
          layers={workspace.layers}
          onToggleLayer={workspace.handleToggleLayer}
          highlightPrice={workspace.highlightPrice}
        />
        <ActionPlanPanel
          payload={detail}
          highlightPrice={workspace.highlightPrice}
          onSelectPrice={workspace.setHighlightPrice}
          onCreateInsight={() => createInsight()}
          busy={busy === "insight"}
          density={workspace.density}
        />
      </section>

      <EvidenceAccordion
        payload={detail}
        chartAnalysis={chartAnalysis}
        openModule={workspace.openModule}
        onModuleToggle={workspace.handleModuleToggle}
        density={workspace.density}
        historyExtras={recordForms}
      />
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

function actionPlanForPayload(payload: LivePositionPayload): PositionActionPlan | null {
  return payload.action_plan ?? payload.latest_insight?.action_plan ?? null;
}

function headlineForPayload(payload: LivePositionPayload): string {
  const plan = actionPlanForPayload(payload);
  return (
    plan?.headline_action ??
    deriveHeadlineAction(plan, payload.position.direction) ??
    "지금 볼 것: 액션 플랜 근거 부족. 갱신 후 다시 확인."
  );
}

/** 백엔드 headline_action과 동일 규칙의 클라이언트 폴백: 현재가에서 가장 가까운 트리거 1개. */
function deriveHeadlineAction(plan: PositionActionPlan | null, direction: "long" | "short"): string | null {
  if (!plan) return null;
  const candidates: Array<{ distance: number; kind: "invalidation" | "take_profit"; price: number | null; action: string }> = [];
  if (plan.invalidation && typeof plan.invalidation.distance_pct === "number") {
    candidates.push({ distance: Math.abs(plan.invalidation.distance_pct), kind: "invalidation", price: plan.invalidation.price, action: plan.invalidation.action ?? "조건 확인" });
  }
  for (const target of plan.take_profit ?? []) {
    if (typeof target.distance_pct === "number") {
      candidates.push({ distance: Math.abs(target.distance_pct), kind: "take_profit", price: target.price, action: target.action ?? "부분 익절 검토" });
    }
  }
  if (candidates.length) {
    const nearest = candidates.reduce((left, right) => (right.distance < left.distance ? right : left));
    const price = nearest.price === null ? "-" : formatPrice(nearest.price);
    if (nearest.kind === "invalidation") {
      return `지금 볼 것: ${price} ${direction === "long" ? "지지 유지 여부" : "저항 유지 여부"}. ${nearest.action}.`;
    }
    return `지금 볼 것: ${price} ${direction === "long" ? "저항 반응" : "지지 반응"}. 도달 시 ${nearest.action}.`;
  }
  const trigger = plan.watch_triggers?.[0];
  if (trigger) return `지금 볼 것: ${trigger.condition}. ${trigger.meaning}.`;
  return null;
}

function actionPlanRows(plan: PositionActionPlan | null) {
  if (!plan) return [];
  const rows: Array<{ kind: string; price?: string; condition?: string; action: string; basis: string; tone: "danger" | "positive" | "warning" | "neutral"; priceValue: number | null }> = [];
  if (plan.invalidation) {
    rows.push({
      kind: "무효화",
      price: `${formatNullablePrice(plan.invalidation.price)} · ${formatDistance(plan.invalidation.distance_pct)}`,
      action: plan.invalidation.action ?? "조건 확인",
      basis: plan.invalidation.basis ?? "무효화 기준",
      tone: "danger",
      priceValue: plan.invalidation.price
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
      tone: "positive",
      priceValue: target.price
    });
  }
  for (const trigger of watchTriggers.slice(0, 3)) {
    rows.push({
      kind: "감시",
      condition: trigger.condition ?? "조건 확인",
      action: "조건 확인",
      basis: trigger.meaning ?? "추가 확인 필요",
      tone: "warning",
      priceValue: null
    });
  }
  if (typeof plan.liquidation?.price === "number") {
    rows.push({
      kind: "청산가",
      price: formatNullablePrice(plan.liquidation.price),
      action: "거리 확인",
      basis: "거래소 수신 청산가",
      tone: "neutral",
      priceValue: plan.liquidation.price
    });
  }
  return rows.slice(0, 6);
}

/** 간단 모드에서는 점수 수치를 숨기고 문장 라벨만 남긴다. */
function formatBasis(basis: string, density: Density): string {
  const plain = plainifyTaText(basis);
  if (density === "detailed") return plain;
  return plain.replace(/\s*·\s*점수\s*\d+/g, "");
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
