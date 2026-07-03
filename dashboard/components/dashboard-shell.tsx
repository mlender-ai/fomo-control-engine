"use client";

import Link from "next/link";
import { RefreshCw, ShieldAlert, TestTube2, UploadCloud } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { api, type BitgetConnectionTest, type MarketSummary, type SystemStatus } from "@/lib/api";
import { formatPrice, scoreTone, signedPercent } from "@/lib/format";

export function DashboardShell() {
  const [summary, setSummary] = useState<MarketSummary | null>(null);
  const [status, setStatus] = useState<SystemStatus | null>(null);
  const [connectionTest, setConnectionTest] = useState<BitgetConnectionTest | null>(null);
  const [actionMessage, setActionMessage] = useState("");
  const [loading, setLoading] = useState(true);
  const [actionLoading, setActionLoading] = useState("");
  const [error, setError] = useState("");

  async function load() {
    setLoading(true);
    setError("");
    try {
      const [nextStatus, nextSummary] = await Promise.all([api.systemStatus(), api.summary()]);
      setStatus(nextStatus);
      setSummary(nextSummary);
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

  async function testConnection() {
    setActionLoading("test");
    setActionMessage("");
    setError("");
    try {
      const result = await api.testBitgetConnection();
      setConnectionTest(result);
      setActionMessage(`Public API ${result.public_market_data.ok ? "OK" : "ERROR"} · Private ${result.private_positions.status}`);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to test Bitget connection");
    } finally {
      setActionLoading("");
    }
  }

  async function syncPositions() {
    setActionLoading("sync");
    setActionMessage("");
    setError("");
    try {
      const result = await api.syncBitgetPositions();
      setActionMessage(`Position sync: ${result.status}, created ${result.created}, updated ${result.updated}`);
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to sync positions");
    } finally {
      setActionLoading("");
    }
  }

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
      {actionMessage ? <div className="panel subtle">{actionMessage}</div> : null}

      <section className="panel">
        <div className="panelHeader">
          <div>
            <h2>API Status</h2>
            <p className="subtle">Provider, public market data, private read-only position sync 상태입니다.</p>
          </div>
          <div className="actionGroup">
            <button className="button secondary" onClick={testConnection} disabled={actionLoading === "test"}>
              <TestTube2 size={16} />
              Test Bitget Connection
            </button>
            <button className="button secondary" onClick={syncPositions} disabled={actionLoading === "sync"}>
              <UploadCloud size={16} />
              Sync Positions
            </button>
          </div>
        </div>
        <div className="statusGrid">
          <StatusItem label="Provider" value={status?.market_data_provider ?? summary?.market_data_provider ?? "..."} tone="ok" />
          <StatusItem label="Database" value={status?.database ?? "..."} tone={status?.database === "ok" ? "ok" : "error"} />
          <StatusItem label="Public API" value={connectionTest?.public_market_data.ok ? "ok" : status?.bitget_public_api ?? "..."} tone={connectionTest?.public_market_data.ok ? "ok" : "muted"} />
          <StatusItem label="Private API" value={connectionTest?.private_positions.status ?? status?.bitget_private_api ?? "..."} tone={statusTone(connectionTest?.private_positions.status ?? status?.bitget_private_api)} />
          <StatusItem label="Candles" value={String(connectionTest?.public_market_data.candles ?? topCandidates[0]?.data_quality.candles ?? "-")} tone="muted" />
          <StatusItem label="Last Sync" value={status?.timestamp ? new Date(status.timestamp).toLocaleString() : "-"} tone="muted" />
        </div>
      </section>

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

function StatusItem({ label, value, tone }: { label: string; value: string; tone: "ok" | "warn" | "error" | "muted" }) {
  return (
    <div className={`statusItem ${tone}`}>
      <span>{label}</span>
      <strong>{value}</strong>
    </div>
  );
}

function statusTone(value: string | undefined): "ok" | "warn" | "error" | "muted" {
  if (!value || value === "not_configured" || value === "not_active" || value === "configured") return "muted";
  if (value === "ok") return "ok";
  if (value === "permission_error") return "warn";
  return "error";
}
