"use client";

import {
  CandlestickSeries,
  ColorType,
  createChart,
  createSeriesMarkers,
  type CandlestickData,
  type SeriesMarker,
  type Time
} from "lightweight-charts";
import { useEffect, useMemo, useRef, useState } from "react";
import { api, type StockPaperEntryChart as EntryChartData, type StockPaperFill } from "@/lib/api";
import { createChartPalette } from "@/lib/chartTheme";
import { formatPrice } from "@/lib/format";

type Instrument = { market: "KR" | "US"; symbol: string; currency: "KRW" | "USD" };

export function StockPaperEntryChart({ fills }: { fills: StockPaperFill[] }) {
  const instruments = useMemo(() => uniqueInstruments(fills), [fills]);
  const [selected, setSelected] = useState<Instrument | null>(instruments[0] ?? null);
  const [data, setData] = useState<EntryChartData | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    if (!selected || instruments.some((item) => instrumentKey(item) === instrumentKey(selected))) return;
    setSelected(instruments[0] ?? null);
  }, [instruments, selected]);

  useEffect(() => {
    if (!selected) return;
    let cancelled = false;
    setLoading(true);
    setError("");
    void api.stockPaperEntryChart(selected.market, selected.symbol)
      .then((value) => { if (!cancelled) setData(value); })
      .catch((reason) => {
        if (!cancelled) {
          setData(null);
          setError(reason instanceof Error ? reason.message : "체결 차트를 불러오지 못했습니다.");
        }
      })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [selected]);

  return (
    <section className="stockEntryAudit" data-testid="stock-paper-entry-chart">
      <header>
        <div><span className="engineSectionLabel">체결 시점 감사</span><h3>언제 진입했나</h3><p>실제 PaperBroker fill을 Toss 관측 캔들 위에 표시합니다.</p></div>
        <nav aria-label="체결 종목 선택">
          {instruments.map((item) => (
            <button
              className={selected && instrumentKey(item) === instrumentKey(selected) ? "active" : ""}
              key={instrumentKey(item)}
              onClick={() => setSelected(item)}
              type="button"
            >
              <strong>{item.symbol}</strong><span>{item.market}</span>
            </button>
          ))}
        </nav>
      </header>
      {!selected ? (
        <div className="stockEntryEmpty">
          <strong>아직 차트에 표시할 페이퍼 진입이 없습니다.</strong>
          <span>정직한 체결 조건을 통과한 실제 fill이 생기면 이곳에 시각·가격을 표시합니다.</span>
        </div>
      ) : null}
      {loading ? <div className="stockEntryEmpty">실제 체결 주변 캔들을 불러오는 중입니다.</div> : null}
      {!loading && error ? <div className="stockEntryEmpty error">{error}</div> : null}
      {!loading && !error && data?.empty_reason ? (
        <div className="stockEntryEmpty">
          <strong>{data.empty_reason === "paper_fill_missing" ? "저장된 페이퍼 체결이 없습니다." : "체결 시점 주변 Toss 캔들이 없습니다."}</strong>
          <span>관측값이 생기기 전에는 차트나 가격을 합성하지 않습니다.</span>
        </div>
      ) : null}
      {!loading && !error && data && !data.empty_reason ? <EntryChartCanvas data={data} /> : null}
    </section>
  );
}

function EntryChartCanvas({ data }: { data: EntryChartData }) {
  const containerRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    const container = containerRef.current;
    if (!container || data.candles.length < 2) return;
    const palette = createChartPalette(container);
    const chart = createChart(container, {
      autoSize: true,
      height: 340,
      layout: {
        background: { type: ColorType.Solid, color: palette.color("panel") },
        textColor: palette.color("muted")
      },
      grid: {
        vertLines: { color: palette.color("neutral", 0.16) },
        horzLines: { color: palette.color("neutral", 0.16) }
      },
      rightPriceScale: { borderColor: palette.color("neutral", 0.3) },
      timeScale: {
        borderColor: palette.color("neutral", 0.3),
        timeVisible: data.timeframe === "1m",
        secondsVisible: false,
        rightOffset: 5
      },
      localization: { locale: "ko-KR" }
    });
    const series = chart.addSeries(CandlestickSeries, {
      upColor: palette.color("green"),
      downColor: palette.color("red"),
      borderVisible: false,
      wickUpColor: palette.color("green", 0.9),
      wickDownColor: palette.color("red", 0.9)
    });
    const candles: CandlestickData[] = data.candles.map((candle) => ({
      time: epochSeconds(candle.opened_at) as Time,
      open: candle.open,
      high: candle.high,
      low: candle.low,
      close: candle.close
    }));
    series.setData(candles);
    const candleTimes = candles.map((candle) => Number(candle.time));
    const markers: SeriesMarker<Time>[] = data.fills.map((fill) => ({
      time: nearestTime(epochSeconds(fill.filled_at), candleTimes) as Time,
      position: fill.side === "buy" ? "belowBar" : "aboveBar",
      color: fill.side === "buy" ? palette.color("teal") : palette.color("amber"),
      shape: fill.side === "buy" ? "arrowUp" : "arrowDown",
      text: `${fill.side === "buy" ? "진입" : "청산"} ${formatPrice(fill.price)}`
    }));
    createSeriesMarkers(series, markers.sort((left, right) => Number(left.time) - Number(right.time)));
    chart.timeScale().fitContent();
    return () => chart.remove();
  }, [data]);

  return (
    <div className="stockEntryChartBody">
      <div className="stockEntryChartMeta">
        <strong>{data.symbol}</strong>
        <span>{data.market} · {data.timeframe === "1m" ? "1분봉" : "일봉"} · {data.source}</span>
        <small>▲ 진입 · ▼ 청산 · 표시는 실제 페이퍼 체결</small>
      </div>
      <div className="stockEntryChartCanvas" ref={containerRef} />
      <div className="stockEntryFillList">
        {data.fills.map((fill) => (
          <article className={fill.side} key={fill.id}>
            <i />
            <div><strong>{fill.side === "buy" ? "진입" : "청산"}</strong><time>{fillTime(fill.filled_at)}</time></div>
            <b>{money(fill.price, fill.currency)}</b>
            <span>{fill.quantity.toLocaleString("ko-KR")}주 · 실제 fill</span>
          </article>
        ))}
      </div>
    </div>
  );
}

function uniqueInstruments(fills: StockPaperFill[]): Instrument[] {
  const seen = new Set<string>();
  return fills.flatMap((fill) => {
    const item: Instrument = { market: fill.market, symbol: fill.symbol, currency: fill.currency };
    const key = instrumentKey(item);
    if (seen.has(key)) return [];
    seen.add(key);
    return [item];
  });
}

function instrumentKey(item: Instrument): string {
  return `${item.market}:${item.symbol}`;
}

function epochSeconds(value: string): number {
  return Math.floor(new Date(value).getTime() / 1_000);
}

function nearestTime(target: number, candidates: number[]): number {
  return candidates.reduce((nearest, value) => Math.abs(value - target) < Math.abs(nearest - target) ? value : nearest, candidates[0]);
}

function fillTime(value: string): string {
  return new Date(value).toLocaleString("ko-KR", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit"
  });
}

function money(value: number, currency: "KRW" | "USD"): string {
  return new Intl.NumberFormat("ko-KR", {
    style: "currency",
    currency,
    maximumFractionDigits: currency === "KRW" ? 0 : 2
  }).format(value);
}
