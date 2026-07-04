import type { PositionChartAnalysis } from "@/lib/api";
import { formatPrice } from "@/lib/format";

export function VolumeProfilePanel({ analysis }: { analysis: PositionChartAnalysis }) {
  const maxVolume = Math.max(...analysis.volume_profile.bins.map((bin) => bin.volume), 1);
  const visibleBins = [...analysis.volume_profile.bins].reverse();
  const markAbovePoc = analysis.mark_price >= analysis.volume_profile.poc_price;
  const hasTradeFills = analysis.volume_profile.has_trade_fills;
  return (
    <section className="analysisPanel volumeProfilePanel">
      <div className="analysisPanelHeader">
        <div>
          <h2>볼륨 프로파일</h2>
          <p>{hasTradeFills ? "실체결 구간은 매수/매도 체결량을 표시하고, 미커버 구간은 총량만 표시합니다." : "실체결 미수신 구간입니다. OHLCV 기반 총량 추정만 표시합니다."}</p>
        </div>
        <span>{methodLabel(analysis.volume_profile.method)} · POC {formatPrice(analysis.volume_profile.poc_price)}</span>
      </div>
      <p className="volumeProfileDescription">POC: 거래량이 가장 많이 쌓인 가격대</p>
      <div className="volumeProfileMeta">
        <span>Value Area High {formatPrice(analysis.volume_profile.value_area_high)}</span>
        <span>Value Area Low {formatPrice(analysis.volume_profile.value_area_low)}</span>
        <span>현재가: POC {markAbovePoc ? "위" : "아래"}</span>
        <span>출처 {analysis.volume_profile.source_methods.map(methodLabel).join(" + ")}</span>
      </div>
      <div className="volumeProfileRows">
        {visibleBins.map((bin) => (
          <div className="volumeProfileRow" key={`${bin.price_low}-${bin.price_high}`}>
            <span>{formatPrice((bin.price_low + bin.price_high) / 2)}</span>
            <div className={`volumeProfileBar method-${bin.method}`}>
              {bin.buy_volume !== undefined || bin.sell_volume !== undefined ? (
                <>
                  <b style={{ width: `${Math.max(3, ((bin.sell_volume ?? 0) / maxVolume) * 100)}%` }} />
                  <i style={{ width: `${Math.max(3, ((bin.buy_volume ?? 0) / maxVolume) * 100)}%` }} />
                </>
              ) : (
                <em style={{ width: `${Math.max(3, (bin.volume / maxVolume) * 100)}%` }} />
              )}
            </div>
          </div>
        ))}
      </div>
    </section>
  );
}

function methodLabel(method: string): string {
  if (method === "trade_fills") return "실체결";
  if (method === "ohlcv_estimated") return "OHLCV 추정";
  if (method === "mixed") return "혼합";
  return method;
}
