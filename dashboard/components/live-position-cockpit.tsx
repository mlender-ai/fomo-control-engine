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
import { FormEvent, useEffect, useState } from "react";
import { TerminalPanel, TerminalWarning } from "@/components/terminal";
import {
  SymbolAnalysisView,
  useAnalysisWorkspace,
  type MetricTone
} from "@/components/symbol-analysis-view";
import {
  api,
  type BitgetConnectionTest,
  type LivePositionDetail,
  type LivePositionPayload,
  type LivePositionsResponse,
  type PositionActionPlan,
  type PositionChartAnalysis,
  type ScenarioMatchResponse
} from "@/lib/api";
import { type Density } from "@/lib/density";
import { formatPrice, signedPercent } from "@/lib/format";
import { plainifyTaText } from "@/lib/labels/taGlossary";
import { connectionStatusLabel, directionLabel, localizeMarketCodes, trendLabel } from "@/lib/labels/marketStateLabels";

const LIVE_POSITION_SYNC_INTERVAL_SECONDS = 30;

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
  const [stripChartAnalysis, setStripChartAnalysis] = useState<Record<string, PositionChartAnalysis>>({});
  const workspace = useAnalysisWorkspace();

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
    void load(false);
    const interval = window.setInterval(() => {
      void load(false);
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
  const stripChartKey = positions.map((item) => item.position.id).join("|");

  async function loadSelectedDetail(positionId: string) {
    try {
      setSelectedDetail(await api.livePosition(positionId));
    } catch {
      setSelectedDetail(null);
    }
  }

  async function loadSelectedChart(positionId: string, showSpinner = true) {
    if (showSpinner) setSelectedChartLoading(true);
    setSelectedChartError("");
    try {
      setSelectedChartAnalysis(await api.positionChartAnalysis(positionId, "4h"));
    } catch (err) {
      if (showSpinner) setSelectedChartAnalysis(null);
      setSelectedChartError(err instanceof Error ? err.message : "차트 분석 데이터를 불러오지 못했습니다.");
    } finally {
      if (showSpinner) setSelectedChartLoading(false);
    }
  }

  useEffect(() => {
    if (!selected?.position.id) return;
    void loadSelectedDetail(selected.position.id);
    const hasCurrentChart = selectedChartAnalysis?.position_id === selected.position.id;
    void loadSelectedChart(selected.position.id, !hasCurrentChart);
  }, [selected?.position.id, data?.timestamp]);

  useEffect(() => {
    if (!positions.length) {
      setStripChartAnalysis({});
      return;
    }
    let cancelled = false;
    const ids = positions.slice(0, 10).map((item) => item.position.id);
    async function loadStripCharts() {
      const results = await Promise.allSettled(
        ids.map(async (positionId) => [positionId, await api.positionChartAnalysis(positionId, "4h")] as const)
      );
      if (cancelled) return;
      setStripChartAnalysis((current) => {
        const next: Record<string, PositionChartAnalysis> = {};
        for (const id of ids) {
          if (current[id]) next[id] = current[id];
        }
        results.forEach((result) => {
          if (result.status === "fulfilled") {
            const [positionId, analysis] = result.value;
            next[positionId] = analysis;
          }
        });
        return next;
      });
    }
    void loadStripCharts();
    return () => {
      cancelled = true;
    };
  }, [stripChartKey, data?.timestamp]);

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
          <PositionStrip
            chartAnalysisById={stripChartAnalysis}
            positions={positions}
            selectedId={selected?.position.id ?? ""}
            onSelect={setSelectedId}
          />
          {selectedPayload ? (
            <>
              <PositionVerdictBar
                payload={selectedPayload}
                onRefresh={() => void refreshSelected(selectedPayload.position.id)}
                refreshing={actionLoading === `refresh:${selectedPayload.position.id}`}
              />
              <SymbolAnalysisView
                chartAnalysis={selectedChartAnalysis}
                chartLoading={selectedChartLoading}
                chartError={selectedChartError}
                onRetryChart={() => void loadSelectedChart(selectedPayload.position.id)}
                trendSummary={trendLabel(selectedPayload.state.analysis.technical.trend)}
                plan={actionPlanForPayload(selectedPayload)}
                payload={selectedPayload}
                analystBriefing={selectedPayload.analyst_briefing ?? null}
                workspace={workspace}
                gridClassName="cockpitMainGrid"
                sidePanel={
                  <ActionPlanPanel
                    payload={selectedPayload}
                    highlightPrice={workspace.highlightPrice}
                    onSelectPrice={workspace.setHighlightPrice}
                    onCreateInsight={() => createInsight(selectedPayload.position.id)}
                    busy={actionLoading === `insight:${selectedPayload.position.id}`}
                    density={workspace.density}
                  />
                }
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
  chartAnalysisById,
  positions,
  selectedId,
  onSelect
}: {
  chartAnalysisById: Record<string, PositionChartAnalysis>;
  positions: LivePositionPayload[];
  selectedId: string;
  onSelect: (positionId: string) => void;
}) {
  const sortedPositions = [...positions].sort((left, right) => right.state.severity_rank - left.state.severity_rank);
  return (
    <section className="positionStrip" aria-label="보유 포지션" data-testid="position-strip">
      {sortedPositions.map((item) => {
        const trigger = nearestActionTrigger(item);
        return (
          <button
            className={`positionStripCard severity-${item.state.severity_rank} ${item.position.id === selectedId ? "selected" : ""}`}
            data-testid="position-card"
            key={item.position.id}
            onClick={() => onSelect(item.position.id)}
            title={riskRewardSummary(item)}
            type="button"
          >
            <span className="stripSeverityBar" aria-hidden="true" />
            <div className="stripCardTop">
              <div className="stripIdentity">
                <strong>
                  {item.position.symbol}
                  {liquidationMissing(item) ? <AlertTriangle className="liqMissingIcon" size={12} aria-label="청산가 미수신" /> : null}
                </strong>
                <span>{directionLabel(item.position.direction)} · {item.position.leverage}x</span>
              </div>
              <HealthGaugeRing score={item.state.health_score} severity={item.state.severity_rank} />
            </div>
            <PositionMiniSparkline payload={item} analysis={chartAnalysisById[item.position.id]} />
            <div className="stripCardMetrics">
              <em className={`pnlFlash ${item.state.pnl_percent >= 0 ? "successText pnlFlashUp" : "dangerText pnlFlashDown"}`}>
                {signedPercent(item.state.pnl_percent)}
              </em>
              <StatusPill status={item.state.status} label={item.state.status_label} />
            </div>
            <span className="stripHeadline">
              {plainifyTaText(headlineForPayload(item))}
              {trigger ? <b>{formatDistance(trigger.distance_pct)}</b> : null}
            </span>
          </button>
        );
      })}
    </section>
  );
}

function HealthGaugeRing({ score, severity }: { score: number; severity: number }) {
  const radius = 15;
  const circumference = 2 * Math.PI * radius;
  const progress = clamp(score, 0, 100);
  return (
    <span className={`healthGaugeRing severity-${severity}`} aria-label={`건강도 ${score}`} data-testid="health-gauge">
      <svg viewBox="0 0 40 40" aria-hidden="true">
        <circle cx="20" cy="20" r={radius} className="healthGaugeTrack" />
        <circle
          cx="20"
          cy="20"
          r={radius}
          className="healthGaugeValue"
          strokeDasharray={circumference}
          strokeDashoffset={circumference * (1 - progress / 100)}
        />
      </svg>
      <strong>{Math.round(score)}</strong>
    </span>
  );
}

function PositionMiniSparkline({
  payload,
  analysis
}: {
  payload: LivePositionPayload;
  analysis?: PositionChartAnalysis;
}) {
  const candles = analysis?.candles.slice(-48) ?? [];
  const closes = candles.map((candle) => candle.close);
  const fallbackMark = markPriceForPayload(payload);
  const values = closes.length >= 2 ? closes : [payload.position.entry_price, fallbackMark ?? payload.position.entry_price];
  const plan = actionPlanForPayload(payload);
  const invalidation = numericPlanPrice(plan?.invalidation) ?? numericPlanPrice(plan?.engine_invalidation);
  const takeProfit = numericPlanPrice(plan?.take_profit?.[0]);
  const domainValues = values
    .concat([payload.position.entry_price])
    .concat(invalidation === null ? [] : [invalidation])
    .concat(takeProfit === null ? [] : [takeProfit]);
  const min = Math.min(...domainValues);
  const max = Math.max(...domainValues);
  const width = 154;
  const height = 44;
  const y = (value: number) => height - 8 - ((value - min) / Math.max(max - min, 1e-12)) * (height - 16);
  const x = (index: number) => (values.length <= 1 ? 0 : (index / (values.length - 1)) * width);
  const path = values.map((value, index) => `${index === 0 ? "M" : "L"} ${x(index).toFixed(1)} ${y(value).toFixed(1)}`).join(" ");
  const entryY = y(payload.position.entry_price);
  return (
    <svg className="positionSparkline" data-testid="position-sparkline" viewBox={`0 0 ${width} ${height}`} role="img" aria-label="최근 48캔들 위치 요약">
      <line className="sparkEntry" x1="0" x2={width} y1={entryY} y2={entryY} />
      {invalidation !== null ? <line className="sparkTick sparkInvalidation" x1={width - 18} x2={width} y1={y(invalidation)} y2={y(invalidation)} /> : null}
      {takeProfit !== null ? <line className="sparkTick sparkTakeProfit" x1={width - 18} x2={width} y1={y(takeProfit)} y2={y(takeProfit)} /> : null}
      <path className={payload.state.pnl_percent >= 0 ? "sparkLine positive" : "sparkLine negative"} d={path} />
    </svg>
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
  const freshness = freshnessCountdownLabel(ageMinutes);
  const statusIcon = state.severity_rank >= 3
    ? <AlertTriangle size={26} aria-hidden="true" />
    : state.severity_rank >= 1
      ? <Activity size={26} aria-hidden="true" />
      : <ShieldCheck size={26} aria-hidden="true" />;
  return (
    <section className={`verdictBar status-${state.status} severity-${state.severity_rank}`} data-testid="verdict-bar">
      <div className="verdictHero">
        <div className="verdictStatusIcon">
          {statusIcon}
        </div>
        <div className="verdictHeroMain">
          <div className="verdictTopRow">
            <strong className="verdictSymbol">{position.symbol} {directionLabel(position.direction)} {position.leverage}x</strong>
            <em
              className={`verdictPnl pnlFlash ${state.pnl_percent >= 0 ? "successText pnlFlashUp" : "dangerText pnlFlashDown"}`}
              title={`손익률 출처: ${pnlSourceLabel(state.pnl_source)}`}
            >
              {signedPercent(state.pnl_percent)}
              {state.pnl_source === "exchange" ? <Landmark size={12} /> : <Calculator size={12} />}
            </em>
            <StatusPill status={state.status} label={`${state.status_label} (${state.health_score}/100)`} />
          </div>
          <p className="verdictAction">→ {plainifyTaText(headlineForPayload(payload))}</p>
        </div>
      </div>
      <TriggerProximityMeter payload={payload} />
      <div className="verdictMeta">
        <span className={ageMinutes !== null && ageMinutes > 30 ? "freshnessBadge stale" : "freshnessBadge"}>
          {asOf ? `기준 ${asOf.toLocaleTimeString("ko-KR", { hour: "2-digit", minute: "2-digit" })}` : "기준 -"} · {freshness}
        </span>
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

function TriggerProximityMeter({ payload }: { payload: LivePositionPayload }) {
  const plan = actionPlanForPayload(payload);
  const invalidationPrice = numericPlanPrice(plan?.invalidation) ?? numericPlanPrice(plan?.engine_invalidation);
  const takeProfitPrice = numericPlanPrice(plan?.take_profit?.[0]);
  const currentPrice = markPriceForPayload(payload);
  if (invalidationPrice === null || takeProfitPrice === null || currentPrice === null || invalidationPrice === takeProfitPrice) return null;
  const min = Math.min(invalidationPrice, takeProfitPrice);
  const max = Math.max(invalidationPrice, takeProfitPrice);
  const currentLeft = clamp(((currentPrice - min) / Math.max(max - min, 1e-12)) * 100, 0, 100);
  const invalidationLeft = clamp(((invalidationPrice - min) / Math.max(max - min, 1e-12)) * 100, 0, 100);
  const targetLeft = clamp(((takeProfitPrice - min) / Math.max(max - min, 1e-12)) * 100, 0, 100);
  return (
    <div className="triggerMeter" aria-label="무효화와 1차 익절 사이 현재가 위치" data-testid="trigger-meter">
      <div className="triggerMeterTrack">
        <span className="triggerMeterRisk" style={{ left: `${Math.min(invalidationLeft, currentLeft)}%`, width: `${Math.abs(currentLeft - invalidationLeft)}%` }} />
        <span className="triggerMeterReward" style={{ left: `${Math.min(targetLeft, currentLeft)}%`, width: `${Math.abs(targetLeft - currentLeft)}%` }} />
        <i className="triggerMeterMarker" style={{ left: `${currentLeft}%` }} />
      </div>
      <div className="triggerMeterLabels">
        <span>무효화 {formatPrice(invalidationPrice)}</span>
        <strong>현재 {formatPrice(currentPrice)}</strong>
        <span>익절1 {formatPrice(takeProfitPrice)}</span>
      </div>
    </div>
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
    <section className="focusPanel actionPlanPanel" data-testid="action-plan">
      <div className="focusPanelHeader">
        <div>
          <h2>액션 플랜</h2>
          <p>지금 볼 가격과 발생 시 행동 · 행 클릭 시 차트 강조</p>
        </div>
        <span>{plan?.as_of ? `기준 ${new Date(plan.as_of).toLocaleString()}` : "데이터 부족"}</span>
      </div>
      {payload.latest_insight && !payload.insight_status.is_stale ? (
        <div className="insightSuccessToast">
          인사이트 최신 · {new Date(payload.latest_insight.created_at).toLocaleTimeString("ko-KR", { hour: "2-digit", minute: "2-digit" })}
        </div>
      ) : null}
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
      <DerivativeEvidenceCards payload={payload} />
      <details className="planInsightDetails">
        <summary>해설 보기 · {insightSummaryHint(payload)}</summary>
        <InsightEvidence payload={payload} onCreateInsight={onCreateInsight} busy={busy} />
      </details>
    </section>
  );
}

function DerivativeEvidenceCards({ payload }: { payload: LivePositionPayload }) {
  const derivatives = payload.state.analysis.derivatives;
  const latest = derivatives?.latest;
  const signals = derivatives?.signals;
  if (!latest) return null;
  const coinglass = derivatives?.coinglass;
  const sourceLabel = coinglass?.source_status === "ok" ? `${latest.provider ?? "bitget"} + Coinglass` : latest.provider ?? "bitget";
  return (
    <div className="derivativeEvidenceGrid">
      <div className="derivativeEvidenceCard">
        <span>수급 기준</span>
        <strong>{derivatives?.as_of ? new Date(derivatives.as_of).toLocaleTimeString("ko-KR", { hour: "2-digit", minute: "2-digit" }) : "-"}</strong>
        <em>출처 {sourceLabel}</em>
      </div>
      <div className="derivativeEvidenceCard">
        <span>OI 24h</span>
        <strong>{formatNullablePct(latest.open_interest_change_pct)}</strong>
        <em>{signals?.oi_price_divergence?.label ?? "표본 부족"}</em>
      </div>
      <div className="derivativeEvidenceCard">
        <span>펀딩</span>
        <strong>{signals?.funding_state?.label ?? "표본 부족"}</strong>
        <em>{formatFunding(latest.funding_rate)}</em>
      </div>
      <div className={`derivativeEvidenceCard ${coinglass?.source_status === "locked" ? "locked" : ""}`}>
        <span>청산 밀집대</span>
        <strong>{coinglass?.source_status === "ok" ? `${signals?.liquidation_clusters?.length ?? 0}개 추정` : "Coinglass 연결 필요"}</strong>
        <em>추정 모델 · 확정 아님</em>
      </div>
    </div>
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
  const [scenarioMatch, setScenarioMatch] = useState<ScenarioMatchResponse | null>(null);
  const workspace = useAnalysisWorkspace();

  async function load() {
    setError("");
    try {
      const next = await api.livePosition(positionId);
      setDetail(next);
      void loadScenarioMatch(next);
    } catch (err) {
      setError(err instanceof Error ? err.message : "포지션 상세 데이터를 불러오지 못했습니다.");
    } finally {
      setLoading(false);
    }
  }

  async function loadScenarioMatch(next: LivePositionDetail) {
    if (next.position.status !== "open" || next.position.scenario_id) {
      setScenarioMatch(null);
      return;
    }
    try {
      const match = await api.matchScenario(next.position.id);
      setScenarioMatch(match.suggestion ? match : null);
    } catch {
      setScenarioMatch(null);
    }
  }

  async function applyScenarioLink() {
    if (!scenarioMatch?.scenario) return;
    setBusy("link");
    setError("");
    try {
      await api.linkScenario(scenarioMatch.scenario.id, { position_id: positionId, apply_prefill: true });
      setScenarioMatch(null);
      await load();
      setNotice("저장된 진입 시나리오를 연결하고 메모·계획 가격을 프리필했습니다.");
    } catch (err) {
      setError(err instanceof Error ? err.message : "시나리오 연결에 실패했습니다.");
    } finally {
      setBusy("");
    }
  }

  async function loadChart(nextTimeframe = timeframe, showSpinner = true) {
    setChartError("");
    if (showSpinner) setChartLoading(true);
    try {
      setChartAnalysis(await api.positionChartAnalysis(positionId, nextTimeframe));
    } catch (err) {
      if (showSpinner) setChartAnalysis(null);
      setChartError(err instanceof Error ? err.message : "차트 분석 데이터를 불러오지 못했습니다.");
    } finally {
      if (showSpinner) setChartLoading(false);
    }
  }

  useEffect(() => {
    void load();
  }, [positionId]);

  useEffect(() => {
    const hasCurrentChart = chartAnalysis?.position_id === positionId && chartAnalysis.timeframe === timeframe;
    void loadChart(timeframe, !hasCurrentChart);
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

      {scenarioMatch?.scenario && scenarioMatch.suggestion ? (
        <ScenarioLinkBanner match={scenarioMatch} onApply={() => void applyScenarioLink()} onDismiss={() => setScenarioMatch(null)} busy={busy === "link"} />
      ) : null}

      <PositionVerdictBar payload={detail} onRefresh={() => void analyze()} refreshing={busy === "analyze"} />

      <SymbolAnalysisView
        chartAnalysis={chartAnalysis}
        chartLoading={chartLoading}
        chartError={chartError}
        onRetryChart={() => void loadChart(timeframe)}
        trendSummary={trendLabel(detail.state.analysis.technical.trend)}
        plan={actionPlanForPayload(detail)}
        payload={detail}
        analystBriefing={detail.analyst_briefing ?? null}
        workspace={workspace}
        gridClassName="positionDetailMain"
        historyExtras={recordForms}
        sidePanel={
          <ActionPlanPanel
            payload={detail}
            highlightPrice={workspace.highlightPrice}
            onSelectPrice={workspace.setHighlightPrice}
            onCreateInsight={() => createInsight()}
            busy={busy === "insight"}
            density={workspace.density}
          />
        }
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

function ScenarioLinkBanner({
  match,
  onApply,
  onDismiss,
  busy
}: {
  match: ScenarioMatchResponse;
  onApply: () => void;
  onDismiss: () => void;
  busy: boolean;
}) {
  const scenario = match.scenario!;
  const suggestion = match.suggestion!;
  const created = new Date(scenario.created_at).toLocaleString();
  return (
    <section className="scenarioLinkBanner">
      <div className="scenarioLinkBody">
        <strong>저장된 진입 시나리오와 연결할까요?</strong>
        <p>
          {created}에 저장한 {scenario.direction === "long" ? "롱" : "숏"} {scenario.leverage}x 시나리오(계획 진입 {formatPrice(scenario.entry_price)})와 일치합니다.
          {suggestion.slippage_flag ? (
            <span className="scenarioSlippageFlag"> 계획과 다른 가격에 진입: 슬리피지 {suggestion.slippage_pct === null ? "-" : signedPercent(suggestion.slippage_pct)}</span>
          ) : suggestion.slippage_pct !== null ? (
            <span> 슬리피지 {signedPercent(suggestion.slippage_pct)}</span>
          ) : null}
        </p>
        <small>연결 시 메모·핵심 가설·손절/익절 계획을 프리필하고, 진입 전 판단을 복기 원장에 기록합니다.</small>
      </div>
      <div className="scenarioLinkActions">
        <button className="button" onClick={onApply} disabled={busy} type="button">{busy ? "연결 중" : "연결하고 프리필"}</button>
        <button className="button secondary" onClick={onDismiss} disabled={busy} type="button">나중에</button>
      </div>
    </section>
  );
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

function nearestActionTrigger(payload: LivePositionPayload): { distance_pct: number | null } | null {
  const plan = actionPlanForPayload(payload);
  if (!plan) return null;
  const current = markPriceForPayload(payload);
  const candidates = [plan.invalidation, ...(plan.take_profit ?? [])]
    .map((item) => {
      if (!item) return null;
      const derivedDistance =
        typeof item.distance_pct === "number"
          ? item.distance_pct
          : typeof item.price === "number" && current !== null
            ? distancePctFromCurrent(item.price, current)
            : null;
      return derivedDistance === null ? null : { distance_pct: derivedDistance };
    })
    .filter((item): item is { distance_pct: number } => item !== null);
  if (!candidates.length) return null;
  return candidates.reduce((left, right) => (Math.abs(right.distance_pct) < Math.abs(left.distance_pct) ? right : left));
}

function riskRewardSummary(payload: LivePositionPayload): string {
  const plan = actionPlanForPayload(payload);
  const invalidation = plan?.invalidation ?? plan?.engine_invalidation ?? null;
  const target = plan?.take_profit?.[0] ?? null;
  const current = markPriceForPayload(payload);
  const risk = signedDistanceFromPlanItem(invalidation, current);
  const reward = signedDistanceFromPlanItem(target, current);
  const rr = typeof risk === "number" && typeof reward === "number" && Math.abs(risk) > 0 ? Math.abs(reward / risk) : null;
  if (rr !== null) return `R:R ${rr.toFixed(1)} · 익절 ${formatDistance(reward)} / 무효화 ${formatDistance(risk)}`;
  return "액션 플랜의 익절·무효화 가격을 확인하세요.";
}

function signedDistanceFromPlanItem(item: { price: number | null; distance_pct: number | null } | null, current: number | null): number | null {
  if (!item) return null;
  if (typeof item.distance_pct === "number") return item.distance_pct;
  if (typeof item.price === "number" && current !== null) return distancePctFromCurrent(item.price, current);
  return null;
}

function distancePctFromCurrent(price: number, current: number): number {
  if (!Number.isFinite(price) || !Number.isFinite(current) || current === 0) return 0;
  return ((price - current) / current) * 100;
}

function numericPlanPrice(item: { price: number | null } | null | undefined): number | null {
  return typeof item?.price === "number" && Number.isFinite(item.price) ? item.price : null;
}

function markPriceForPayload(payload: LivePositionPayload): number | null {
  return payload.state.mark_price ?? payload.position.mark_price ?? payload.position.current_price ?? null;
}

function freshnessCountdownLabel(ageMinutes: number | null): string {
  if (ageMinutes === null || !Number.isFinite(ageMinutes)) return "기준 데이터 없음";
  const remaining = Math.ceil(30 - ageMinutes);
  if (remaining > 0) return `유효 ${remaining}분 남음`;
  return `만료 ${Math.abs(remaining)}분 지남 · 갱신 권장`;
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

function formatNullablePct(value: number | null | undefined): string {
  if (typeof value !== "number" || !Number.isFinite(value)) return "표본 부족";
  return signedPercent(value);
}

function formatFunding(value: number | null | undefined): string {
  if (typeof value !== "number" || !Number.isFinite(value)) return "-";
  return `${value > 0 ? "+" : ""}${(value * 100).toFixed(4)}%`;
}

function formatDistance(value: number | null): string {
  return value === null ? "-" : signedPercent(value);
}

function clamp(value: number, min: number, max: number): number {
  return Math.min(max, Math.max(min, value));
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
