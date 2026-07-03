"use client";

import { useEffect, useState } from "react";
import { api, type Trade } from "@/lib/api";
import { formatPrice, signedPercent } from "@/lib/format";

export function JournalShell() {
  const [trades, setTrades] = useState<Trade[]>([]);
  const [error, setError] = useState("");

  useEffect(() => {
    api.trades().then(setTrades).catch((err) => setError(err instanceof Error ? err.message : "Failed to load journal"));
  }, []);

  return (
    <div className="page">
      <header className="pageHeader">
        <div>
          <p className="eyebrow">Trade review</p>
          <h1>Journal</h1>
          <p className="subtle">청산된 거래의 수익률, 점수 변화, 복기 리포트를 확인합니다.</p>
        </div>
      </header>
      {error ? <div className="panel dangerText">{error}</div> : null}
      <section className="panel">
        <div className="panelHeader">
          <h2>Closed Trades</h2>
        </div>
        {trades.length ? (
          <div className="grid">
            {trades.map((trade) => (
              <article className="panel" key={trade.id}>
                <div className="panelHeader">
                  <h3>{trade.symbol}</h3>
                  <strong className={trade.pnl_percent >= 0 ? "successText" : "dangerText"}>{signedPercent(trade.pnl_percent)}</strong>
                </div>
                <p className="subtle">
                  Entry {formatPrice(trade.entry_price)} / Exit {formatPrice(trade.exit_price)} / PnL {trade.pnl_amount} USDT
                </p>
                <p className="reviewText">{trade.review_text}</p>
              </article>
            ))}
          </div>
        ) : (
          <div className="empty">No closed trades yet</div>
        )}
      </section>
    </div>
  );
}

