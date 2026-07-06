"use client";

import type { PositionActionPlan, PositionChartAnalysis } from "@/lib/api";
import type { ChartLayerId, ChartLayerState } from "@/lib/chartLayers";
import type { Density } from "@/lib/density";
import { PositionCandlestickChart } from "./PositionCandlestickChart";

export function PositionChart({
  analysis,
  loading,
  error,
  onRetry,
  trendSummary = "구조 확인 중",
  plan,
  layers,
  onToggleLayer,
  highlightPrice,
  positionOverlay,
  density = "simple",
  intentZoneSelector
}: {
  analysis: PositionChartAnalysis | null;
  loading: boolean;
  error: string;
  onRetry: () => void;
  trendSummary?: string;
  plan: PositionActionPlan | null;
  layers: ChartLayerState;
  onToggleLayer: (id: ChartLayerId, additive: boolean) => void;
  highlightPrice?: number | null;
  positionOverlay?: PositionChartOverlay | null;
  density?: Density;
  intentZoneSelector?: {
    enabled: boolean;
    draft: { lower: number | null; upper: number | null };
    onDraftChange: (lower: number, upper: number) => void;
    onComplete: (lower: number, upper: number) => void;
  };
}) {
  if (loading && !analysis) {
    return (
      <section className="positionChartPanel" data-testid="position-chart">
        <div className="chartLoadingState">
          <i />
          <span>캔들 데이터를 불러오는 중입니다.</span>
        </div>
      </section>
    );
  }

  if (error || !analysis) {
    return (
      <section className="positionChartPanel" data-testid="position-chart">
        <div className="chartErrorState">
          <strong>차트 데이터를 불러올 수 없습니다.</strong>
          <p>가능한 원인: Bitget 시세 오류, 캔들 데이터 부족, 심볼 매핑 오류</p>
          {error ? <small>{error}</small> : null}
          <button className="button" onClick={onRetry} type="button">다시 불러오기</button>
        </div>
      </section>
    );
  }

  if (analysis.candles.length < 100) {
    return (
      <section className="positionChartPanel" data-testid="position-chart">
        <div className="chartErrorState">
          <strong>차트 분석에 필요한 캔들 데이터가 부족합니다.</strong>
          <p>최소 100개 이상의 캔들이 필요합니다.</p>
          <button className="button" onClick={onRetry} type="button">다시 불러오기</button>
        </div>
      </section>
    );
  }

  return (
    <section className="positionChartPanel" data-testid="position-chart">
      {loading ? <div className="chartRefreshingBadge">차트 갱신 중</div> : null}
      <PositionCandlestickChart
        analysis={analysis}
        trendSummary={trendSummary}
        plan={plan}
        layers={layers}
        onToggleLayer={onToggleLayer}
        highlightPrice={highlightPrice}
        positionOverlay={positionOverlay}
        density={density}
        intentZoneSelector={intentZoneSelector}
      />
    </section>
  );
}

export type PositionChartOverlay = {
  direction: "long" | "short";
  quantity: number;
  leverage: number;
  entryPrice: number;
  markPrice: number | null;
  pnlPercent: number;
  pnlAmount: number | null;
  openedAt: string | null;
};
