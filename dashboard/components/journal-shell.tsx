"use client";

import { useEffect, useMemo, useState } from "react";
import { TerminalMetric, TerminalPanel, TerminalTable, TerminalWarning } from "@/components/terminal";
import { api, type Trade } from "@/lib/api";
import { formatPrice, signedPercent } from "@/lib/format";

export function JournalShell() {
  const [trades, setTrades] = useState<Trade[]>([]);
  const [selectedTradeId, setSelectedTradeId] = useState("");
  const [error, setError] = useState("");

  useEffect(() => {
    api.trades()
      .then((nextTrades) => {
        setTrades(nextTrades);
        setSelectedTradeId(nextTrades[0]?.id ?? "");
      })
      .catch((err) => setError(err instanceof Error ? err.message : "Failed to load journal"));
  }, []);

  const selectedTrade = trades.find((trade) => trade.id === selectedTradeId) ?? trades[0];
  const winRate = useMemo(() => {
    if (!trades.length) return 0;
    return (trades.filter((trade) => trade.pnl_percent > 0).length / trades.length) * 100;
  }, [trades]);
  const averagePnl = useMemo(() => {
    if (!trades.length) return 0;
    return trades.reduce((sum, trade) => sum + trade.pnl_percent, 0) / trades.length;
  }, [trades]);
  const totalPnl = useMemo(() => trades.reduce((sum, trade) => sum + trade.pnl_amount, 0), [trades]);

  return (
    <div className="page">
      <header className="pageHeader">
        <div>
          <p className="eyebrow">Trade review</p>
          <h1>Journal</h1>
          <p className="subtle">청산된 거래의 점수 변화, 손익, 복기 리포트를 Shadow Account 입력으로 관리합니다.</p>
        </div>
      </header>

      {error ? <TerminalWarning tone="error">{error}</TerminalWarning> : null}

      <section className="grid four">
        <TerminalMetric label="Closed Trades" value={trades.length} tone="info" />
        <TerminalMetric label="Win Rate" value={`${winRate.toFixed(1)}%`} tone={winRate >= 50 ? "positive" : "warning"} />
        <TerminalMetric label="Average PnL" value={signedPercent(averagePnl)} tone={averagePnl >= 0 ? "positive" : "negative"} />
        <TerminalMetric label="Total PnL" value={`${totalPnl.toFixed(2)} USDT`} tone={totalPnl >= 0 ? "positive" : "negative"} />
      </section>

      <section className="grid two">
        <TerminalPanel title="Closed Trades" subtitle="Click a row to inspect the review text" status={trades.length ? "ok" : "neutral"}>
          <TerminalTable<Trade>
            data={trades}
            idKey="id"
            emptyLabel="No closed trades yet"
            columns={[
              {
                key: "symbol",
                header: "Symbol",
                width: 112,
                render: (trade) => (
                  <button className="terminalDisclosure" type="button" onClick={() => setSelectedTradeId(trade.id)}>
                    {trade.symbol}
                  </button>
                )
              },
              { key: "direction", header: "Side", width: 76, render: (trade) => trade.direction.toUpperCase() },
              { key: "entry_price", header: "Entry", align: "end", render: (trade) => formatPrice(trade.entry_price) },
              { key: "exit_price", header: "Exit", align: "end", render: (trade) => formatPrice(trade.exit_price) },
              { key: "pnl_percent", header: "PnL", align: "end", render: (trade) => <span className={trade.pnl_percent >= 0 ? "successText" : "dangerText"}>{signedPercent(trade.pnl_percent)}</span> },
              { key: "score", header: "Score", render: (trade) => `${trade.entry_score ?? "-"} -> ${trade.exit_score ?? "-"}` },
              { key: "holding_minutes", header: "Hold", align: "end", render: (trade) => `${trade.holding_minutes}m` },
              { key: "created_at", header: "Created", render: (trade) => new Date(trade.created_at).toLocaleString() }
            ]}
          />
        </TerminalPanel>

        <TerminalPanel title="Selected Review" subtitle={selectedTrade ? `${selectedTrade.symbol} · ${selectedTrade.exit_reason}` : "No trade selected"} status={selectedTrade?.pnl_percent && selectedTrade.pnl_percent < 0 ? "warning" : "neutral"}>
          {selectedTrade ? (
            <div className="grid">
              <div className="terminalMetricGrid">
                <TerminalMetric label="Entry" value={formatPrice(selectedTrade.entry_price)} tone="neutral" />
                <TerminalMetric label="Exit" value={formatPrice(selectedTrade.exit_price)} tone="neutral" />
                <TerminalMetric label="PnL" value={signedPercent(selectedTrade.pnl_percent)} tone={selectedTrade.pnl_percent >= 0 ? "positive" : "negative"} />
                <TerminalMetric label="Amount" value={`${selectedTrade.pnl_amount.toFixed(2)} USDT`} tone={selectedTrade.pnl_amount >= 0 ? "positive" : "negative"} />
              </div>
              <p className="reviewText">{selectedTrade.review_text || "No review text recorded."}</p>
            </div>
          ) : (
            <div className="terminalEmpty">No closed trades yet</div>
          )}
        </TerminalPanel>
      </section>
    </div>
  );
}
