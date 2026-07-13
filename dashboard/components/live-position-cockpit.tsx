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
import { FormEvent, useEffect, useRef, useState } from "react";
import { CompactChartWorkspace, MoneyFlowCard, type CompactNextPrice } from "@/components/position/CompactChartWorkspace";
import { MinimalAssetCard } from "@/components/position/MinimalAssetCard";
import { TerminalPanel, TerminalWarning } from "@/components/terminal";
import {
  chartOverlayFromPayload,
  SymbolAnalysisView,
  useAnalysisWorkspace,
  type MetricTone
} from "@/components/symbol-analysis-view";
import {
  api,
  type AnalystEvidence,
  type BitgetConnectionTest,
  type LivePositionDetail,
  type LivePositionPayload,
  type LivePositionsResponse,
  type OneLinerLine,
  type OneLinerStance,
  type OneLinerSummary,
  type PositionActionPlan,
  type PositionChartAnalysis,
  type ScenarioMatchResponse
} from "@/lib/api";
import { type MinimalEvidenceLayer } from "@/lib/chartLayers";
import { type Density } from "@/lib/density";
import { formatPrice, signedPercent } from "@/lib/format";
import { plainifyTaText } from "@/lib/labels/taGlossary";
import { connectionStatusLabel, directionLabel, localizeMarketCodes, trendLabel } from "@/lib/labels/marketStateLabels";
import { loadFceViewMode, saveFceViewMode, type FceViewMode } from "@/lib/viewMode";
import { useSecondaryTaRows, visibleTaRows } from "@/lib/taDisplayPreferences";

const LIVE_POSITION_SYNC_INTERVAL_SECONDS = 30;

type MinimalEvidenceChoice = {
  key: string;
  text: string;
  layer: MinimalEvidenceLayer;
  label: string;
  price?: number | null;
  time?: number | null;
};

