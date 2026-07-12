"use client";

import { RefreshCw, Send, TestTube2, UploadCloud } from "lucide-react";
import { useEffect, useState } from "react";
import { TerminalMetric, TerminalPanel, TerminalTable, TerminalWarning } from "@/components/terminal";
import { api, type AlertSettings, type BitgetConnectionTest, type SystemStatus } from "@/lib/api";
import { DEFAULT_DENSITY, loadDensity, saveDensity, type Density } from "@/lib/density";

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
  const [alertSettings, setAlertSettings] = useState<AlertSettings | null>(null);
  const [connection, setConnection] = useState<BitgetConnectionTest | null>(null);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState("");
  const [density, setDensity] = useState<Density>(DEFAULT_DENSITY);

  useEffect(() => {
    setDensity(loadDensity());
  }, []);

  function updateDensity(next: Density) {
    setDensity(next);
    saveDensity(next);
  }

  async function load() {
    setError("");
    setLoading(true);
    try {
      const [system, alerts] = await Promise.all([api.systemStatus(), api.alertSettings()]);
      setStatus(system);
      setAlertSettings(alerts);
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

  async function updateAlertRule(ruleId: string, patch: { enabled?: boolean; threshold?: number | null }) {
    setBusy(`alert:${ruleId}`);
    setError("");
    setNotice("");
    try {
      const next = await api.updateAlertSettings({ rules: { [ruleId]: patch } });
      setAlertSettings(next);
      setNotice("알림 설정이 저장되었습니다.");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to update alert settings");
    } finally {
      setBusy("");
    }
  }

  async function updateQuietHours(patch: {
    quiet_hours_enabled?: boolean;
    quiet_hours_start?: string;
    quiet_hours_end?: string;
    daily_summary_time?: string;
    pulse_interval_hours?: number;
    paper_alerts_enabled?: boolean;
  }) {
    setBusy("quiet");
    setError("");
    setNotice("");
    try {
      const next = await api.updateAlertSettings(patch);
      setAlertSettings(next);
      setNotice("무음 시간 설정이 저장되었습니다.");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to update quiet hours");
    } finally {
      setBusy("");
    }
  }

  async function sendTestAlert(ruleId?: string) {
    setBusy(ruleId ? `test-alert:${ruleId}` : "test-alert");
    setError("");
    setNotice("");
    try {
      const result = await api.sendTestAlert(ruleId);
      setNotice(result.sent > 0 ? `테스트 알림 ${result.sent}건을 발송했습니다.` : result.configured ? "Telegram 발송이 실패했습니다. 백엔드 로그를 확인하세요." : "Telegram 토큰 또는 chat_id가 설정되지 않았습니다.");
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to send test alert");
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
      {notice ? <TerminalWarning tone="info">{notice}</TerminalWarning> : null}

      <section className="grid four">
        <TerminalMetric label="Environment" value={status?.environment ?? "-"} tone="neutral" />
        <TerminalMetric label="Provider" value={status?.market_data_provider ?? "-"} tone={status?.market_data_provider === "bitget" ? "positive" : "warning"} />
        <TerminalMetric label="Database" value={status?.database ?? "-"} tone={status?.database === "ok" ? "positive" : "negative"} />
        <TerminalMetric label="Private API" value={connection?.private_positions.status ?? status?.bitget_private_api ?? "-"} tone={status?.bitget_private_api === "ok" ? "positive" : "neutral"} />
        <TerminalMetric label="Sync Cycle" value={status ? `${status.refresh_policy.live_position_sync_interval_seconds}s` : "-"} tone="info" />
      </section>

      <TerminalPanel title="표시 밀도" subtitle="포지션 관제 화면의 숫자 노출 수준" status="accent">
        <div className="densityToggle" role="group" aria-label="표시 밀도 선택">
          <button className={density === "simple" ? "active" : ""} onClick={() => updateDensity("simple")} type="button">
            간단
          </button>
          <button className={density === "detailed" ? "active" : ""} onClick={() => updateDensity("detailed")} type="button">
            상세
          </button>
          <small>{density === "simple" ? "신뢰도는 강/중/약으로, 이벤트는 최근 2개만 표시합니다." : "신뢰도 숫자와 이벤트를 모두 표시합니다."}</small>
        </div>
      </TerminalPanel>

      <TerminalPanel
        title="Telegram 알림"
        subtitle="판단이 필요한 순간만 발송하고, 야간에는 critical 외 알림을 아침 요약으로 묶습니다"
        status={alertSettings?.telegram.configured ? "ok" : "warning"}
        actions={
          <button className="button secondary" onClick={() => sendTestAlert()} disabled={busy === "test-alert"}>
            <Send size={16} />
            테스트 발송
          </button>
        }
      >
        <div className="statusGrid">
          <StatusItem label="Telegram" value={alertSettings?.telegram.configured ? "configured" : "missing"} tone={alertSettings?.telegram.configured ? "ok" : "muted"} />
          <StatusItem label="Chat IDs" value={String(alertSettings?.telegram.chat_ids_configured ?? "-")} tone="muted" />
          <StatusItem label="Quiet Hours" value={alertSettings?.telegram.quiet_hours_enabled ? `${alertSettings.telegram.quiet_hours_start}-${alertSettings.telegram.quiet_hours_end}` : "off"} tone="muted" />
          <StatusItem label="Morning Summary" value={alertSettings?.telegram.daily_summary_time ?? "-"} tone="muted" />
          <StatusItem label="Pulse" value={alertSettings ? `${alertSettings.telegram.pulse_interval_hours}h` : "-"} tone="muted" />
          <StatusItem label="엔진 거래" value={alertSettings?.telegram.paper_alerts_enabled ? "on" : "off"} tone="muted" />
        </div>
        {alertSettings ? (
          <div className="alertSettingsGrid">
            <label className="alertQuietToggle">
              <input
                type="checkbox"
                checked={alertSettings.telegram.quiet_hours_enabled}
                onChange={(event) => updateQuietHours({ quiet_hours_enabled: event.currentTarget.checked })}
                disabled={busy === "quiet"}
              />
              야간 무음 사용
            </label>
            <label className="alertQuietToggle">
              <input
                type="checkbox"
                checked={alertSettings.telegram.paper_alerts_enabled}
                onChange={(event) => updateQuietHours({ paper_alerts_enabled: event.currentTarget.checked })}
                disabled={busy === "quiet"}
              />
              엔진 페이퍼 진입·청산 알림
            </label>
            <input
              aria-label="무음 시작"
              type="time"
              value={alertSettings.telegram.quiet_hours_start}
              onChange={(event) => setAlertSettings({ ...alertSettings, telegram: { ...alertSettings.telegram, quiet_hours_start: event.currentTarget.value } })}
              onBlur={(event) => updateQuietHours({ quiet_hours_start: event.currentTarget.value })}
            />
            <input
              aria-label="무음 종료"
              type="time"
              value={alertSettings.telegram.quiet_hours_end}
              onChange={(event) => setAlertSettings({ ...alertSettings, telegram: { ...alertSettings.telegram, quiet_hours_end: event.currentTarget.value } })}
              onBlur={(event) => updateQuietHours({ quiet_hours_end: event.currentTarget.value })}
            />
            <input
              aria-label="아침 요약"
              type="time"
              value={alertSettings.telegram.daily_summary_time}
              onChange={(event) => setAlertSettings({ ...alertSettings, telegram: { ...alertSettings.telegram, daily_summary_time: event.currentTarget.value } })}
              onBlur={(event) => updateQuietHours({ daily_summary_time: event.currentTarget.value })}
            />
            <input
              aria-label="펄스 주기"
              min="0.25"
              step="0.25"
              type="number"
              value={alertSettings.telegram.pulse_interval_hours}
              onChange={(event) =>
                setAlertSettings({
                  ...alertSettings,
                  telegram: { ...alertSettings.telegram, pulse_interval_hours: Number(event.currentTarget.value) }
                })
              }
              onBlur={(event) => updateQuietHours({ pulse_interval_hours: Number(event.currentTarget.value) })}
            />
          </div>
        ) : null}
        <div className="alertRuleList">
          {alertSettings?.rules.map((rule) => (
            <div className={`alertRuleItem ${rule.severity}`} key={rule.id}>
              <div>
                <label>
                  <input
                    type="checkbox"
                    checked={rule.enabled}
                    onChange={(event) => updateAlertRule(rule.id, { enabled: event.currentTarget.checked })}
                    disabled={busy === `alert:${rule.id}`}
                  />
                  <strong>{rule.label}</strong>
                </label>
                <span>{severityLabel(rule.severity)} · 쿨다운 {rule.cooldown_minutes}분</span>
              </div>
              {rule.threshold !== null ? (
                <input
                  aria-label={`${rule.label} 임계값`}
                  type="number"
                  step="0.1"
                  value={rule.threshold}
                  onChange={(event) =>
                    setAlertSettings({
                      ...alertSettings,
                      rules: alertSettings.rules.map((item) => (item.id === rule.id ? { ...item, threshold: Number(event.currentTarget.value) } : item))
                    })
                  }
                  onBlur={(event) => updateAlertRule(rule.id, { threshold: Number(event.currentTarget.value) })}
                />
              ) : (
                <span className="alertRuleFixed">조건형</span>
              )}
              <button className="button ghost" onClick={() => sendTestAlert(rule.id)} disabled={busy === `test-alert:${rule.id}`} type="button">
                테스트
              </button>
            </div>
          ))}
        </div>
        <TerminalWarning tone="info">
          Critical은 무효화 이탈·청산 접근만 사용합니다. 모든 메시지는 스냅샷 숫자와 액션 플랜 근거만 인용합니다.
        </TerminalWarning>
      </TerminalPanel>

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

function severityLabel(severity: string) {
  if (severity === "critical") return "critical";
  if (severity === "warn") return "warn";
  if (severity === "action") return "action";
  return "info";
}
