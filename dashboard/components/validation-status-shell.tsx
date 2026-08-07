"use client";

/**
 * WO-FCE-SAMPLE-VIABILITY-01 PHASE 6 — 검증 완료 현황.
 *
 * "4주 검증"은 캘린더 기준이었고, 그래서 "3주 검증했다"는 거짓 진술이 가능했다.
 * 이 화면은 **세 조건을 함께** 낸다: 유효 관측일 · 채점 가능 표본 · 시장 국면.
 * 유효일만 크게 표시하지 않는다 — **가장 뒤처진 조건이 그 트랙의 상태다.**
 */

import { useEffect, useState } from "react";
import { RefreshCw } from "lucide-react";
import { TerminalPanel, TerminalTable, TerminalWarning } from "@/components/terminal";
import { api, type LiveGateTrack, type PaperDashboard, type PaperDiagnosis, type SampleViabilityTrack, type ValidationCompletion } from "@/lib/api";

const TRACK_ORDER = ["crypto", "poly", "stock_kr", "stock_us"];

const VERDICT_LABEL: Record<string, string> = {
  VIABLE: "표본 생성 가능",
  SLOW: "검증 창 밖 도달",
  STRUCTURALLY_BLOCKED: "구조적 차단",
  INSUFFICIENT_DATA: "판정 불가"
};

export function ValidationStatusShell() {
  const [diagnosis, setDiagnosis] = useState<PaperDiagnosis | null>(null);
  const [paper, setPaper] = useState<PaperDashboard | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  async function load() {
    setLoading(true);
    setError("");
    try {
      const [next, dashboard] = await Promise.all([api.paperDiagnosis(), api.paperDashboard().catch(() => null)]);
      setDiagnosis(next);
      setPaper(dashboard);
    } catch (err) {
      setError(err instanceof Error ? err.message : "검증 현황을 불러오지 못했습니다.");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    void load();
  }, []);

  const viability = diagnosis?.sample_viability;
  const completion = viability?.completion ?? {};
  const tracks = viability?.tracks ?? {};
  const funnel = paper?.gate_funnel;
  const gate = diagnosis?.live_trading_gate;

  return (
    <div className="page" data-testid="validation-status-page">
      <header className="pageHeader">
        <div>
          <p className="eyebrow">Validation</p>
          <h1>검증 완료 현황</h1>
          <p className="subtle">
            검증 완료 = 유효 관측일 ≥ {viability?.target_effective_days ?? 28} <b>AND</b> 채점 가능 표본 ≥ {viability?.target_samples ?? 30} <b>AND</b> 시장 국면 ≥{" "}
            {viability?.target_regimes ?? 2}. 세 조건을 모두 만족해야 완료이며 트랙별로 독립 판정합니다.
          </p>
        </div>
        <button className="button secondary" onClick={load} disabled={loading} type="button">
          <RefreshCw size={16} />
          새로고침
        </button>
      </header>

      {error ? <TerminalWarning tone="error">{error}</TerminalWarning> : null}
      {viability && !viability.available ? <TerminalWarning tone="warning">표본 판정을 산출할 수 없습니다 ({viability.reason ?? "unknown"}).</TerminalWarning> : null}
      {viability?.blocked_tracks?.length ? (
        <TerminalWarning tone="error">
          구조적 차단 트랙: {viability.blocked_tracks.join(", ")} — 관측일을 더 쌓아도 표본이 생성되지 않습니다. 선정 기준을 고쳐야 합니다.
        </TerminalWarning>
      ) : null}

      <TerminalPanel title="트랙별 3조건" subtitle="가장 뒤처진 조건이 그 트랙의 상태입니다" status="accent">
        <div className="validationLines">
          {TRACK_ORDER.filter((track) => completion[track]).map((track) => (
            <ConditionRow completion={completion[track]} key={track} viability={tracks[track]} />
          ))}
          {TRACK_ORDER.every((track) => !completion[track]) ? <div className="terminalEmpty">판정 데이터가 없습니다.</div> : null}
        </div>
      </TerminalPanel>

      <TerminalPanel title="표본 산출 실측" subtitle="진입률의 분모는 유효 관측일입니다. 추정 계수를 쓰지 않습니다." status="neutral">
        <TerminalTable
          data={TRACK_ORDER.filter((track) => tracks[track]).map((track) => tracks[track])}
          idKey="track"
          emptyLabel="표본 산출 데이터가 없습니다."
          columns={[
            { key: "label", header: "트랙", render: (row) => row.label },
            { key: "entries", header: "진입/유효일", align: "end", render: (row) => `${row.entries_per_effective_day} (CI ${row.entries_per_effective_day_ci95[0]}~${row.entries_per_effective_day_ci95[1]})` },
            { key: "exit", header: "청산 완료율", align: "end", width: 120, render: (row) => `${Math.round(row.exit_completion_rate * 1000) / 10}%` },
            { key: "projected", header: `D+${viability?.target_effective_days ?? 28} 예상 표본`, align: "end", width: 140, render: (row) => String(row.projected_samples_at_target) },
            { key: "eta", header: "N≥30 예상", align: "end", width: 170, render: (row) => (row.effective_days_to_target ? `유효 ${row.effective_days_to_target}일 후` : "—") },
            { key: "verdict", header: "판정", width: 130, render: (row) => VERDICT_LABEL[row.verdict] ?? row.verdict }
          ]}
        />
        <ul className="validationNotes">
          {TRACK_ORDER.filter((track) => tracks[track]).map((track) => (
            <li key={track}>
              <b>{tracks[track].label}</b> — {tracks[track].verdict_reason}
            </li>
          ))}
        </ul>
      </TerminalPanel>

      <TerminalPanel
        title="checklist 항목별 통과율"
        subtitle={funnel?.checklist_bottleneck?.policy ?? "품질 게이트입니다. 통과율이 낮아도 임계값을 완화하지 않습니다."}
        status="warning"
      >
        {funnel?.checklist_bottleneck ? (
          <p className="subtle">
            평가 {funnel.checklist_bottleneck.evaluated}건 · checklist 게이트 통과율 {funnel.checklist_bottleneck.checklist_gate_pass_rate_pct ?? "—"}% ·{" "}
            <b>checklist만 막은 건수 {funnel.checklist_bottleneck.checklist_only_blocked}</b>
            {funnel.checklist_bottleneck.top_sole_blocker
              ? ` · 최다 유일 탈락 사유: ${funnel.checklist_bottleneck.top_sole_blocker.label} (${funnel.checklist_bottleneck.top_sole_blocker.sole_block_count}건)`
              : ""}
          </p>
        ) : null}
        <TerminalTable
          data={funnel?.checklist_pass_rates ?? []}
          idKey="key"
          emptyLabel="checklist 계측 데이터가 없습니다."
          columns={[
            { key: "label", header: "항목", render: (row) => row.label },
            { key: "evaluated", header: "평가", align: "end", width: 80, render: (row) => String(row.evaluated) },
            { key: "passed", header: "통과", align: "end", width: 80, render: (row) => String(row.passed) },
            { key: "pass_rate_pct", header: "통과율", align: "end", width: 90, render: (row) => `${row.pass_rate_pct}%` },
            { key: "sole_block_count", header: "유일 탈락", align: "end", width: 100, render: (row) => String(row.sole_block_count ?? 0) }
          ]}
        />
      </TerminalPanel>

      <TerminalPanel
        title="자동매매 전환 게이트"
        subtitle={gate?.principle ?? "결과를 보기 전에 정한 기준만이 기준입니다. 이 판정은 아무것도 해제하지 않습니다."}
        status={gate?.gate_approved ? "warning" : "neutral"}
      >
        {gate?.available === false ? (
          <div className="terminalEmpty">게이트 판정을 산출할 수 없습니다 ({gate.reason ?? "unknown"}).</div>
        ) : (
          <>
            <p className="subtle">
              인적 승인: <b>{gate?.gate_approved ? "승인" : "미승인"}</b> · 서명란 {gate?.document ?? "docs/validation/LIVE_TRADING_GATE.md"} §5 ·{" "}
              {gate?.ready_tracks?.length ? `준비 완료 트랙: ${gate.ready_tracks.join(", ")}` : "준비 완료 트랙 없음"}
            </p>
            <div className="validationLines">
              {TRACK_ORDER.filter((track) => gate?.tracks?.[track]).map((track) => (
                <GateRow gate={gate!.tracks![track]} key={track} label={completion[track]?.label ?? track} />
              ))}
            </div>
          </>
        )}
      </TerminalPanel>

      {diagnosis?.observation_integrity?.manual_actions?.length ? (
        <TerminalPanel title="수동 조치 필요" subtitle="코드로 해결할 수 없는 관측 손실입니다" status="error">
          <ul className="validationNotes">
            {diagnosis.observation_integrity.manual_actions.map((action) => (
              <li key={action.kind}>
                <b>{action.title}</b> — {action.detail}
                <br />
                <span className="subtle">{action.remedy}</span>
              </li>
            ))}
          </ul>
        </TerminalPanel>
      ) : null}
    </div>
  );
}