export function LivePositionCockpit() {
  const [data, setData] = useState<LivePositionsResponse | null>(null);
  const [selectedId, setSelectedId] = useState("");
  const [connectionTest, setConnectionTest] = useState<BitgetConnectionTest | null>(null);
  const [loading, setLoading] = useState(true);
  const [actionLoading, setActionLoading] = useState("");
  const [notice, setNotice] = useState("");
  const [error, setError] = useState("");
  const [selectedChartAnalysis, setSelectedChartAnalysis] = useState<PositionChartAnalysis | null>(null);
  const [selectedDetail, setSelectedDetail] = useState<LivePositionDetail | null>(null);
  const [selectedChartLoading, setSelectedChartLoading] = useState(false);
  const [selectedChartError, setSelectedChartError] = useState("");
  const [stripChartAnalysis, setStripChartAnalysis] = useState<Record<string, PositionChartAnalysis>>({});
  const [viewMode, setViewMode] = useState<FceViewMode>("minimal");
  const selectedChartRequestRef = useRef(0);
  const selectedDetailRequestRef = useRef(0);
  const hasPositionDataRef = useRef(false);
  const workspace = useAnalysisWorkspace();

  useEffect(() => {
    setViewMode(new URLSearchParams(window.location.search).get("mode") === "pro" ? "pro" : loadFceViewMode());
  }, []);

  function updateViewMode(mode: FceViewMode) {
    setViewMode(mode);
    saveFceViewMode(mode);
    const url = new URL(window.location.href);
    url.searchParams.set("mode", mode);
    if (mode === "minimal") {
      url.searchParams.delete("focus");
      url.searchParams.delete("price");
    }
    window.history.replaceState(window.history.state, "", url);
  }

  async function load(sync = false): Promise<boolean> {
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
      hasPositionDataRef.current = true;
      setError("");
      setSelectedId((current) => current || normalized.positions[0]?.position.id || "");
      return true;
    } catch (err) {
      if (sync || !hasPositionDataRef.current) {
        setError(err instanceof Error ? err.message : "라이브 포지션 데이터를 불러오지 못했습니다.");
      }
      return false;
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

  useEffect(() => {
    const refresh = () => void load(false);
    window.addEventListener("fce:refresh", refresh);
    return () => window.removeEventListener("fce:refresh", refresh);
  }, []);

  async function syncPositions() {
    setActionLoading("sync");
    setNotice("");
    const succeeded = await load(true);
    if (succeeded) {
      setNotice("Bitget read-only 포지션 동기화와 상태 분석을 갱신했습니다.");
    }
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
      setSelectedDetail((current) =>
        current?.position.id === positionId ? { ...current, ...result } : current
      );
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
      const requests: Array<Promise<unknown>> = [loadSelectedChart(positionId, false), load(false)];
      requests.push(
        api.livePosition(positionId).then((detail) => {
          setSelectedDetail(detail);
        })
      );
      await Promise.all(requests);
      setNotice("포지션 상태와 액션 플랜을 갱신했습니다.");
    } catch (err) {
      setError(err instanceof Error ? err.message : "상태 갱신에 실패했습니다.");
    } finally {
      setActionLoading("");
    }
  }

  const positions = data?.positions ?? [];
  const selected = positions.find((item) => item.position.id === selectedId) ?? positions[0];
  const selectedPayload = selectedDetail?.position.id === selected?.position.id ? selectedDetail : selected;
  const selectedChartForPayload = selectedChartAnalysis?.position_id === selectedPayload?.position.id ? selectedChartAnalysis : null;
  const stripChartKey = positions.map((item) => item.position.id).join("|");

  async function loadSelectedChart(positionId: string, showSpinner = true, compact = viewMode === "minimal") {
    const requestId = selectedChartRequestRef.current + 1;
    selectedChartRequestRef.current = requestId;
    if (showSpinner) setSelectedChartLoading(true);
    setSelectedChartError("");
    try {
      const analysis = await api.positionChartAnalysis(positionId, "4h", compact);
      if (selectedChartRequestRef.current !== requestId) return;
      setSelectedChartAnalysis(analysis);
    } catch (err) {
      if (selectedChartRequestRef.current !== requestId) return;
      if (showSpinner) setSelectedChartAnalysis(null);
      setSelectedChartError(err instanceof Error ? err.message : "차트 분석 데이터를 불러오지 못했습니다.");
    } finally {
      if (selectedChartRequestRef.current === requestId && showSpinner) setSelectedChartLoading(false);
    }
  }

  useEffect(() => {
    if (!selected?.position.id) return;
    const positionId = selected.position.id;
    if (selectedDetail?.position.id === positionId) return;
    const requestId = selectedDetailRequestRef.current + 1;
    selectedDetailRequestRef.current = requestId;
    void api
      .livePosition(positionId)
      .then((detail) => {
        if (selectedDetailRequestRef.current === requestId) setSelectedDetail(detail);
      })
      .catch((err) => {
        if (selectedDetailRequestRef.current === requestId) {
          setError(err instanceof Error ? err.message : "상세 포지션 데이터를 불러오지 못했습니다.");
        }
      });
  }, [selected?.position.id, selectedDetail?.position.id]);

  useEffect(() => {
    if (!selected?.position.id) return;
    const desiredDetailLevel = viewMode === "minimal" ? "compact" : "full";
    const hasCurrentChart =
      selectedChartAnalysis?.position_id === selected.position.id &&
      selectedChartAnalysis.detail_level === desiredDetailLevel;
    if (!hasCurrentChart) {
      setSelectedChartAnalysis(null);
    }
    void loadSelectedChart(selected.position.id, !hasCurrentChart, viewMode === "minimal");
  }, [selected?.position.id, viewMode]);

  useEffect(() => {
    if (viewMode !== "pro" || !positions.length) {
      setStripChartAnalysis({});
      return;
    }
    let cancelled = false;
    const ids = positions.slice(0, 10).map((item) => item.position.id);
    async function loadStripCharts() {
      const results = await Promise.allSettled(
        ids.map(async (positionId) => [positionId, await api.positionChartAnalysis(positionId, "4h", true)] as const)
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
  }, [stripChartKey, viewMode]);

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
          <ViewModeToggle mode={viewMode} onChange={updateViewMode} />
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
            onSelect={(positionId) => {
              setSelectedId(positionId);
              document.querySelector(".cockpitToolbar")?.scrollIntoView({ block: "start" });
            }}
            compact={viewMode === "minimal"}
          />
          {selectedPayload ? (
            <>
              {viewMode === "minimal" ? (
                <MinimalPositionWorkspace
                  payload={selectedPayload}
                  chartAnalysis={selectedChartForPayload}
                  chartLoading={selectedChartLoading}
                  chartError={selectedChartError}
                  onRetryChart={() => void loadSelectedChart(selectedPayload.position.id)}
                  onRefresh={() => void refreshSelected(selectedPayload.position.id)}
                  refreshing={actionLoading === `refresh:${selectedPayload.position.id}`}
                  onShowPro={() => updateViewMode("pro")}
                  workspace={workspace}
                />
              ) : (
                <>
                  <PositionVerdictBar
                    payload={selectedPayload}
                    onRefresh={() => void refreshSelected(selectedPayload.position.id)}
                    refreshing={actionLoading === `refresh:${selectedPayload.position.id}`}
                  />
                  <SymbolAnalysisView
                    chartAnalysis={selectedChartForPayload}
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
              )}
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
  compact,
  positions,
  selectedId,
  onSelect
}: {
  chartAnalysisById: Record<string, PositionChartAnalysis>;
  compact: boolean;
  positions: LivePositionPayload[];
  selectedId: string;
  onSelect: (positionId: string) => void;
}) {
  const sortedPositions = [...positions].sort((left, right) => right.state.severity_rank - left.state.severity_rank);
  return (
    <section className={`positionStrip ${compact ? "compact" : ""}`} aria-label="보유 포지션" data-testid="position-strip">
      {sortedPositions.map((item) => {
        const trigger = nearestActionTrigger(item);
        const selected = item.position.id === selectedId;
        if (compact) {
          return (
            <MinimalAssetCard
              key={item.position.id}
              meta={`${directionLabel(item.position.direction)} · ${item.position.leverage}x`}
              onClick={() => onSelect(item.position.id)}
              selected={selected}
              symbol={item.position.symbol}
              title={`${item.position.symbol} · ${directionLabel(item.position.direction)} ${item.position.leverage}x · ${item.state.status_label}`}
              tone={item.state.severity_rank >= 3 ? "negative" : item.state.severity_rank >= 1 ? "watch" : "positive"}
            />
          );
        }
        return (
          <button
            className={`positionStripCard severity-${item.state.severity_rank} ${selected ? "selected" : ""}`}
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
                {roeContextLabel(item) ? <small>{roeContextLabel(item)}</small> : null}
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

function ViewModeToggle({ mode, onChange }: { mode: FceViewMode; onChange: (mode: FceViewMode) => void }) {
  return (
    <div className="viewModeToggle" role="group" aria-label="화면 모드">
      <button className={mode === "minimal" ? "active" : ""} onClick={() => onChange("minimal")} type="button" data-testid="minimal-mode-button">
        미니멀
      </button>
      <button className={mode === "pro" ? "active" : ""} onClick={() => onChange("pro")} type="button" data-testid="pro-mode-button">
        프로
      </button>
    </div>
  );
}

function MinimalPositionWorkspace({
  payload,
  chartAnalysis,
  chartLoading,
  chartError,
  onRetryChart,
  onRefresh,
  refreshing,
  onShowPro,
  workspace
}: {
  payload: LivePositionPayload;
  chartAnalysis: PositionChartAnalysis | null;
  chartLoading: boolean;
  chartError: string;
  onRetryChart: () => void;
  onRefresh: () => void;
  refreshing: boolean;
  onShowPro: () => void;
  workspace: ReturnType<typeof useAnalysisWorkspace>;
}) {
  const plan = actionPlanForPayload(payload);
  const copy = minimalPositionCopy(payload);
  const gauges = (payload as LivePositionDetail).gauges ?? null;
  const nextPrice = compactNextPriceForPlan(plan, payload.latest_snapshot.mark_price);
  void onRefresh;
  void refreshing;
  void onShowPro;
  void workspace;
  return (
    <section className="minimalPositionWorkspace" data-testid="minimal-position-workspace">
      <CompactChartWorkspace
        analysis={chartAnalysis}
        loading={chartLoading}
        error={chartError}
        onRetry={onRetryChart}
        trendSummary={gauges?.market_view?.stance_label || "시장 판단 대기"}
        plan={plan}
        gauges={gauges}
        nextPrice={nextPrice}
        positionOverlay={chartOverlayFromPayload(payload)}
        onOpenEvidence={() => {
          workspace.focusEvidence("levels", nextPrice?.price ?? null);
          onShowPro();
        }}
      />
    </section>
  );
}

function compactNextPriceForPlan(plan: PositionActionPlan | null, markPrice: number | null): CompactNextPrice | null {
  const candidates: CompactNextPrice[] = [];
  const invalidation = numericPlanPrice(plan?.invalidation) ?? numericPlanPrice(plan?.engine_invalidation);
  if (invalidation !== null) candidates.push({ label: "무효화", price: invalidation, detail: plan?.invalidation?.action || "이탈 시 논리 점검" });
  const target = numericPlanPrice(plan?.take_profit?.[0]);
  if (target !== null) candidates.push({ label: "익절", price: target, detail: plan?.take_profit?.[0]?.action || "도달 시 부분 익절 검토" });
  if (!candidates.length) return null;
  if (markPrice === null || !Number.isFinite(markPrice)) return candidates[0];
  return [...candidates].sort((left, right) => Math.abs((left.price ?? markPrice) - markPrice) - Math.abs((right.price ?? markPrice) - markPrice))[0];
}

function TaOneLineStrip({
  oneLiners,
  payload,
  loading,
  selectedEvidenceKey,
  onSelectEvidence,
  onShowPro
}: {
  oneLiners: OneLinerSummary | null;
  payload: LivePositionPayload;
  loading: boolean;
  selectedEvidenceKey: string;
  onSelectEvidence: (evidence: MinimalEvidenceChoice) => void;
  onShowPro: () => void;
}) {
  const showSecondaryTaRows = useSecondaryTaRows();
  if (loading) {
    return (
      <section className="taOneLineStrip loading" data-testid="ta-one-line-strip" aria-live="polite">
        <span className="taOneLineSpinner" aria-hidden="true" />
        TA 판정 불러오는 중
      </section>
    );
  }

  const lines = normalizedOneLinerLines(oneLiners, showSecondaryTaRows);
  const choices = oneLinerEvidenceChoices(oneLiners, payload, showSecondaryTaRows);
  const counts = countOneLinerStances(lines);
  const conflict = (counts["상방"] ?? 0) > 0 && (counts["하방"] ?? 0) > 0;

  return (
    <section className="taOneLineStrip" data-testid="ta-one-line-strip">
      <div className="taOneLineGrid">
        {lines.map((line, index) => {
          const evidence = choices[index] ?? oneLinerChoiceFromLine(line, payload);
          return (
            <button
              className={`taOneLineRow ${selectedEvidenceKey === evidence.key ? "active" : ""}`}
              key={`${line.module}-${line.evidence_ref || line.phrase}`}
              onClick={() => onSelectEvidence(evidence)}
              onDoubleClick={onShowPro}
              title="클릭하면 차트 근거를 전환합니다. 더블클릭하면 자세히 봅니다."
              type="button"
            >
              <span className="taOneLineModule">{line.module_label}</span>
              <span
                aria-label={`${stanceDisplayLabel(line.stance)} · ${line.confidence_class}`}
                className={`taOneLineDot ${oneLinerStanceClass(line.stance)} ${oneLinerConfidenceClass(line.confidence_class)}`}
              />
              <strong>{plainifyTaText(line.phrase)}</strong>
            </button>
          );
        })}
      </div>
      <div className="taOneLineSummary">
        <span>{oneLinerSummaryText(counts)}</span>
        {conflict ? <em>충돌</em> : null}
      </div>
    </section>
  );
}

const ONE_LINER_MODULES: Array<{ module: OneLinerLine["module"]; label: string }> = [
  { module: "wyckoff", label: "와이코프" },
  { module: "liquidity", label: "유동성" },
  { module: "volume", label: "볼륨" },
  { module: "harmonic", label: "하모닉" },
  { module: "levels", label: "레벨" },
  { module: "derivatives", label: "수급" },
  { module: "indicators", label: "지표" }
];

function normalizedOneLinerLines(oneLiners: OneLinerSummary | null, includeSecondary = true): OneLinerLine[] {
  const byModule = new Map((oneLiners?.lines ?? []).map((line) => [line.module, line]));
  return visibleTaRows(
    ONE_LINER_MODULES.map(({ module, label }) => byModule.get(module) ?? fallbackOneLinerLine(module, label)),
    includeSecondary
  );
}

function fallbackOneLinerLine(module: OneLinerLine["module"], label: string): OneLinerLine {
  return {
    module,
    module_label: label,
    stance: "판단불가",
    phrase: "데이터 부족",
    confidence_class: "약",
    evidence_ref: module
  };
}

function oneLinerEvidenceChoices(oneLiners: OneLinerSummary | null, payload: LivePositionPayload, includeSecondary = true): MinimalEvidenceChoice[] {
  return normalizedOneLinerLines(oneLiners, includeSecondary).map((line) => oneLinerChoiceFromLine(line, payload));
}

function oneLinerChoiceFromLine(line: OneLinerLine, payload: LivePositionPayload): MinimalEvidenceChoice {
  const fallbackPrice = nearestActionTriggerPrice(payload);
  return {
    key: `ta:${line.module}:${line.evidence_ref || line.phrase}`,
    text: line.phrase,
    layer: oneLinerEvidenceLayer(line),
    label: `${line.module_label} · ${plainifyTaText(line.phrase)}`,
    price: oneLinerEvidencePrice(line) ?? fallbackPrice
  };
}

function oneLinerEvidenceLayer(line: OneLinerLine): MinimalEvidenceLayer {
  if (line.module === "wyckoff") return "wyckoff";
  if (line.module === "liquidity") return "liquidity";
  if (line.module === "harmonic") return "harmonic";
  if (line.module === "volume" || line.module === "derivatives") return "flow";
  if (line.module === "levels" || line.module === "indicators") return "levels";
  return "plan";
}

function oneLinerEvidencePrice(line: OneLinerLine): number | null {
  const source = `${line.evidence_ref} ${line.phrase}`;
  const match = source.match(/(?:price|level|poc|s1|r1|=|:)\s*(-?\d{1,3}(?:,\d{3})*(?:\.\d+)?|-?\d+\.\d+)/i);
  if (!match) return null;
  const parsed = Number(match[1].replace(/,/g, ""));
  return Number.isFinite(parsed) && parsed > 0 ? parsed : null;
}

function countOneLinerStances(lines: OneLinerLine[]): Record<OneLinerStance, number> {
  return lines.reduce<Record<OneLinerStance, number>>(
    (counts, line) => {
      counts[line.stance] += 1;
      return counts;
    },
    { "상방": 0, "하방": 0, "횡보": 0, "판단불가": 0 }
  );
}

function oneLinerSummaryText(counts: Record<OneLinerStance, number>): string {
  return `종합: 상방 ${counts["상방"] ?? 0} · 하방 ${counts["하방"] ?? 0} · 중립 ${counts["횡보"] ?? 0} · 판단불가 ${counts["판단불가"] ?? 0}`;
}

function stanceDisplayLabel(stance: OneLinerStance): string {
  return stance === "횡보" ? "중립" : stance;
}

function oneLinerStanceClass(stance: OneLinerStance): string {
  if (stance === "상방") return "stance-up";
  if (stance === "하방") return "stance-down";
  if (stance === "횡보") return "stance-neutral";
  return "stance-unknown";
}

function oneLinerConfidenceClass(confidence: OneLinerLine["confidence_class"]): string {
  if (confidence === "강") return "confidence-strong";
  if (confidence === "중") return "confidence-medium";
  return "confidence-weak";
}

function MinimalPositionVerdictCard({
  payload,
  copy,
  selectedEvidenceKey,
  onSelectEvidence,
  onChart,
  onRefresh,
  refreshing,
  onShowPro
}: {
  payload: LivePositionPayload;
  copy: MinimalPositionCopy;
  selectedEvidenceKey: string;
  onSelectEvidence: (evidence: MinimalEvidenceChoice) => void;
  onChart: () => void;
  onRefresh: () => void;
  refreshing: boolean;
  onShowPro: () => void;
}) {
  const trigger = nearestActionTrigger(payload);
  const pnlPrimary = minimalPnlPrimary(payload);
  const pnlSecondary = minimalPnlSecondary(payload);
  return (
    <article
      className={`oneQuestionCard positionOneQuestion state-${copy.state}`}
      data-budget-numbers-max="7"
      data-budget-buttons-max="4"
      data-testid="position-one-question-card"
    >
      <div className="oneQuestionTop">
        <div>
          <strong>{payload.position.symbol} {directionLabel(payload.position.direction)} {payload.position.leverage}x</strong>
          <span>{marginModeLabel(payload.position.margin_mode ?? "")}</span>
        </div>
        <em className={payload.state.pnl_percent >= 0 ? "successText" : "dangerText"}>
          {pnlPrimary}
          {pnlSecondary ? <small>{pnlSecondary}</small> : null}
        </em>
      </div>
      <div className="oneQuestionAnswer">
        <span className="oneQuestionDot" aria-hidden="true" />
        <h2>{copy.label}</h2>
      </div>
      <div className="oneQuestionReasons">
        <button
          className={`oneQuestionEvidence ${selectedEvidenceKey === copy.whyEvidence.key ? "active" : ""}`}
          onClick={() => onSelectEvidence(copy.whyEvidence)}
          type="button"
        >
          <b>왜:</b> <span>{copy.why}</span>
        </button>
        <button
          className={`oneQuestionEvidence ${selectedEvidenceKey === copy.counterEvidence.key ? "active" : ""}`}
          onClick={() => onSelectEvidence(copy.counterEvidence)}
          type="button"
        >
          <b>반대:</b> <span>{copy.counter}</span>
        </button>
      </div>
      <div className="oneQuestionNext">
        <span>다음 가격</span>
        <strong>{trigger ? `${trigger.price} ${trigger.distanceLabel}` : copy.next}</strong>
        <em>{trigger ? plainifyTaText(trigger.action) : "조건 확인"}</em>
      </div>
      {liquidationMissing(payload) ? (
        <div className="oneQuestionWarning">
          <AlertTriangle size={14} />
          청산가 미수신 · 거래소에서 수동 확인
        </div>
      ) : null}
      <div className="oneQuestionActions">
        <button className="button secondary" onClick={onChart} type="button">차트</button>
        <button className="button secondary" onClick={onRefresh} disabled={refreshing} type="button">
          {refreshing ? "갱신 중" : "갱신"}
        </button>
        <button className="button" onClick={onShowPro} type="button">자세히 →</button>
      </div>
    </article>
  );
}

type MinimalPositionCopy = {
  state: string;
  label: string;
  why: string;
  counter: string;
  next: string;
  whyEvidence: MinimalEvidenceChoice;
  counterEvidence: MinimalEvidenceChoice;
};

function minimalPositionCopy(payload: LivePositionPayload): MinimalPositionCopy {
  const plan = actionPlanForPayload(payload);
  const verdictState = plan?.verdict_state ?? "holding";
  const label = minimalStatusLabel(payload, verdictState);
  const briefing = payload.analyst_briefing?.confluence;
  const sameDirection = payload.position.direction === "short" ? briefing?.short_evidence : briefing?.long_evidence;
  const oppositeDirection = payload.position.direction === "short" ? briefing?.long_evidence : briefing?.short_evidence;
  const riskState = verdictState === "danger" || verdictState === "weakening" || payload.state.severity_rank >= 2;
  const headlineText = minimalHeadlineText(payload);
  const counterSource = riskState ? dedupeEvidence(sameDirection) : dedupeEvidence([...(briefing?.counter_evidence ?? []), ...(oppositeDirection ?? [])]);
  const whyEvidence = evidenceChoiceFromAnalyst(
    undefined,
    headlineText,
    "why",
    nearestActionTriggerPrice(payload)
  );
  const counterEvidence = evidenceChoiceFromAnalyst(
    counterSource[0],
    standbyReferenceText(plan) || "반대 근거는 아직 강하게 확인되지 않았습니다.",
    "counter",
    nearestActionTriggerPrice(payload)
  );
  const why = plainifyTaText(whyEvidence.text);
  const counter = plainifyTaText(counterEvidence.text);
  return {
    state: verdictState,
    label,
    why: clampSentence(why, 108),
    counter: clampSentence(counter, 82),
    next: plan?.standby_reason ? plainifyTaText(plan.standby_reason) : "가까운 트리거 대기",
    whyEvidence,
    counterEvidence
  };
}

function minimalStatusLabel(payload: LivePositionPayload, verdictState: string): string {
  const label = plainifyTaText(payload.state.status_label || "");
  return label || minimalVerdictLabel(verdictState, payload.state.severity_rank);
}

function minimalHeadlineText(payload: LivePositionPayload): string {
  const raw = plainifyTaText(headlineForPayload(payload));
  const cleaned = raw
    .replace(/^→\s*/u, "")
    .replace(/^지금\s*볼\s*것\s*[:：]\s*/u, "")
    .trim();
  const triggerPrice = nearestActionTriggerPrice(payload);
  const readable = triggerPrice === null
    ? cleaned
    : cleaned.replace(/\d+(?:\.\d+)?/u, formatPrice(triggerPrice));
  return readable || "현재 평결 기준을 확인하세요.";
}

function dedupeEvidence(items: AnalystEvidence[] | undefined): AnalystEvidence[] {
  const seen = new Set<string>();
  const result: AnalystEvidence[] = [];
  for (const item of items ?? []) {
    const key = `${item.engine}:${plainifyTaText(item.claim).replace(/[.\s]+$/g, "").toLowerCase()}`;
    if (seen.has(key)) continue;
    seen.add(key);
    result.push(item);
  }
  return result;
}

function evidenceChoiceFromAnalyst(
  evidence: AnalystEvidence | undefined,
  fallbackText: string,
  prefix: "why" | "counter",
  fallbackPrice: number | null
): MinimalEvidenceChoice {
  if (!evidence) {
    return {
      key: `${prefix}:plan:${fallbackText}`,
      text: fallbackText,
      layer: "plan",
      label: prefix === "why" ? "판정 근거" : "반대 근거",
      price: fallbackPrice
    };
  }
  const text = plainifyTaText(evidence.claim);
  return {
    key: `${prefix}:${evidence.engine}:${text}`,
    text,
    layer: evidenceLayerFromAnalyst(evidence),
    label: evidenceShortLabel(evidence),
    price: evidencePrice(evidence) ?? fallbackPrice,
    time: evidence.as_of ? Math.floor(new Date(evidence.as_of).getTime() / 1000) : null
  };
}

function evidenceLayerFromAnalyst(evidence: AnalystEvidence): MinimalEvidenceLayer {
  const value = `${evidence.engine} ${evidence.claim} ${evidence.source ?? ""}`.toLowerCase();
  if (value.includes("wyckoff") || value.includes("spring") || value.includes("utad") || value.includes("와이코프")) return "wyckoff";
  if (value.includes("liquidity") || value.includes("sweep") || value.includes("스윕") || value.includes("유동성")) return "liquidity";
  if (value.includes("harmonic") || value.includes("prz") || value.includes("하모닉") || value.includes("반전 후보")) return "harmonic";
  if (value.includes("flow") || value.includes("funding") || value.includes("oi") || value.includes("체결") || value.includes("수급") || value.includes("펀딩")) return "flow";
  if (value.includes("level") || value.includes("support") || value.includes("resistance") || value.includes("지지") || value.includes("저항")) return "levels";
  return "plan";
}

function evidenceShortLabel(evidence: AnalystEvidence): string {
  const text = plainifyTaText(evidence.claim);
  if (text.includes("스윕")) return "스윕 근거";
  if (text.includes("반전 후보")) return "반전 후보";
  if (text.includes("지지") || text.includes("저항")) return "구조 레벨";
  if (text.includes("체결") || text.includes("수급") || text.includes("펀딩")) return "수급 근거";
  return text.split(/[.·]/)[0]?.trim() || "판정 근거";
}

function evidencePrice(evidence: AnalystEvidence): number | null {
  if (typeof evidence.score === "number" && Number.isFinite(evidence.score) && evidence.score > 0) return null;
  const match = evidence.claim.match(/(?:\d{1,3}(?:,\d{3})+|\d+\.\d+|\d{4,})/);
  if (!match) return null;
  const parsed = Number(match[0].replace(/,/g, ""));
  return Number.isFinite(parsed) && parsed > 0 ? parsed : null;
}

function nearestActionTriggerPrice(payload: LivePositionPayload): number | null {
  // 표시용 문자열을 역파싱하면 로케일 그룹핑(1.234,56 등)에서 자릿수가 깨진다 — 원본 숫자 사용.
  return nearestActionTrigger(payload)?.priceValue ?? null;
}

function minimalVerdictLabel(verdictState: string, severityRank: number): string {
  if (verdictState === "danger" || severityRank >= 4) return "위험 확인 필요";
  if (verdictState === "standby") return "판단 유보";
  if (verdictState === "weakening" || severityRank >= 2) return "근거 약화";
  return "유지 근거 우세";
}

function standbyReferenceText(plan: PositionActionPlan | null): string {
  const zone = plan?.reference_zones?.[0];
  if (!zone) return "";
  return `${zone.basis || "참조 존"} 기준은 약하므로 알림 트리거로 쓰지 않습니다.`;
}

function minimalPnlPrimary(payload: LivePositionPayload): string {
  const amount = payload.state.pnl_amount ?? payload.position.unrealized_pl;
  if (typeof amount === "number" && Number.isFinite(amount)) {
    const sign = amount > 0 ? "+" : "";
    return `${sign}${formatPrice(amount)} USDT`;
  }
  return signedPercent(payload.state.pnl_percent);
}

function minimalPnlSecondary(payload: LivePositionPayload): string {
  const roe = signedPercent(payload.state.pnl_percent);
  const context = roeContextLabel(payload);
  return context ? `${roe} · ${context}` : roe;
}

function clampSentence(value: string, maxLength: number): string {
  const normalized = value.replace(/\s+/g, " ").trim();
  if (normalized.length <= maxLength) return normalized;
  return `${normalized.slice(0, maxLength - 1).trim()}…`;
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
  // state.as_of와 plan.as_of는 같은 응답에서 함께 생성돼 차이가 항상 ~0 —
  // 폴링이 멈춰도 "유효"로 보이는 버그가 있었다. 벽시계 틱(30s)으로 실제 나이를 계산한다.
  const [nowTick, setNowTick] = useState(() => Date.now());
  useEffect(() => {
    const interval = window.setInterval(() => setNowTick(Date.now()), 30_000);
    return () => window.clearInterval(interval);
  }, []);
  const ageMinutes = asOf ? (nowTick - asOf.getTime()) / 60000 : null;
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
              {roeContextLabel(payload) ? <small>{roeContextLabel(payload)}</small> : null}
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
  const rows = actionPlanRows(plan, true);
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
    <>
      <MoneyFlowCard derivatives={payload.state.analysis.derivatives} />
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
    </>
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
  const chartRequestRef = useRef("");
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
    // 타임프레임/포지션 연속 전환 시 늦게 도착한 이전 응답이 최신 선택을 덮어쓰지 않게,
    // 마지막으로 발행한 요청만 화면에 반영한다.
    const requestKey = `${positionId}:${nextTimeframe}`;
    chartRequestRef.current = requestKey;
    try {
      const next = await api.positionChartAnalysis(positionId, nextTimeframe);
      if (chartRequestRef.current !== requestKey) return;
      setChartAnalysis(next);
    } catch (err) {
      if (chartRequestRef.current !== requestKey) return;
      if (showSpinner) setChartAnalysis(null);
      setChartError(err instanceof Error ? err.message : "차트 분석 데이터를 불러오지 못했습니다.");
    } finally {
      if (showSpinner && chartRequestRef.current === requestKey) setChartLoading(false);
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
    "채점 가능한 구조 없음 — 데이터 표본 축적 중. 참조 존 형성 대기."
  );
}

function nearestActionTrigger(
  payload: LivePositionPayload
): { distance_pct: number | null; distanceLabel: string; price: string; priceValue: number | null; action: string } | null {
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
      return derivedDistance === null
        ? null
        : {
            distance_pct: derivedDistance,
            distanceLabel: formatDistance(derivedDistance),
            price: formatNullablePrice(item.price),
            priceValue: typeof item.price === "number" && Number.isFinite(item.price) ? item.price : null,
            action: item.action || item.basis || "조건 확인"
          };
    })
    .filter(
      (item): item is { distance_pct: number; distanceLabel: string; price: string; priceValue: number | null; action: string } =>
        item !== null
    );
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

function actionPlanRows(plan: PositionActionPlan | null, compact = false) {
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
  for (const target of takeProfitTargets.slice(0, compact ? 1 : 3)) {
    rows.push({
      kind: "익절",
      price: `${formatNullablePrice(target.price)} · ${formatDistance(target.distance_pct)}`,
      action: target.action ?? "부분 익절 검토",
      basis: target.basis ?? "익절 후보",
      tone: "positive",
      priceValue: target.price
    });
  }
  for (const trigger of watchTriggers.slice(0, compact ? 1 : 3)) {
    rows.push({
      kind: "감시",
      condition: trigger.condition ?? "조건 확인",
      action: "조건 확인",
      basis: trigger.meaning ?? "추가 확인 필요",
      tone: "warning",
      priceValue: null
    });
  }
  if (compact) {
    const hiddenCount = Math.max(0, takeProfitTargets.length - 1) + Math.max(0, watchTriggers.length - 1) + Math.max(0, (plan.reference_zones ?? []).length) + (typeof plan.liquidation?.price === "number" ? 1 : 0);
    if (hiddenCount > 0) {
      rows.push({
        kind: "더보기",
        condition: `접힌 항목 ${hiddenCount}개`,
        action: "프로 화면에서 전체 가격 보기",
        basis: "기본 화면은 무효화 1 · 익절 1 · 감시 1만 표시",
        tone: "neutral",
        priceValue: null
      });
    }
    return rows.slice(0, 4);
  }
  for (const reference of (plan.reference_zones ?? []).slice(0, 3)) {
    rows.push({
      kind: "참조",
      price: `${formatNullablePrice(reference.price)} · ${formatDistance(reference.distance_pct)}`,
      action: reference.action ?? "알림 미사용 · 수동 참고",
      basis: reference.basis ?? reference.label ?? "근거 약함 · 참고 전용",
      tone: "neutral",
      priceValue: reference.price
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
  return rows.slice(0, 8);
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

function roeContextLabel(payload: LivePositionPayload): string | null {
  if (Math.abs(payload.state.pnl_percent) <= 100) return null;
  const mode = payload.position.margin_mode ? marginModeLabel(payload.position.margin_mode) : "ROE";
  return `${mode} · -100% 초과 가능`;
}

function marginModeLabel(mode: string): string {
  const normalized = mode.toLowerCase();
  if (normalized.includes("cross")) return "교차";
  if (normalized.includes("isolated") || normalized.includes("fixed")) return "격리";
  return mode;
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
