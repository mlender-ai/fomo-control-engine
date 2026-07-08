"use client";

import { useEffect, useMemo, useState } from "react";
import { RefreshCw } from "lucide-react";
import { TerminalMetric, TerminalPanel, TerminalTable, TerminalWarning } from "@/components/terminal";
import { api, type PerformanceMetrics, type PerformanceSummary } from "@/lib/api";

export function PerformanceShell() {
  const [performance, setPerformance] = useState<PerformanceSummary | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  async function load() {
    setLoading(true);
    setError("");
    try {
      setPerformance(await api.performance());
    } catch (err) {
      setError(err instanceof Error ? err.message : "계좌 성과 데이터를 불러오지 못했습니다.");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    void load();
  }, []);

  const overall = performance?.overall;
  const equityPoints = useMemo(() => performance?.equity_curve ?? [], [performance]);

  return (
    <div className="page" data-testid="performance-page">
      <header className="pageHeader">
        <div>
          <p className="eyebrow">Performance</p>
          <h1>계좌 성적표</h1>
          <p className="subtle">종료 거래 기준 성과, 에쿼티 낙폭, 엔진·대응·계좌 결과를 같은 거래 단위로 봅니다.</p>
        </div>
        <button className="button secondary" onClick={load} disabled={loading} type="button">
          <RefreshCw size={16} />
          새로고침
        </button>
      </header>

      {error ? <TerminalWarning tone="error">{error}</TerminalWarning> : null}

      {performance && overall ? (
        <>
          <section className="grid four">
            <TerminalMetric label="순손익" value={`${fmtMoney(overall.net_profit_usdt)} USDT`} delta={`N=${overall.sample_size}`} tone={toneForNumber(overall.net_profit_usdt)} />
            <TerminalMetric label="수익팩터 PF" value={fmtRatio(overall.profit_factor)} delta="참조 1.5 / 2.0" tone={overall.profit_factor !== null && overall.profit_factor >= 1.5 ? "positive" : "warning"} />
            <TerminalMetric label="최대 낙폭 MDD" value={fmtPct(overall.max_drawdown_pct)} delta={`${fmtMoney(overall.max_drawdown_usdt)} USDT`} tone={overall.max_drawdown_pct !== null && overall.max_drawdown_pct <= -10 ? "negative" : "neutral"} />
            <TerminalMetric label="Sortino" value={fmtRatio(overall.sortino)} delta="일간 수익률" tone="info" />
          </section>

          <section className="grid two">
            <TerminalPanel title="에쿼티 곡선" subtitle={`기준 자본 ${fmtMoney(performance.capital_base_usdt)} USDT · 실현손익 누적 + 최신 미실현`} status="accent">
              <EquityCurve points={equityPoints} />
            </TerminalPanel>

            <TerminalPanel title="월 MDD 가드" subtitle="사용자가 설정한 월 낙폭 한도에 대한 사실 통보입니다. 강제 조치는 없습니다." status={guardStatus(performance.mdd_guard)}>
              <MddGuard guard={performance.mdd_guard} />
            </TerminalPanel>
          </section>

          <section className="grid four">
            <TerminalMetric label="승률" value={fmtPct(overall.win_rate_pct)} delta="계좌 거래 기준" tone="info" />
            <TerminalMetric label="평균 R" value={fmtRatio(overall.avg_r)} delta={overall.avg_r_method ?? "trade unit"} tone="agent" />
            <TerminalMetric label="리커버리 팩터" value={fmtRatio(overall.recovery_factor)} delta={`${overall.longest_recovery_days}일 회복`} tone="warning" />
            <TerminalMetric label="파산확률" value={riskOfRuin(overall)} delta="동일 베팅 반복 가정" tone="negative" />
          </section>

          {overall.warnings?.length ? (
            <TerminalWarning tone="warning">{overall.warnings.join(" · ")}</TerminalWarning>
          ) : null}

          <section className="grid two">
            <TerminalPanel title="3성적표 교차 뷰" subtitle="엔진 판단, 내 대응, 계좌 결과를 같은 거래 단위로 대조합니다" status="ok">
              <CrossScorecard data={performance.scoreboard_cross_view} />
            </TerminalPanel>

            <TerminalPanel title="월별 성과" subtitle="월 단위 종료 거래 기준" status="neutral">
              <BreakdownTable data={performance.breakdowns?.month ?? {}} label="월" />
            </TerminalPanel>
          </section>

          <section className="grid two">
            <TerminalPanel title="방향별 성과" subtitle="롱/숏 포지션의 계좌 성과 분리" status="neutral">
              <BreakdownTable data={performance.breakdowns?.direction ?? {}} label="방향" />
            </TerminalPanel>

            <TerminalPanel title="자산 클래스·셋업 경유" subtitle="크립토/주식 및 셋업 알림 경유 여부" status="neutral">
              <div className="performanceSplitTables">
                <BreakdownTable data={performance.breakdowns?.asset_class ?? {}} label="클래스" compact />
                <BreakdownTable data={performance.breakdowns?.setup_linked ?? {}} label="경유" compact />
              </div>
            </TerminalPanel>
          </section>

          <p className="performanceDisclaimer">{performance.disclaimer}</p>
        </>
      ) : (
        <TerminalPanel title="Loading" subtitle="performance" status="neutral">
          <div className="terminalEmpty">계좌 성과 데이터를 불러오는 중입니다.</div>
        </TerminalPanel>
      )}
    </div>
  );
}

