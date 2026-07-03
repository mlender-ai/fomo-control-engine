"use client";

import { RefreshCw } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { ScoreBreakdownView } from "@/components/score-breakdown";
import {
  TerminalMetric,
  TerminalPanel,
  TerminalRawJson,
  TerminalScoreBadge,
  TerminalWarning
} from "@/components/terminal";
import { api, type Report } from "@/lib/api";
import { formatPrice, signedPercent } from "@/lib/format";

export function TickerDetail({ symbol }: { symbol: string }) {
  const [report, setReport] = useState<Report | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  const load = useCallback(async (create = false) => {
    setLoading(true);
    setError("");
    try {
      setReport(create ? await api.createReport(symbol) : await api.report(symbol));
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load report");
    } finally {
      setLoading(false);
    }
  }, [symbol]);

  useEffect(() => {
    void load(false);
  }, [load]);

  return (
    <div className="page">
      <header className="pageHeader">
        <div>
          <p className="eyebrow">Ticker detail</p>
          <h1>{symbol}</h1>
          <p className="subtle">Entry Opportunity Score, FOMO Index, risk score, data quality를 같은 snapshot 기준으로 확인합니다.</p>
        </div>
        <button className="button secondary" onClick={() => load(true)} disabled={loading}>
          <RefreshCw size={16} />
          New Report
        </button>
      </header>

      {error ? <TerminalWarning tone="error">{error}</TerminalWarning> : null}

      {report ? (
        <>
          <section className="grid four">
            <TerminalMetric label="Current Price" value={formatPrice(report.price)} delta={signedPercent(report.change_24h)} tone={report.change_24h >= 0 ? "positive" : "negative"} />
            <TerminalMetric label="Entry Score" value={`${report.entry_score}/100`} delta={report.state_label} tone="info" />
            <TerminalMetric label="FOMO Index" value={`${report.scores.fomo}/100`} delta={report.scores.fomo >= 70 ? "Chase risk" : "Controlled"} tone={report.scores.fomo >= 70 ? "warning" : "neutral"} />
            <TerminalMetric label="Risk Score" value={`${report.scores.risk}/100`} delta={report.provider.toUpperCase()} tone={report.scores.risk >= 70 ? "negative" : "neutral"} />
          </section>

          <section className="grid two">
            <TerminalPanel title="Score Breakdown" subtitle="LLM does not calculate this score" status="ok" actions={<TerminalScoreBadge score={report.entry_score} type="entry" />}>
              <ScoreBreakdownView scores={report.scores} />
            </TerminalPanel>

            <TerminalPanel title="Data Quality" subtitle={`Data source: ${report.provider === "bitget" ? "Bitget Live" : report.provider}`} status={qualityStatus(report)}>
              <div className="statusGrid">
                <QualityItem label="OHLCV" ok={report.data_quality.ohlcv_ok} />
                <QualityItem label="Funding" ok={report.data_quality.funding_ok} warnOnly />
                <QualityItem label="Open Interest" ok={report.data_quality.open_interest_ok} warnOnly />
                <StatusItem label="Candles" value={String(report.data_quality.candles)} />
                <StatusItem label="Timeframe" value={report.timeframe.toUpperCase()} />
                <StatusItem label="Last Candle" value={report.data_quality.last_candle_at ? new Date(report.data_quality.last_candle_at).toLocaleString() : "-"} />
              </div>
            </TerminalPanel>
          </section>

          <section className="grid two">
            <TerminalPanel title="Report" subtitle="Natural-language explanation of the deterministic score JSON" status={report.entry_score >= 75 ? "ok" : "warning"}>
              <p className="reportText">{report.report}</p>
            </TerminalPanel>
            <TerminalPanel title="Snapshot JSON" subtitle="Raw data for reproducibility and audit" status="neutral">
              <TerminalRawJson data={{ report: report.raw_json, data_quality: report.data_quality }} label={`${symbol} snapshot`} />
            </TerminalPanel>
          </section>
        </>
      ) : (
        <div className="terminalEmpty">{loading ? "Loading report..." : "No report available"}</div>
      )}
    </div>
  );
}

function QualityItem({ label, ok, warnOnly = false }: { label: string; ok: boolean; warnOnly?: boolean }) {
  return (
    <div className={`statusItem ${ok ? "ok" : warnOnly ? "warn" : "error"}`}>
      <span>{label}</span>
      <strong>{ok ? "OK" : warnOnly ? "Missing" : "Error"}</strong>
    </div>
  );
}

function StatusItem({ label, value }: { label: string; value: string }) {
  return (
    <div className="statusItem muted">
      <span>{label}</span>
      <strong>{value}</strong>
    </div>
  );
}

function qualityStatus(report: Report): "ok" | "warning" | "error" {
  if (!report.data_quality.ohlcv_ok || !report.data_quality.min_candles_met) return "error";
  if (!report.data_quality.funding_ok || !report.data_quality.open_interest_ok || report.data_quality.fallback_used) return "warning";
  return "ok";
}
