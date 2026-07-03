"use client";

import Link from "next/link";
import { RefreshCw } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { TerminalMetric, TerminalPanel, TerminalScoreBadge, TerminalTable, TerminalWarning } from "@/components/terminal";
import { api, type MarketSummary, type Report } from "@/lib/api";
import { formatPrice, signedPercent } from "@/lib/format";

export function MarketsShell() {
  const [summary, setSummary] = useState<MarketSummary | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  async function load() {
    setLoading(true);
    setError("");
    try {
      setSummary(await api.summary());
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load markets");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    void load();
  }, []);

  const reports = useMemo(() => summary?.reports ?? [], [summary]);
  const top = useMemo(() => [...reports].sort((a, b) => b.entry_score - a.entry_score)[0], [reports]);
  const lowestFomo = useMemo(() => [...reports].sort((a, b) => a.scores.fomo - b.scores.fomo)[0], [reports]);
  const highRisk = useMemo(() => reports.filter((report) => report.scores.risk >= 70).length, [reports]);

  return (
    <div className="page">
      <header className="pageHeader">
        <div>
          <p className="eyebrow">Market monitor</p>
          <h1>Markets</h1>
          <p className="subtle">Entry Score, FOMO, risk, data quality 기준으로 기본 심볼을 빠르게 비교합니다.</p>
        </div>
        <button className="button secondary" onClick={load} disabled={loading}>
          <RefreshCw size={16} />
          Refresh
        </button>
      </header>

      {error ? <TerminalWarning tone="error">{error}</TerminalWarning> : null}

      <section className="grid four">
        <TerminalMetric label="Reports" value={reports.length} tone="info" />
        <TerminalMetric label="Top Entry" value={top?.symbol ?? "-"} delta={top ? `${top.entry_score}/100` : "No report"} tone="positive" />
        <TerminalMetric label="Lowest FOMO" value={lowestFomo?.symbol ?? "-"} delta={lowestFomo ? `${lowestFomo.scores.fomo}/100` : "No report"} tone="neutral" />
        <TerminalMetric label="High Risk" value={highRisk} delta="Risk >= 70" tone={highRisk ? "negative" : "neutral"} />
      </section>

      <TerminalPanel title="Market Watchlist" subtitle={`${summary?.market_data_provider ?? "provider"} feed · 4H snapshot`} status={reports.length ? "ok" : "neutral"}>
        {loading ? (
          <div className="terminalEmpty">Loading markets...</div>
        ) : (
          <TerminalTable<Report>
            data={reports}
            idKey="id"
            emptyLabel="No reports available"
            columns={[
              {
                key: "symbol",
                header: "Ticker",
                width: 120,
                render: (report) => (
                  <Link href={`/markets/${report.symbol}`}>
                    <strong>{report.symbol}</strong>
                  </Link>
                )
              },
              { key: "price", header: "Price", align: "end", render: (report) => formatPrice(report.price) },
              { key: "change_24h", header: "24H", align: "end", render: (report) => <span className={report.change_24h >= 0 ? "successText" : "dangerText"}>{signedPercent(report.change_24h)}</span> },
              { key: "entry_score", header: "Entry", align: "center", render: (report) => <TerminalScoreBadge score={report.entry_score} type="entry" /> },
              { key: "structure", header: "Struct", align: "center", render: (report) => report.scores.structure },
              { key: "volume", header: "Vol", align: "center", render: (report) => report.scores.volume },
              { key: "liquidity", header: "Liq", align: "center", render: (report) => report.scores.liquidity },
              { key: "momentum", header: "Mom", align: "center", render: (report) => report.scores.momentum },
              { key: "risk", header: "Risk", align: "center", render: (report) => <TerminalScoreBadge score={report.scores.risk} type="risk" /> },
              { key: "fomo", header: "FOMO", align: "center", render: (report) => <TerminalScoreBadge score={report.scores.fomo} type="fomo" /> },
              { key: "state_label", header: "State", render: (report) => report.state_label },
              { key: "created_at", header: "Updated", render: (report) => new Date(report.created_at).toLocaleString() }
            ]}
          />
        )}
      </TerminalPanel>
    </div>
  );
}
