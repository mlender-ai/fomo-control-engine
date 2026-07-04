import type { PositionChartAnalysis } from "@/lib/api";
import { formatPrice } from "@/lib/format";

export function VolumeProfilePanel({ analysis }: { analysis: PositionChartAnalysis }) {
  const maxVolume = Math.max(...analysis.volume_profile.bins.map((bin) => bin.volume), 1);
  const visibleBins = [...analysis.volume_profile.bins].reverse();
  const markAbovePoc = analysis.mark_price >= analysis.volume_profile.poc_price;
  return (
    <section className="analysisPanel volumeProfilePanel">
      <div className="analysisPanelHeader">
        <div>
          <h2>추정 볼륨 프로파일</h2>
          <p>OHLCV 기반으로 추정한 거래량 분포입니다. 실제 체결 분포와 다를 수 있습니다.</p>
        </div>
        <span>POC {formatPrice(analysis.volume_profile.poc_price)}</span>
      </div>
      <p className="volumeProfileDescription">POC: 거래량이 가장 많이 쌓인 가격대</p>
      <div className="volumeProfileMeta">
        <span>Value Area High {formatPrice(analysis.volume_profile.value_area_high)}</span>
        <span>Value Area Low {formatPrice(analysis.volume_profile.value_area_low)}</span>
        <span>현재가: POC {markAbovePoc ? "위" : "아래"}</span>
      </div>
      <div className="volumeProfileRows">
        {visibleBins.map((bin) => (
          <div className="volumeProfileRow" key={`${bin.price_low}-${bin.price_high}`}>
            <span>{formatPrice((bin.price_low + bin.price_high) / 2)}</span>
            <div>
              <i style={{ width: `${Math.max(3, (bin.buy_volume_proxy / maxVolume) * 100)}%` }} />
              <b style={{ width: `${Math.max(3, (bin.sell_volume_proxy / maxVolume) * 100)}%` }} />
            </div>
          </div>
        ))}
      </div>
    </section>
  );
}
