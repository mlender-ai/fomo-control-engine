import type { PositionChartAnalysis } from "@/lib/api";
import { formatPrice } from "@/lib/format";

export function VolumeProfilePanel({ analysis }: { analysis: PositionChartAnalysis }) {
  const maxVolume = Math.max(...analysis.volume_profile.bins.map((bin) => bin.volume), 1);
  const visibleBins = [...analysis.volume_profile.bins].reverse();
  return (
    <section className="analysisPanel volumeProfilePanel">
      <div className="analysisPanelHeader">
        <div>
          <h2>Estimated Volume Profile</h2>
          <p>OHLCV 기반 proxy입니다. 정확한 체결 분포가 아닙니다.</p>
        </div>
        <span>POC {formatPrice(analysis.volume_profile.poc_price)}</span>
      </div>
      <div className="volumeProfileMeta">
        <span>VAH {formatPrice(analysis.volume_profile.value_area_high)}</span>
        <span>VAL {formatPrice(analysis.volume_profile.value_area_low)}</span>
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