function GateRow({ gate, label }: { gate: LiveGateTrack; label: string }) {
  return (
    <div className="validationLine" data-testid={`gate-line-${gate.track}`}>
      <strong>{label}</strong>
      <span className="validationCell">
        측정 축 {gate.measured_met}/{gate.measured_total}
      </span>
      {gate.axes
        .filter((axis) => axis.kind === "measured")
        .map((axis) => (
          <span className={`validationCell${axis.met ? " met" : ""}`} key={axis.axis} title={axis.detail}>
            {axis.met ? "✓" : axis.available ? "·" : "—"} {axis.label}
          </span>
        ))}
      <span className="validationVerdict">{gate.ready ? "[준비 완료]" : `[미달: ${gate.unmet.join(", ")}]`}</span>
      <span className="subtle validationStatement">{gate.seal}</span>
    </div>
  );
}

function ConditionRow({ completion, viability }: { completion: ValidationCompletion; viability?: SampleViabilityTrack }) {
  const { conditions } = completion;
  const blocked = completion.verdict === "STRUCTURALLY_BLOCKED";
  return (
    <div className={`validationLine${blocked ? " blocked" : ""}`} data-testid={`validation-line-${completion.track}`}>
      <strong>{completion.label}</strong>
      <Cell condition={conditions.effective_days} laggard={completion.laggard === "effective_days"} name="유효일" />
      <Cell condition={conditions.scored_samples} laggard={completion.laggard === "scored_samples"} name="표본" />
      <Cell condition={conditions.regimes} laggard={completion.laggard === "regimes"} name="국면" available={completion.regimes.available} />
      <span className="validationVerdict">
        {blocked ? "[구조적 차단]" : completion.complete ? "[완료]" : `[미달: ${completion.unmet.join(", ")}]`}
      </span>
      {viability ? <span className="subtle validationStatement">{viability.statement}</span> : null}
    </div>
  );
}

function Cell({ condition, laggard, name, available = true }: { condition: { current: number; target: number; met: boolean }; laggard: boolean; name: string; available?: boolean }) {
  return (
    <span className={`validationCell${laggard ? " laggard" : ""}${condition.met ? " met" : ""}`}>
      {name} {available ? `${condition.current}/${condition.target}` : "-"}
    </span>
  );
}