function EquityCurve({ points }: { points: Array<Record<string, unknown>> }) {
  if (!points.length) return <div className="terminalEmpty">에쿼티 포인트가 없습니다.</div>;
  const values = points.map((point) => Number(point.equity_usdt ?? 0)).filter(Number.isFinite);
  const min = Math.min(...values);
  const max = Math.max(...values);
  const span = Math.max(max - min, 1);
  return (
    <div className="equityCurve" role="img" aria-label="equity curve">
      {points.map((point, index) => {
        const equity = Number(point.equity_usdt ?? 0);
        const dd = Number(point.drawdown_pct ?? 0);
        const height = 20 + ((equity - min) / span) * 70;
        return (
          <div className="equityColumn" key={`${point.ts}-${index}`} title={`${String(point.ts)} · ${fmtMoney(equity)} USDT · MDD ${fmtPct(dd)}`}>
            <span className={dd < 0 ? "drawdown" : ""} style={{ height: `${Math.max(2, Math.abs(dd))}%` }} />
            <b style={{ height: `${height}%` }} />
          </div>
        );
      })}
    </div>
  );
}

function MddGuard({ guard }: { guard: Record<string, unknown> }) {
  if (!guard.configured) {
    return <div className="terminalEmpty">월 MDD 한도가 설정되지 않았습니다. 설정 시 80%/100% 도달 알림만 발송합니다.</div>;
  }
  return (
    <div className="mddGuard">
      <div>
        <span>현재 낙폭</span>
        <strong>{fmtPct(Number(guard.current_mdd_pct ?? 0) * -1)}</strong>
      </div>
      <div>
        <span>한도</span>
        <strong>{fmtPct(guard.limit_pct)}</strong>
      </div>
      <div>
        <span>사용률</span>
        <strong>{fmtPct(guard.usage_pct)}</strong>
      </div>
    </div>
  );
}

function CrossScorecard({ data }: { data: Record<string, unknown> }) {
  return (
    <div className="crossScorecard">
      <div><strong>{String(data.total_trades ?? 0)}</strong><span>총 거래</span></div>
      <div><strong>{String(data.engine_right_but_account_lost ?? 0)}</strong><span>엔진 적중 · 계좌 손실</span></div>
      <div><strong>{String(data.engine_wrong_dominant ?? 0)}</strong><span>엔진 오판 우세</span></div>
      <div><strong>{String(data.setup_linked_trades ?? 0)}</strong><span>셋업 경유</span></div>
      <p>{String(data.note ?? "")}</p>
    </div>
  );
}

function BreakdownTable({ data, label, compact = false }: { data: Record<string, PerformanceMetrics>; label: string; compact?: boolean }) {
  const rows = Object.entries(data).map(([group, metrics]) => ({ group, ...metrics }));
  if (!rows.length) return <div className="terminalEmpty">표본이 없습니다.</div>;
  return (
    <TerminalTable
      data={rows}
      idKey="group"
      emptyLabel="표본이 없습니다."
      columns={[
        { key: "group", header: label, render: (row) => groupLabel(String(row.group)) },
        { key: "sample_size", header: "N", align: "end", width: 64, render: (row) => String(row.sample_size) },
        { key: "net_profit_usdt", header: "순손익", align: "end", render: (row) => `${fmtMoney(Number(row.net_profit_usdt))}` },
        { key: "max_drawdown_pct", header: compact ? "MDD" : "MDD", align: "end", width: 96, render: (row) => fmtPct(Number(row.max_drawdown_pct ?? 0)) },
        { key: "profit_factor", header: "PF", align: "end", width: 76, render: (row) => fmtRatio(row.profit_factor as number | null) }
      ]}
    />
  );
}

function riskOfRuin(metrics: PerformanceMetrics): string {
  const ruin = metrics.risk_of_ruin ?? {};
  if (!ruin.published) return "유보";
  return fmtPct(ruin.probability_pct as number);
}

function guardStatus(guard: Record<string, unknown>): "neutral" | "warning" | "accent" | "ok" {
  if (!guard.configured) return "neutral";
  if (guard.status === "critical" || guard.status === "warn") return "warning";
  return "ok";
}

function groupLabel(value: string): string {
  const map: Record<string, string> = {
    long: "롱",
    short: "숏",
    crypto: "크립토",
    stock: "주식",
    setup_linked: "셋업 경유",
    direct_or_unknown: "직접/미분류"
  };
  return map[value] ?? value;
}

function fmtMoney(value: unknown): string {
  const number = Number(value);
  if (!Number.isFinite(number)) return "-";
  return number.toLocaleString(undefined, { maximumFractionDigits: 2 });
}

function fmtPct(value: unknown): string {
  const number = Number(value);
  if (!Number.isFinite(number)) return "유보";
  return `${number > 0 ? "+" : ""}${number.toFixed(2)}%`;
}

function fmtRatio(value: number | null | undefined): string {
  if (value === null || value === undefined || !Number.isFinite(Number(value))) return "유보";
  return Number(value).toFixed(2);
}

function toneForNumber(value: number): "positive" | "negative" | "neutral" {
  if (value > 0) return "positive";
  if (value < 0) return "negative";
  return "neutral";
}
