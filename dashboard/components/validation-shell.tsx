"use client";

import { Play, RefreshCw } from "lucide-react";
import { useEffect, useState } from "react";
import { TerminalMetric, TerminalPanel, TerminalRawJson, TerminalTable, TerminalWarning } from "@/components/terminal";
import { api, type ValidationRun } from "@/lib/api";

export function ValidationShell() {
  const [runs, setRuns] = useState<ValidationRun[]>([]);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const [running, setRunning] = useState(false);

  async function load() {
    setLoading(true);
    setError("");
    try {
      setRuns((await api.validationRuns()).validation_runs);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load validation runs");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    void load();
  }, []);

  async function runValidation() {
    setError("");
    setRunning(true);
    try {
      await api.runValidation({ symbol: "BTCUSDT", timeframe: "4h" });
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to run validation");
    } finally {
      setRunning(false);
    }
  }

  const latest = runs[0];

  return (
    <div className="page">
      <header className="pageHeader">
        <div>
          <p className="eyebrow">Validation Lab</p>
          <h1>Validation</h1>
          <p className="subtle">Monte Carlo, Bootstrap Sharpe CI, Walk Forward로 Entry Score 전략의 과최적화 위험을 점검합니다.</p>
        </div>
        <div className="actionGroup">
          <button className="button secondary" onClick={load} disabled={loading}>
            <RefreshCw size={16} />
            Refresh
          </button>
          <button className="button" onClick={runValidation} disabled={running}>
            <Play size={16} />
            {running ? "Running" : "Run Validation"}
          </button>
        </div>
      </header>

      {error ? <TerminalWarning tone="error">{error}</TerminalWarning> : null}

      <section className="grid four">
        <TerminalMetric label="Runs" value={runs.length} tone="info" />
        <TerminalMetric label="Latest Strategy" value={latest?.strategy_type ?? "-"} tone="agent" mono={false} />
        <TerminalMetric label="Latest Sharpe" value={formatNumber(latest?.summary.sharpe)} tone={Number(latest?.summary.sharpe ?? 0) > 0 ? "positive" : "neutral"} />
        <TerminalMetric label="Warnings" value={latest?.warnings.length ?? 0} tone={(latest?.warnings.length ?? 0) ? "warning" : "neutral"} />
      </section>

      <TerminalPanel title="Validation Runs" subtitle="Deterministic seed and stored result payload" status={runs.length ? "ok" : "neutral"}>
        {loading ? (
          <div className="terminalEmpty">Loading validation runs...</div>
        ) : (
          <TerminalTable<ValidationRun>
            data={runs}
            idKey="id"
            emptyLabel="No validation runs yet"
            columns={[
              { key: "symbol", header: "Symbol", width: 110, render: (run) => <strong>{run.symbol}</strong> },
              { key: "timeframe", header: "TF", width: 70, render: (run) => run.timeframe.toUpperCase() },
              { key: "strategy_type", header: "Strategy", render: (run) => run.strategy_type },
              { key: "total_trades", header: "Trades", align: "end", render: (run) => formatNumber(run.summary.total_trades) },
              { key: "win_rate", header: "Win", align: "end", render: (run) => formatNumber(run.summary.win_rate) },
              { key: "profit_factor", header: "PF", align: "end", render: (run) => formatNumber(run.summary.profit_factor) },
              { key: "sharpe", header: "Sharpe", align: "end", render: (run) => formatNumber(run.summary.sharpe) },
              { key: "max_drawdown", header: "MDD", align: "end", render: (run) => formatNumber(run.summary.max_drawdown) },
              { key: "warnings", header: "Warn", align: "end", render: (run) => run.warnings.length },
              { key: "created_at", header: "Created", render: (run) => new Date(run.created_at).toLocaleString() }
            ]}
          />
        )}
      </TerminalPanel>

      {latest ? (
        <section className="grid two">
          <TerminalPanel title="Latest Warnings" subtitle={`${latest.symbol} · ${latest.strategy_type}`} status={latest.warnings.length ? "warning" : "ok"}>
            {latest.warnings.length ? (
              <div className="grid">
                {latest.warnings.map((warning) => (
                  <TerminalWarning key={warning} tone="warning">{warning}</TerminalWarning>
                ))}
              </div>
            ) : (
              <div className="terminalEmpty">No validation warnings on latest run</div>
            )}
          </TerminalPanel>
          <TerminalPanel title="Latest Raw Results" subtitle="Monte Carlo, bootstrap, walk-forward payload" status="neutral">
            <TerminalRawJson data={{ params: latest.params, summary: latest.summary, results: latest.results }} label={`${latest.symbol} validation`} />
          </TerminalPanel>
        </section>
      ) : null}
    </div>
  );
}

function formatNumber(value: number | undefined) {
  if (value === undefined || Number.isNaN(value)) return "-";
  return Number.isInteger(value) ? String(value) : value.toFixed(3);
}
