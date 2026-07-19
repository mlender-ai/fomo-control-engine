"use client";

import Link from "next/link";
import { ArrowRight, Building2, FileClock, LineChart, SlidersHorizontal } from "lucide-react";
import { useEffect, useState } from "react";
import { api, type CalibrationSummary, type PerformanceSummary, type StockPaperDashboard, type Trade } from "@/lib/api";
import { ReviewSectionNav } from "./review-section-nav";

export function ReviewOverviewShell() {
  const [trades, setTrades] = useState<Trade[] | null>(null);
  const [performance, setPerformance] = useState<PerformanceSummary | null>(null);
  const [calibration, setCalibration] = useState<CalibrationSummary | null>(null);
  const [stockPaper, setStockPaper] = useState<StockPaperDashboard | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    let cancelled = false;
    const recordError = (reason: unknown) => {
      if (!cancelled) setError(reason instanceof Error ? reason.message : "일부 성적 데이터를 불러오지 못했습니다.");
    };
    void api.trades().then((value) => { if (!cancelled) setTrades(value); }).catch(recordError);
    void api.performance().then((value) => { if (!cancelled) setPerformance(value); }).catch(recordError);
    void api.reviewCalibration().then((value) => { if (!cancelled) setCalibration(value); }).catch(recordError);
    void api.stockPaperDashboard().then((value) => { if (!cancelled) setStockPaper(value); }).catch(recordError);
    return () => { cancelled = true; };
  }, []);

  const tested = numeric(calibration?.totals?.tested);
  const accuracy = optionalNumeric(calibration?.totals?.accuracy_pct);
  return (
    <div className="page reviewOverviewPage" data-testid="review-overview-page">
      <ReviewSectionNav />
      <header className="reviewOverviewHeader">
        <p className="eyebrow">복기와 성적</p>
        <h1>무엇이 맞았고, 결과가 어땠는지</h1>
        <p>거래 결과, 엔진 판단, 계좌 성과를 같은 기록에서 나눠 봅니다.</p>
      </header>
      {error ? <div className="reviewOverviewNotice">{error}</div> : null}
      <section className="reviewOverviewGrid">
        <ReviewEntry
          href="/engine?tab=stocks"
          icon={Building2}
          title="주식 페이퍼"
          value={stockPaper?.tracks.length ? stockPaper.tracks.map((track) => `${track.market} ${track.elapsed_days}/28일`).join(" · ") : "트랙 준비 중"}
          detail={stockPaper?.sample_note || "나스닥100·코스피100을 독립 시계로 검증합니다."}
        />
        <ReviewEntry
          href="/trades"
          icon={FileClock}
          title="거래 복기"
          value={trades ? `${trades.length}건` : "불러오는 중"}
          detail={trades?.length ? "종료 거래별 판단 이력과 메모" : "종료된 거래가 쌓이면 자동 복기됩니다."}
        />
        <ReviewEntry
          href="/engine?tab=status"
          icon={SlidersHorizontal}
          title="엔진 상태"
          value={calibration?.cache_status === "preparing" ? "집계 준비 중" : calibration ? `${tested}건 검증${accuracy === null ? "" : ` · ${accuracy.toFixed(1)}%`}` : "불러오는 중"}
          detail={calibration?.sample_warning || "판단 유형별 적중과 표본 수를 확인합니다."}
        />
        <ReviewEntry
          href="/performance"
          icon={LineChart}
          title="계좌 성적표"
          value={performance ? `${performance.overall.sample_size}건 · ${signedMoney(performance.overall.net_profit_usdt)}` : "불러오는 중"}
          detail={performance?.overall.sample_warning || "실현손익, 낙폭, 수익팩터를 계좌 기준으로 봅니다."}
        />
      </section>
    </div>
  );
}

function ReviewEntry({ href, icon: Icon, title, value, detail }: { href: string; icon: typeof FileClock; title: string; value: string; detail: string }) {
  return (
    <Link className="reviewOverviewEntry" href={href}>
      <span className="reviewOverviewIcon"><Icon size={20} /></span>
      <div><span>{title}</span><strong>{value}</strong><small>{detail}</small></div>
      <ArrowRight size={18} />
    </Link>
  );
}

function numeric(value: unknown): number { return typeof value === "number" && Number.isFinite(value) ? value : 0; }
function optionalNumeric(value: unknown): number | null { return typeof value === "number" && Number.isFinite(value) ? value : null; }
function signedMoney(value: number): string { return `${value > 0 ? "+" : ""}${value.toFixed(2)} USDT`; }
