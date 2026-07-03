"use client";

import { Play, RefreshCw } from "lucide-react";
import { useEffect, useState } from "react";
import { api, type ValidationRun } from "@/lib/api";

export function ValidationShell() {
  const [runs, setRuns] = useState<ValidationRun[]>([]);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);

  async function load() {
    setLoading(true);
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
    try {
      await api.runValidation({ symbol: "BTCUSDT", timeframe: "4h" });
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to run validation");
    }
  }

  return (
    <div className="page">
      <header className="pageHeader">
        <div>
          <p className="eyebrow">Validation Lab</p>
          <h1>Validation</h1>
          <p className="subtle">Monte Carlo, Bootstrap Sharpe CI, Walk Forward로 과최적화 위험을 점검합니다.</p>
        </div>
        <div className="actionGroup">
          <button className="button secondary" onClick={load} disabled={loading}><RefreshCw size={16} />Refresh</button>
          <button className="button" onClick={runValidation}><Play size={16} />Run Validation</button>
        </div>
      </header>
      {error ? <div className="panel dangerText">{error}</div> : null}
      {runs.length ? (
        <div className="grid">
          {runs.map((run) => (
            <article className="panel" key={run.id}>
              <div className="panelHeader">
                <h2>{run.symbol} · {run.strategy_type}</h2>
                <span className="subtle">{new Date(run.created_at).toLocaleString()}</span>
              </div>
              <div className="metricGrid">
                <div className="metric"><span>Trades</span><strong>{run.summary.total_trades ?? 0}</strong></div>
                <div className="metric"><span>Win Rate</span><strong>{run.summary.win_rate ?? 0}</strong></div>
                <div className="metric"><span>Profit Factor</span><strong>{run.summary.profit_factor ?? 0}</strong></div>
                <div className="metric"><span>Sharpe</span><strong>{run.summary.sharpe ?? 0}</strong></div>
                <div className="metric"><span>MDD</span><strong>{run.summary.max_drawdown ?? 0}</strong></div>
              </div>
              <p className="reportText">{run.warnings.length ? run.warnings.join("\n") : "No warnings"}</p>
            </article>
          ))}
        </div>
      ) : (
        <div className="empty">{loading ? "Loading validation runs..." : "No validation runs yet"}</div>
      )}
    </div>
  );
}
