"use client";

import { RefreshCw, TestTube2, UploadCloud } from "lucide-react";
import { useEffect, useState } from "react";
import { TerminalMetric, TerminalPanel, TerminalTable, TerminalWarning } from "@/components/terminal";
import { api, type BitgetConnectionTest, type SystemStatus } from "@/lib/api";

type ShortcutRow = {
  id: string;
  keys: string;
  action: string;
  scope: string;
};

const shortcuts: ShortcutRow[] = [
  { id: "cmd-k", keys: "Cmd/Ctrl + K", action: "Open command palette", scope: "Global" },
  { id: "slash", keys: "/", action: "Open command palette", scope: "Global when not typing" },
  { id: "gd", keys: "G then D", action: "Dashboard", scope: "Route mode" },
  { id: "gr", keys: "G then R", action: "Research Runs", scope: "Route mode" },
  { id: "gp", keys: "G then P", action: "Positions", scope: "Route mode" },
  { id: "gj", keys: "G then J", action: "Journal", scope: "Route mode" },
  { id: "gs", keys: "G then S", action: "Shadow Account", scope: "Route mode" },
  { id: "gv", keys: "G then V", action: "Validation Lab", scope: "Route mode" }
];

export function SettingsShell() {
  const [status, setStatus] = useState<SystemStatus | null>(null);
  const [connection, setConnection] = useState<BitgetConnectionTest | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState("");

  async function load() {
    setError("");
    setLoading(true);
    try {
      setStatus(await api.systemStatus());
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load settings");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    void load();
  }, []);

  async function testConnection() {
    setBusy("test");
    setError("");
    try {
      setConnection(await api.testBitgetConnection());
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to test Bitget connection");
    } finally {
      setBusy("");
    }
  }

  async function syncPositions() {
    setBusy("sync");
    setError("");
    try {
      await api.syncBitgetPositions();
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to sync positions");
    } finally {
      setBusy("");
    }
  }

  return (
    <div className="page">
      <header className="pageHeader">
        <div>
          <p className="eyebrow">Terminal settings</p>
          <h1>Settings</h1>
          <p className="subtle">Provider boundary, read-only safety, database state, keyboard workflow를 확인합니다.</p>
        </div>
        <button className="button secondary" onClick={load} disabled={loading}>
          <RefreshCw size={16} />
          Refresh
        </button>
      </header>

      {error ? <TerminalWarning tone="error">{error}</TerminalWarning> : null}

      <section className="grid four">
        <TerminalMetric label="Environment" value={status?.environment ?? "-"} tone="neutral" />
        <TerminalMetric label="Provider" value={status?.market_data_provider ?? "-"} tone={status?.market_data_provider === "bitget" ? "positive" : "warning"} />
        <TerminalMetric label="Database" value={status?.database ?? "-"} tone={status?.database === "ok" ? "positive" : "negative"} />
        <TerminalMetric label="Private API" value={connection?.private_positions.status ?? status?.bitget_private_api ?? "-"} tone={status?.bitget_private_api === "ok" ? "positive" : "neutral"} />
        <TerminalMetric label="Sync Cycle" value={status ? `${status.refresh_policy.live_position_sync_interval_seconds}s` : "-"} tone="info" />
      </section>

      <section className="grid two">
        <TerminalPanel
          title="Read-only Exchange Boundary"
          subtitle="Bitget credentials are used only for data collection and position read sync"
          status="ok"
          actions={
            <>
              <button className="button secondary" onClick={testConnection} disabled={busy === "test"}>
                <TestTube2 size={16} />
                Test Bitget
              </button>
              <button className="button secondary" onClick={syncPositions} disabled={busy === "sync"}>
                <UploadCloud size={16} />
                Sync Positions
              </button>
            </>
          }
        >
          <div className="statusGrid">
            <StatusItem label="Public API" value={connection?.public_market_data.ok ? "ok" : status?.bitget_public_api ?? "-"} tone={connection?.public_market_data.ok || status?.bitget_public_api === "ok" ? "ok" : "muted"} />
            <StatusItem label="Private API" value={connection?.private_positions.status ?? status?.bitget_private_api ?? "-"} tone={status?.bitget_private_api === "ok" ? "ok" : "muted"} />
            <StatusItem label="Sample Symbol" value={connection?.public_market_data.sample_symbol ?? "-"} tone="muted" />
            <StatusItem label="Candles" value={String(connection?.public_market_data.candles ?? "-")} tone="muted" />
            <StatusItem label="Default Symbols" value={String(status?.default_symbols.length ?? "-")} tone="muted" />
            <StatusItem label="Updated" value={status?.timestamp ? new Date(status.timestamp).toLocaleString() : "-"} tone="muted" />
          </div>
        </TerminalPanel>

        <TerminalPanel title="Refresh Policy" subtitle="자동 동기화와 인사이트 stale 기준" status="accent">
          <div className="statusGrid">
            <StatusItem label="Position Sync" value={status ? `${status.refresh_policy.live_position_sync_interval_seconds}s` : "-"} tone="muted" />
            <StatusItem label="Insight Stale" value={status ? `${status.refresh_policy.insight_stale_after_minutes}m` : "-"} tone="muted" />
            <StatusItem label="Price Drift Guard" value={status ? `±${status.refresh_policy.insight_price_drift_stale_pct}%` : "-"} tone="muted" />
            <StatusItem label="Auto Insight" value={status?.refresh_policy.insight_auto_refresh_enabled ? "enabled" : "manual only"} tone="muted" />
            <StatusItem label="Insight Model" value={status?.refresh_policy.insight_model ?? "-"} tone="muted" />
            <StatusItem label="Min Regen" value={status ? `${status.refresh_policy.insight_min_regeneration_interval_minutes}m` : "-"} tone="muted" />
          </div>
        </TerminalPanel>

        <TerminalPanel title="Safety Contract" subtitle="v0.4 scope guardrails" status="ok">
          <div className="grid">
            <TerminalWarning tone="info">No automatic trading, no semi-automatic order buttons, and no exchange order execution code is exposed in the dashboard.</TerminalWarning>
            <TerminalWarning tone="info">LLM output explains deterministic score JSON; it does not calculate Entry Score, Risk, or FOMO Index.</TerminalWarning>
            <TerminalWarning tone="info">Mock and live providers remain separate so analysis results are reproducible from stored snapshots.</TerminalWarning>
          </div>
        </TerminalPanel>
      </section>

      <TerminalPanel title="Keyboard Workflow" subtitle="Bloomberg-style density without copying proprietary UI or brand elements" status="accent">
        <TerminalTable<ShortcutRow>
          data={shortcuts}
          idKey="id"
          columns={[
            { key: "keys", header: "Keys", width: 150, render: (row) => row.keys },
            { key: "action", header: "Action", render: (row) => row.action },
            { key: "scope", header: "Scope", width: 190, render: (row) => row.scope }
          ]}
        />
      </TerminalPanel>
    </div>
  );
}

function StatusItem({ label, value, tone }: { label: string; value: string; tone: "ok" | "muted" }) {
  return (
    <div className={`statusItem ${tone}`}>
      <span>{label}</span>
      <strong>{value}</strong>
    </div>
  );
}
