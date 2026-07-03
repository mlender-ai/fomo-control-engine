"use client";

import Link from "next/link";
import { RefreshCw, ShieldAlert, TestTube2, UploadCloud } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import {
  TerminalMetric,
  TerminalPanel,
  TerminalScoreBadge,
  TerminalTable,
  TerminalWarning
} from "@/components/terminal";
import {
  api,
  type BitgetConnectionTest,
  type MarketSummary,
  type Position,
  type Report,
  type ResearchRun,
  type SystemStatus
} from "@/lib/api";
import { formatPrice, signedPercent } from "@/lib/format";

export function DashboardShell() {
  const [summary, setSummary] = useState<MarketSummary | null>(null);
  const [status, setStatus] = useState<SystemStatus | null>(null);
  const [researchRuns, setResearchRuns] = useState<ResearchRun[]>([]);
  const [connectionTest, setConnectionTest] = useState<BitgetConnectionTest | null>(null);
  const [actionMessage, setActionMessage] = useState("");
  const [loading, setLoading] = useState(true);
  const [actionLoading, setActionLoading] = useState("");
  const [error, setError] = useState("");

  async function load() {
    setLoading(true);
    setError("");
    try {
      const [nextStatus, nextSummary, researchResult] = await Promise.all([
        api.systemStatus(),
        api.summary(),
        api.researchRuns().catch(() => ({ research_runs: [] }))
      ]);
      setStatus(nextStatus);
      setSummary(nextSummary);
      setResearchRuns(researchResult.research_runs);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load summary");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    void load();
  }, []);

  const reports = useMemo(() => summary?.reports ?? [], [summary]);
  const openPositions = useMemo(() => (summary?.positions ?? []).filter((position) => position.status === "open"), [summary]);
  const topCandidate = useMemo(() => [...reports].sort((a, b) => b.entry_score - a.entry_score)[0], [reports]);
  const fomoWarnings = useMemo(() => reports.filter((report) => report.scores.fomo >= 70), [reports]);
  const riskWarnings = useMemo(() => reports.filter((report) => report.scores.risk >= 70), [reports]);
  const blockedReports = useMemo(() => reports.filter((report) => report.entry_score < 65 || report.scores.fomo >= 75), [reports]);

  async function testConnection() {
    setActionLoading("test");
    setActionMessage("");
    setError("");
    try {
      const result = await api.testBitgetConnection();
      setConnectionTest(result);
      setActionMessage(`Bitget public ${result.public_market_data.ok ? "OK" : "ERROR"} · private ${result.private_positions.status}`);
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
      setActionMessage(`Position sync ${result.status}: created ${result.created}, updated ${result.updated}, missing ${result.missing_from_exchange}`);
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
          <p className="subtle">실시간 데이터, 진입 점수, 리서치 런, 포지션과 저널을 한 화면에서 빠르게 검토합니다.</p>
        </div>
        <button className="button secondary" onClick={load} disabled={loading}>
          <RefreshCw size={16} />
          {loading ? "Loading" : "Refresh"}
        </button>
      </header>

      {error ? <TerminalWarning tone="error">{error}</TerminalWarning> : null}
      {actionMessage ? <TerminalWarning tone="info">{actionMessage}</TerminalWarning> : null}

      <TerminalPanel
        title="Connection Matrix"
        subtitle="Mock / Bitget provider boundary, public market data, private read-only position sync"
        status={status?.database === "ok" ? "ok" : "warning"}
        actions={
          <>
            <button className="button secondary" onClick={testConnection} disabled={actionLoading === "test"}>
              <TestTube2 size={16} />
              Test Bitget
            </button>
            <button className="button secondary" onClick={syncPositions} disabled={actionLoading === "sync"}>
              <UploadCloud size={16} />
              Sync Positions
            </button>
          </>
        }
      >
        <div className="statusGrid">
          <StatusItem label="Provider" value={status?.market_data_provider ?? summary?.market_data_provider ?? "..."} tone={status?.market_data_provider === "bitget" ? "ok" : "warn"} />
          <StatusItem label="Database" value={status?.database ?? "..."} tone={status?.database === "ok" ? "ok" : "error"} />
          <StatusItem label="Public API" value={connectionTest?.public_market_data.ok ? "ok" : status?.bitget_public_api ?? "..."} tone={connectionTest?.public_market_data.ok || status?.bitget_public_api === "ok" ? "ok" : "muted"} />
          <StatusItem label="Private API" value={connectionTest?.private_positions.status ?? status?.bitget_private_api ?? "..."} tone={statusTone(connectionTest?.private_positions.status ?? status?.bitget_private_api)} />
          <StatusItem label="Candles" value={String(connectionTest?.public_market_data.candles ?? topCandidate?.data_quality.candles ?? "-")} tone="muted" />
          <StatusItem label="Timestamp" value={status?.timestamp ? new Date(status.timestamp).toLocaleTimeString() : "-"} tone="muted" />
        </div>
      </TerminalPanel>

      <section className="grid four">
        <TerminalMetric label="Top Entry Score" value={topCandidate ? `${topCandidate.entry_score}/100` : "-"} delta={topCandidate?.symbol ?? "No report"} tone="info" />
        <TerminalMetric label="FOMO Warnings" value={fomoWarnings.length} delta="FOMO Index >= 70" tone={fomoWarnings.length ? "warning" : "neutral"} />
        <TerminalMetric label="Risk Spikes" value={riskWarnings.length} delta="Risk score >= 70" tone={riskWarnings.length ? "negative" : "neutral"} />
        <TerminalMetric label="Open Positions" value={openPositions.length} delta={summary?.market_data_provider ?? "provider"} tone="positive" />
      </section>

      <section className="grid two">
        <TerminalPanel title="Market Monitor" subtitle="4H deterministic report snapshot" status={reports.length ? "ok" : "neutral"}>
          {loading && !summary ? (
            <div className="terminalEmpty">Loading market reports...</div>
          ) : (
            <TerminalTable<Report>
              data={reports}
              idKey="id"
              emptyLabel="No reports available"
              columns={[
                {
                  key: "symbol",
                  header: "Ticker",
                  width: 116,
                  render: (report) => (
                    <Link href={`/dashboard/${report.symbol}`}>
                      <strong>{report.symbol}</strong>
                    </Link>
                  )
                },
                { key: "price", header: "Price", align: "end", render: (report) => formatPrice(report.price) },
                {
                  key: "change_24h",
                  header: "24H",
                  align: "end",
                  render: (report) => (
                    <span className={report.change_24h >= 0 ? "successText" : "dangerText"}>{signedPercent(report.change_24h)}</span>
                  )
                },
                { key: "entry_score", header: "Entry", align: "center", render: (report) => <TerminalScoreBadge score={report.entry_score} type="entry" /> },
                { key: "fomo", header: "FOMO", align: "center", render: (report) => <TerminalScoreBadge score={report.scores.fomo} type="fomo" /> },
                { key: "risk", header: "Risk", align: "center", render: (report) => <TerminalScoreBadge score={report.scores.risk} type="risk" /> },
                { key: "state_label", header: "State", render: (report) => report.state_label },
                { key: "provider", header: "Feed", width: 92, render: (report) => report.provider.toUpperCase() }
              ]}
            />
          )}
        </TerminalPanel>

        <TerminalPanel title="FOMO Gate" subtitle="Score is deterministic; text explains the JSON only" status={blockedReports.length ? "warning" : "ok"}>
          <div className="terminalMetricGrid">
            <TerminalMetric label="Candidate" value={topCandidate?.symbol ?? "-"} delta={topCandidate ? `${topCandidate.state_label}` : "No signal"} tone="info" />
            <TerminalMetric label="Entry" value={topCandidate?.entry_score ?? "-"} tone="positive" />
            <TerminalMetric label="FOMO" value={topCandidate?.scores.fomo ?? "-"} tone={(topCandidate?.scores.fomo ?? 0) >= 70 ? "warning" : "neutral"} />
            <TerminalMetric label="Risk" value={topCandidate?.scores.risk ?? "-"} tone={(topCandidate?.scores.risk ?? 0) >= 70 ? "negative" : "neutral"} />
            <TerminalMetric label="Liquidity" value={topCandidate?.scores.liquidity ?? "-"} tone="info" />
          </div>
          {topCandidate ? <p className="reportText">{topCandidate.report}</p> : <div className="terminalEmpty">No report selected</div>}
        </TerminalPanel>
      </section>

      <section className="grid two">
        <TerminalPanel title="Open Position Monitor" subtitle="Read-only exchange sync plus local manual entries" status={openPositions.length ? "warning" : "neutral"}>
          <TerminalTable<Position>
            data={openPositions}
            idKey="id"
            emptyLabel="No open positions"
            columns={[
              { key: "symbol", header: "Ticker", width: 110, render: (position) => <strong>{position.symbol}</strong> },
              { key: "direction", header: "Side", width: 80, render: (position) => position.direction.toUpperCase() },
              { key: "entry_price", header: "Entry", align: "end", render: (position) => formatPrice(position.entry_price) },
              { key: "mark_price", header: "Mark", align: "end", render: (position) => position.mark_price ? formatPrice(position.mark_price) : position.current_price ? formatPrice(position.current_price) : "-" },
              { key: "pnl_percent", header: "PnL", align: "end", render: (position) => <span className={position.pnl_percent >= 0 ? "successText" : "dangerText"}>{signedPercent(position.pnl_percent)}</span> },
              { key: "score", header: "Score", render: (position) => `${position.entry_score ?? "-"} -> ${position.current_score ?? "-"}` },
              { key: "source", header: "Source", render: (position) => position.source }
            ]}
          />
        </TerminalPanel>

        <TerminalPanel title="Research Run Tape" subtitle="Bull/Bear/Risk/Gatekeeper records" status={researchRuns.length ? "ok" : "neutral"}>
          <TerminalTable<ResearchRun>
            data={researchRuns.slice(0, 8)}
            idKey="research_run_id"
            emptyLabel="No research runs yet"
            columns={[
              {
                key: "symbol",
                header: "Symbol",
                width: 110,
                render: (run) => (
                  <Link href={`/research/${run.research_run_id}`}>
                    <strong>{run.symbol}</strong>
                  </Link>
                )
              },
              { key: "entry_score", header: "Entry", align: "center", render: (run) => <TerminalScoreBadge score={run.entry_score} type="entry" /> },
              { key: "fomo_index", header: "FOMO", align: "center", render: (run) => <TerminalScoreBadge score={run.fomo_index} type="fomo" /> },
              { key: "final_action_label", header: "Gate", render: (run) => run.final_action_label },
              { key: "created_at", header: "Time", render: (run) => new Date(run.created_at).toLocaleString() }
            ]}
          />
        </TerminalPanel>
      </section>

      <TerminalPanel
        title="Attention Queue"
        subtitle="Warnings are advisory only; no order execution is available in this product"
        status={fomoWarnings.length || riskWarnings.length ? "warning" : "ok"}
        actions={<ShieldAlert size={18} />}
      >
        {fomoWarnings.length || riskWarnings.length ? (
          <div className="grid">
            {[...new Map([...fomoWarnings, ...riskWarnings].map((report) => [report.symbol, report])).values()].map((report) => (
              <TerminalWarning key={report.id} tone={report.scores.risk >= 75 || report.scores.fomo >= 75 ? "error" : "warning"}>
                <strong>{report.symbol}</strong> · Entry {report.entry_score} · FOMO {report.scores.fomo} · Risk {report.scores.risk} · {report.state_label}
              </TerminalWarning>
            ))}
          </div>
        ) : (
          <div className="terminalEmpty">No high FOMO or high risk warnings in the latest snapshot.</div>
        )}
      </TerminalPanel>
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
