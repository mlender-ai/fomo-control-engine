"use client";

import { RefreshCw } from "lucide-react";
import { useEffect, useState } from "react";
import { ScoreBreakdownView } from "@/components/score-breakdown";
import { api, type Report } from "@/lib/api";
import { formatPrice, scoreTone, signedPercent } from "@/lib/format";

export function TickerDetail({ symbol }: { symbol: string }) {
  const [report, setReport] = useState<Report | null>(null);
  const [error, setError] = useState("");

  async function load(create = false) {
    setError("");
    try {
      setReport(create ? await api.createReport(symbol) : await api.report(symbol));
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load report");
    }
  }

  useEffect(() => {
    void load(false);
  }, [symbol]);

  return (
    <div className="page">
      <header className="pageHeader">
        <div>
          <p className="eyebrow">Ticker detail</p>
          <h1>{symbol}</h1>
          <p className="subtle">Entry Opportunity Score와 점수 구성 근거를 확인합니다.</p>
        </div>
        <button className="button secondary" onClick={() => load(true)}>
          <RefreshCw size={16} />
          New Report
        </button>
      </header>

      {error ? <div className="panel dangerText">{error}</div> : null}
      {report ? (
        <>
          <section className="grid three">
            <div className="panel">
              <p className="subtle">Current Price</p>
              <h2>{formatPrice(report.price)}</h2>
              <p className={report.change_24h >= 0 ? "successText" : "dangerText"}>{signedPercent(report.change_24h)}</p>
            </div>
            <div className="panel">
              <p className="subtle">Entry Score</p>
              <span className={`scorePill ${scoreTone(report.entry_score)}`}>{report.entry_score}/100</span>
            </div>
            <div className="panel">
              <p className="subtle">FOMO Index</p>
              <span className={`scorePill ${scoreTone(100 - report.scores.fomo)}`}>{report.scores.fomo}/100</span>
            </div>
          </section>
          <section className="panel">
            <div className="panelHeader">
              <h2>Score Breakdown</h2>
              <span className="subtle">{report.state_label}</span>
            </div>
            <ScoreBreakdownView scores={report.scores} />
          </section>
          <section className="panel">
            <div className="panelHeader">
              <h2>Data Quality</h2>
              <span className="subtle">Data Source: {report.provider === "bitget" ? "Bitget Live" : report.provider}</span>
            </div>
            <div className="statusGrid">
              <div className={`statusItem ${report.data_quality.ohlcv_ok ? "ok" : "error"}`}>
                <span>OHLCV</span>
                <strong>{report.data_quality.ohlcv_ok ? "OK" : "Error"}</strong>
              </div>
              <div className={`statusItem ${report.data_quality.funding_ok ? "ok" : "warn"}`}>
                <span>Funding</span>
                <strong>{report.data_quality.funding_ok ? "OK" : "Missing"}</strong>
              </div>
              <div className={`statusItem ${report.data_quality.open_interest_ok ? "ok" : "warn"}`}>
                <span>Open Interest</span>
                <strong>{report.data_quality.open_interest_ok ? "OK" : "Missing"}</strong>
              </div>
              <div className="statusItem muted">
                <span>Candles</span>
                <strong>{report.data_quality.candles}</strong>
              </div>
              <div className="statusItem muted">
                <span>Timeframe</span>
                <strong>{report.timeframe.toUpperCase()}</strong>
              </div>
              <div className="statusItem muted">
                <span>Last Candle</span>
                <strong>{report.data_quality.last_candle_at ? new Date(report.data_quality.last_candle_at).toLocaleString() : "-"}</strong>
              </div>
            </div>
          </section>
          <section className="grid two">
            <div className="panel">
              <div className="panelHeader">
                <h2>Report</h2>
              </div>
              <p className="reportText">{report.report}</p>
            </div>
            <div className="panel">
              <div className="panelHeader">
                <h2>Raw JSON</h2>
              </div>
              <pre className="reportText">{JSON.stringify(report.raw_json, null, 2)}</pre>
            </div>
          </section>
        </>
      ) : (
        <div className="empty">Loading report...</div>
      )}
    </div>
  );
}
