"use client";

import Link from "next/link";
import { RefreshCw, ShieldAlert } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { api, type MarketSummary } from "@/lib/api";
import { formatPrice, scoreTone, signedPercent } from "@/lib/format";

export function DashboardShell() {
  const [summary, setSummary] = useState<MarketSummary | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  async function load() {
    setLoading(true);
    setError("");
    try {
      setSummary(await api.summary());
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load summary");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    void load();
  }, []);

  const topCandidates = useMemo(
    () => [...(summary?.reports ?? [])].sort((a, b) => b.entry_score - a.entry_score).slice(0, 5),
    [summary]
  );
  const fomoWarnings = useMemo(() => (summary?.reports ?? []).filter((report) => report.scores.fomo >= 70), [summary]);

  return (
    <div className="page">
      <header className="pageHeader">
        <div>
          <p className="eyebrow">Read-only decision cockpit</p>
          <h1>FOMO Control Engine</h1>
          <p className="subtle">진입 전 점수화, 보유 중 모니터링, 청산 후 복기까지 한 화면에서 추적합니다.</p>
        </div>
        <button className="button secondary" onClick={load} disabled={loading}>
          <RefreshCw size={16} />
          {loading ? "Loading" : "Refresh"}
        </button>
      </header>

      {error ? <div className="panel dangerText">{error}</div> : null}

      <section className="grid three">
        <div className="panel">
          <div className="panelHeader">
            <h2>Data Source</h2>
          </div>
          <strong>{summary?.market_data_provider ?? "..."}</strong>
          <p className="subtle">환경변수로 mock / bitget 전환</p>
        </div>
        <div className="panel">
          <div className="panelHeader">
            <h2>Top Score</h2>
          </div>
          <strong>{topCandidates[0]?.entry_score ?? "-"}/100</strong>
          <p className="subtle">{topCandidates[0]?.symbol ?? "No report yet"}</p>
        </div>
        <div className="panel">
          <div className="panelHeader">
            <h2>FOMO Warnings</h2>
            <ShieldAlert size={18} />
          </div>
          <strong>{fomoWarnings.length}</strong>
          <p className="subtle">FOMO Index 70 이상</p>
        </div>
      </section>

      <section className="grid two">
        <div className="panel">
          <div className="panelHeader">
            <h2>Market Watch</h2>
            <span className="subtle">4H {summary?.market_data_provider ?? "feed"}</span>
          </div>
          {loading && !summary ? (
            <div className="empty">Loading market reports...</div>
          ) : summary?.reports.length ? (
            <table className="table">
              <thead>
                <tr>
                  <th>Ticker</th>
                  <th>Price</th>
                  <th>24h</th>
                  <th>Entry Score</th>
                  <th>FOMO</th>
                  <th>State</th>
                </tr>
              </thead>
              <tbody>
                {summary.reports.map((report) => (
                  <tr key={report.id}>
                    <td>
                      <Link href={`/dashboard/${report.symbol}`}>
                        <strong>{report.symbol}</strong>
                      </Link>
                    </td>
                    <td>{formatPrice(report.price)}</td>
                    <td className={report.change_24h >= 0 ? "successText" : "dangerText"}>{signedPercent(report.change_24h)}</td>
                    <td>
                      <span className={`scorePill ${scoreTone(report.entry_score)}`}>{report.entry_score}</span>
                    </td>
                    <td>{report.scores.fomo}</td>
                    <td>{report.state_label}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          ) : (
            <div className="empty">No reports available</div>
          )}
        </div>

        <div className="panel">
          <div className="panelHeader">
            <h2>Highest Score Report</h2>
          </div>
          {topCandidates[0] ? (
            <p className="reportText">{topCandidates[0].report}</p>
          ) : (
            <div className="empty">{loading ? "Loading reports..." : "No report available"}</div>
          )}
        </div>
      </section>
    </div>
  );
}
