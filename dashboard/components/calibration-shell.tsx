"use client";

import { useEffect, useMemo, useState } from "react";
import { RefreshCw } from "lucide-react";
import { TerminalMetric, TerminalPanel, TerminalTable, TerminalWarning } from "@/components/terminal";
import { api, type CalibrationSuggestion, type CalibrationSummary } from "@/lib/api";

const SAMPLE_WARNING = "표본 부족 — 결론 유보";

export function CalibrationShell() {
  const [calibration, setCalibration] = useState<CalibrationSummary | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");

  async function load() {
    setLoading(true);
    setError("");
    try {
      setCalibration(await api.reviewCalibration());
    } catch (err) {
      setError(err instanceof Error ? err.message : "캘리브레이션 데이터를 불러오지 못했습니다.");
    } finally {
      setLoading(false);
    }
  }

  async function vetoSuggestion(suggestion: CalibrationSuggestion) {
    setBusy(suggestion.id);
    setError("");
    try {
      await api.vetoCalibrationSuggestion(suggestion.id);
      setCalibration(await api.reviewCalibration());
    } catch (err) {
      setError(err instanceof Error ? err.message : "거부권 처리에 실패했습니다.");
    } finally {
      setBusy("");
    }
  }

  async function approveSuggestion(suggestion: CalibrationSuggestion) {
    setBusy(suggestion.id);
    setError("");
    try {
      await api.approveCalibrationSuggestion(suggestion.id);
      setCalibration(await api.reviewCalibration());
    } catch (err) {
      setError(err instanceof Error ? err.message : "승인 처리에 실패했습니다.");
    } finally {
      setBusy("");
    }
  }

  async function approveRecovery(signatureKey: string) {
    setBusy(signatureKey);
    setError("");
    try {
      await api.approveSignatureRecovery(signatureKey);
      setCalibration(await api.reviewCalibration());
    } catch (err) {
      setError(err instanceof Error ? err.message : "복귀 승인에 실패했습니다.");
    } finally {
      setBusy("");
    }
  }

  useEffect(() => {
    void load();
  }, []);

  const weekly = useMemo(() => (calibration?.weekly_report && typeof calibration.weekly_report === "object" ? calibration.weekly_report : null), [calibration]);

  return (
    <div className="page" data-testid="calibration-page">
      <header className="pageHeader">
        <div>
          <p className="eyebrow">Calibration</p>
          <h1>판단 캘리브레이션</h1>
          <p className="subtle">판단 원장의 적중률, 신뢰도 과신 구간, 레벨 품질, 승인된 파라미터 버전을 확인합니다.</p>
        </div>
        <button className="button secondary" onClick={load} disabled={loading}>
          <RefreshCw size={16} />
          새로고침
        </button>
      </header>

      {error ? <TerminalWarning tone="error">{error}</TerminalWarning> : null}

      {calibration ? (
        <>
          <section className="grid four">
            <TerminalMetric label="전체 판단" value={metricNumber(calibration.totals, "total")} delta={`${metricNumber(calibration.totals, "tested")} 검증`} tone="info" />
            <TerminalMetric label="전체 적중률" value={metricPercent(calibration.totals, "accuracy_pct")} tone={metricTone(calibration.totals)} />
            <TerminalMetric label="중간 채점" value={String(calibration.score_contexts?.interim ?? 0)} delta="open position" tone="agent" />
            <TerminalMetric label="자율 예정" value={String(calibration.suggestion_status_counts?.scheduled ?? 0)} delta={`${calibration.suggestion_status_counts?.experiment ?? 0} shadow experiments`} tone="warning" />
          </section>

          <section className="grid two">
            <div data-testid="calibration-module-scorecard">
              <TerminalPanel title="판단 성적표" subtitle={calibration.sample_warning} status="accent">
                <JudgmentScorecard rows={calibration.judgment_types ?? {}} />
              </TerminalPanel>
            </div>

            <div data-testid="calibration-module-weekly">
              <TerminalPanel title="주간 리포트" subtitle="일요일 20:00 텔레그램 리포트와 같은 결정론 요약" status="ok">
                <WeeklyReport weekly={weekly} onApproveRecovery={approveRecovery} busy={busy} />
              </TerminalPanel>
            </div>
          </section>

          <section className="grid two">
            <div data-testid="calibration-module-confidence">
              <TerminalPanel title="신뢰도 곡선" subtitle="confidence 구간별 실제 적중률. 대각선은 표시 신뢰도와 실제 적중률이 같은 이상선입니다" status="accent">
                <ConfidenceCurve rows={calibration.confidence_curve ?? []} />
              </TerminalPanel>
            </div>

            <div data-testid="calibration-module-levels">
              <TerminalPanel title="레벨 품질" subtitle="레벨 score 구간별 무효화/익절 basis 검증" status="neutral">
                <LevelQuality data={calibration.level_quality ?? {}} />
              </TerminalPanel>
            </div>
          </section>

          <TerminalPanel title="알림 대응 성적표" subtitle="알림 이후 6시간 내 대응과 이후 24시간 경로의 결과론적 비교" status="warning">
            <AlertResponseSummary data={calibration.alert_response_summary ?? {}} />
          </TerminalPanel>

          <TerminalPanel title="진입 전 셋업 성적표" subtitle="진입하지 않은 스카우트 셋업도 트리거 이후 가격 경로로 채점합니다" status="accent">
            <ScoutSetupSummary data={calibration.scout_setup_summary ?? {}} />
          </TerminalPanel>

          <TerminalPanel title="브리핑 성적표" subtitle="컨플루언스 스탠스와 이후 가격 경로를 비교합니다. conflicted 판정의 정직성도 따로 봅니다" status="accent">
            <BriefingPerformanceSummary data={calibration.briefing_performance ?? {}} />
          </TerminalPanel>

          <section className="grid two">
            <TerminalPanel title="파라미터 자율 피드" subtitle="조임 변경은 거부권 창 이후 자동 적용됩니다. 완화/중립 변경은 섀도 실험으로 검증합니다" status={calibration.suggestions.length ? "warning" : "neutral"}>
              {calibration.suggestions.length ? (
                <div className="eventTimeline">
                  {calibration.suggestions.map((suggestion) => (
                    <div className={`eventItem severity-${suggestion.status === "adopted" || suggestion.status === "approved" ? "low" : "medium"}`} key={suggestion.id}>
                      <div>
                        <strong>{suggestion.title}</strong>
                        <span>{suggestionStatusLabel(suggestion)} · N={suggestion.sample_size}</span>
                      </div>
                      <p>{suggestion.rationale}</p>
                      <small>{formatChange(suggestion.proposed_change)}</small>
                      <small>{autonomyLine(suggestion)}</small>
                      <small>{oosValidationLine(suggestion)}</small>
                      {["pending", "scheduled", "experiment"].includes(suggestion.status) ? (
                        <div className="actionGroup">
                          <button className="button" onClick={() => approveSuggestion(suggestion)} disabled={busy === suggestion.id}>승인</button>
                          <button className="button secondary" onClick={() => vetoSuggestion(suggestion)} disabled={busy === suggestion.id}>거부</button>
                        </div>
                      ) : null}
                    </div>
                  ))}
                </div>
              ) : (
                <div className="terminalEmpty">제안은 N≥15 표본이 쌓인 구간에서만 생성됩니다.</div>
              )}
            </TerminalPanel>

            <TerminalPanel title="적용된 파라미터 버전" subtitle="자율 채택 또는 수동 적용 이후 판단은 이 버전 스냅샷으로 태깅됩니다" status={calibration.engine_params?.length ? "ok" : "neutral"}>
              {calibration.engine_params?.length ? (
                <TerminalTable
                  data={calibration.engine_params}
                  idKey="id"
                  emptyLabel="승인된 파라미터가 없습니다."
                  columns={[
                    { key: "param", header: "Param", render: (row) => row.param },
                    { key: "new_value", header: "Value", align: "end", render: (row) => String(row.new_value) },
                    { key: "adopted_by", header: "By", width: 104, render: (row) => String(row.adopted_by ?? "manual") },
                    { key: "status", header: "Status", width: 104, render: (row) => row.status },
                    { key: "approved_at", header: "Approved", width: 168, render: (row) => new Date(row.approved_at).toLocaleString() }
                  ]}
                />
              ) : (
                <div className="terminalEmpty">아직 승인된 파라미터 변경이 없습니다.</div>
              )}
            </TerminalPanel>
          </section>
        </>
      ) : (
        <TerminalPanel title="Loading" subtitle="calibration" status="neutral">
          <div className="terminalEmpty">캘리브레이션 데이터를 불러오는 중입니다.</div>
        </TerminalPanel>
      )}
    </div>
  );
}

function JudgmentScorecard({ rows }: { rows: Record<string, Record<string, unknown>> }) {
  const data: Array<Record<string, unknown> & { type: string }> = Object.entries(rows).map(([type, value]) => ({ type, ...value }));
  return (
    <div className="calibrationTable">
      {data.map((row) => (
        <div className="calibrationScoreRow" key={row.type}>
          <div>
            <strong>{judgmentTypeLabel(String(row.type))}</strong>
            <span>N={metricNumber(row, "tested")} · {String(row.conclusion ?? SAMPLE_WARNING)}</span>
          </div>
          <StackBar row={row} />
          <b>{row.sample_state === "ok" ? metricPercent(row, "accuracy_pct") : SAMPLE_WARNING}</b>
        </div>
      ))}
    </div>
  );
}

function StackBar({ row }: { row: Record<string, unknown> }) {
  const total = Number(row.total || 0) || 1;
  const parts = [
    ["correct", "correct"],
    ["wrong", "wrong"],
    ["whipsaw", "whipsaw"],
    ["untested", "untested"]
  ] as const;
  return (
    <div className="stackBar" aria-label="outcome stack">
      {parts.map(([key, className]) => (
        <span className={className} key={key} style={{ width: `${(Number(row[key] || 0) / total) * 100}%` }} />
      ))}
    </div>
  );
}

function ConfidenceCurve({ rows }: { rows: Array<Record<string, unknown>> }) {
  const points = rows.filter((row) => typeof row.accuracy_pct === "number").map((row) => ({
    x: Number(row.confidence_midpoint_pct ?? 0),
    y: Number(row.accuracy_pct ?? 0),
    bucket: String(row.bucket ?? "-"),
    state: String(row.calibration_state ?? "insufficient_sample"),
    tested: Number(row.tested ?? 0)
  }));
  if (!points.length) {
    return <div className="terminalEmpty">confidence 표본이 아직 없습니다.</div>;
  }
  return (
    <div className="confidenceChart">
      <svg viewBox="0 0 100 100" role="img" aria-label="confidence calibration chart">
        <line x1="10" y1="90" x2="90" y2="10" className="ideal" />
        {points.map((point) => (
          <circle key={point.bucket} cx={10 + point.x * 0.8} cy={90 - point.y * 0.8} r={point.tested >= 10 ? 3.6 : 2.4} className={`point ${point.state}`} />
        ))}
      </svg>
      <div className="confidenceLegend">
        {points.map((point) => (
          <span key={point.bucket}>{point.bucket}: {point.tested >= 10 ? `${point.y.toFixed(1)}%` : SAMPLE_WARNING}</span>
        ))}
      </div>
    </div>
  );
}

function LevelQuality({ data }: { data: Record<string, Array<Record<string, unknown>>> }) {
  const rows: Array<Record<string, unknown> & { group: string }> = [
    ...(data.invalidation ?? []).map((row) => ({ group: "무효화", ...row })),
    ...(data.take_profit ?? []).map((row) => ({ group: "익절", ...row }))
  ];
  if (!rows.length) {
    return <div className="terminalEmpty">level_score가 포함된 채점 표본이 아직 없습니다.</div>;
  }
  return (
    <div className="calibrationTable">
      {rows.map((row) => (
        <div className="calibrationScoreRow" key={`${row.group}-${row.bucket}`}>
          <div>
            <strong>{row.group} · score {String(row.bucket)}</strong>
            <span>N={metricNumber(row, "tested")} · {String(row.conclusion ?? SAMPLE_WARNING)}</span>
          </div>
          <StackBar row={row} />
          <b>{row.sample_state === "ok" ? metricPercent(row, "accuracy_pct") : SAMPLE_WARNING}</b>
        </div>
      ))}
    </div>
  );
}

function WeeklyReport({
  weekly,
  onApproveRecovery,
  busy
}: {
  weekly: Record<string, unknown> | null;
  onApproveRecovery?: (signatureKey: string) => void;
  busy?: string;
}) {
  if (!weekly) {
    return <div className="terminalEmpty">주간 리포트 데이터가 없습니다.</div>;
  }
  const totals = asRecord(weekly.totals);
  const highlights = Array.isArray(weekly.highlights) ? weekly.highlights.map(String) : [];
  const best = asRecord(weekly.best_judgment);
  const worst = asRecord(weekly.worst_judgment);
  const accuracyCi = Array.isArray(totals.accuracy_ci) ? totals.accuracy_ci : null;
  return (
    <div className="calibrationWeekly">
      <div className="terminalMetricGrid">
        <TerminalMetric label="주간 검증" value={metricNumber(totals, "tested")} tone="info" />
        <TerminalMetric
          label="주간 적중률"
          value={metricPercent(totals, "accuracy_pct")}
          delta={accuracyCi ? `CI ${accuracyCi[0]}~${accuracyCi[1]}%` : undefined}
          tone={metricTone(totals)}
        />
      </div>
      {highlights.map((item, index) => <p key={`${index}-${item}`}>{item}</p>)}
      <div className="grid two compactGrid">
        <div className="calibrationMiniBox"><strong>최고 판단</strong><span>{best.detail ? `${judgmentTypeLabel(String(best.judgment_type))} · ${String(best.detail)}` : "표본 없음"}</span></div>
        <div className="calibrationMiniBox"><strong>최악 판단</strong><span>{worst.detail ? `${judgmentTypeLabel(String(worst.judgment_type))} · ${String(worst.detail)}` : "표본 없음"}</span></div>
      </div>
      <SelfAuditSection audit={asRecord(weekly.self_audit)} onApproveRecovery={onApproveRecovery} busy={busy} />
    </div>
  );
}

function SelfAuditSection({
  audit,
  onApproveRecovery,
  busy
}: {
  audit: Record<string, unknown>;
  onApproveRecovery?: (signatureKey: string) => void;
  busy?: string;
}) {
  const did = asRecord(audit.engine_did_autonomously);
  const waiting = asRecord(audit.awaiting_approval);
  const didRows = Array.isArray(did.transitions) ? did.transitions.map((row) => asRecord(row)) : [];
  const waitingRows = Array.isArray(waiting.transitions) ? waiting.transitions.map((row) => asRecord(row)) : [];
  const recoveryPending = Array.isArray(waiting.recovery_pending) ? waiting.recovery_pending.map(String) : [];
  const meta = asRecord(audit.meta_integrity);
  if (!didRows.length && !waitingRows.length && !recoveryPending.length && !audit.generated_at) {
    return null;
  }
  return (
    <div className="calibrationSelfAudit">
      {audit.critical ? <p className="dangerText">🚨 전 시그니처 격리 — 엔진 전면 불신 상태 · 신규 자율 강등 동결</p> : null}
      <div className="grid two compactGrid">
        <div className="calibrationMiniBox">
          <strong>엔진이 이번 주 스스로 한 일 (자율 {didRows.length}건)</strong>
          {didRows.length ? didRows.slice(0, 5).map((row, index) => (
            <span key={`${index}-${String(row.signature_key)}`}>
              {String(row.signature_key)}: {String(row.from)}→{String(row.to)}{row.regime ? ` · ${String(row.regime)} 한정` : ""} ({String(row.reason)})
            </span>
          )) : <span>이번 주 자율 전이 없음</span>}
        </div>
        <div className="calibrationMiniBox">
          <strong>사용자 승인 대기 중인 일 ({waitingRows.length + recoveryPending.length}건)</strong>
          {waitingRows.slice(0, 5).map((row, index) => (
            <span key={`${index}-${String(row.signature_key)}`}>{String(row.signature_key)}: {String(row.transition)} ({String(row.reason)})</span>
          ))}
          {recoveryPending.map((key) => (
            <span key={key} className="calibrationRecoveryRow">
              복귀 제안: {key}
              {onApproveRecovery ? (
                <button className="button secondary" onClick={() => onApproveRecovery(key)} disabled={busy === key}>복귀 승인</button>
              ) : null}
            </span>
          ))}
          {!waitingRows.length && !recoveryPending.length ? <span>승인 대기 항목 없음</span> : null}
        </div>
      </div>
      {typeof meta.misjudgment_rate_pct === "number" ? (
        <p className="subtle">자율 강등 오판율 {meta.misjudgment_rate_pct}% (N={metricNumber(meta, "autonomous_downgrades")}) — 자율 규칙도 채점 대상</p>
      ) : null}
    </div>
  );
}

function AlertResponseSummary({ data }: { data: Record<string, unknown> }) {
  const total = asRecord(data.total);
  const byRule = asRecord(data.by_rule);
  const rows: Array<Record<string, unknown> & { ruleId: string }> = Object.entries(byRule).map(([ruleId, value]) => ({ ruleId, ...asRecord(value) }));
  if (!Number(total.total || 0)) {
    return <div className="terminalEmpty">아직 채점된 알림 대응이 없습니다. 알림 발생 후 6시간 창이 지나면 자동으로 누적됩니다.</div>;
  }
  return (
    <div className="alertResponseSummary">
      <div className="terminalMetricGrid">
        <TerminalMetric label="대응 표본" value={metricNumber(total, "total")} delta={`${metricNumber(total, "tested")} 검증`} tone="info" />
        <TerminalMetric label="좋은 대응" value={metricNumber(total, "response_good")} delta={metricPercent(total, "good_rate_pct")} tone="positive" />
        <TerminalMetric label="비용 발생" value={metricNumber(total, "response_costly")} delta={metricPercent(total, "costly_rate_pct")} tone="negative" />
      </div>
      <p>{String(data.behavior_summary ?? "대응 패턴을 계산할 표본이 부족합니다.")}</p>
      <div className="calibrationTable">
        {rows.map((row) => (
          <div className="calibrationScoreRow" key={row.ruleId}>
            <div>
              <strong>{alertRuleLabel(row.ruleId)}</strong>
              <span>N={metricNumber(row, "total")} · good {metricNumber(row, "response_good")} · costly {metricNumber(row, "response_costly")}</span>
            </div>
            <StackBar row={{ correct: row.response_good, wrong: row.response_costly, whipsaw: 0, untested: row.inconclusive, total: row.total }} />
            <b>{metricPercent(row, "good_rate_pct")}</b>
          </div>
        ))}
      </div>
      <small>결과론적 비교입니다. 대응 채점은 다음 알림 임계값에 자동 반영하지 않습니다.</small>
    </div>
  );
}

function ScoutSetupSummary({ data }: { data: Record<string, unknown> }) {
  const rows: Array<Record<string, unknown> & { setupType: string }> = Object.entries(asRecord(data.by_type)).map(([setupType, value]) => ({ setupType, ...asRecord(value) }));
  if (!Number(data.total || 0)) {
    return <div className="terminalEmpty">아직 채점된 진입 전 셋업이 없습니다. 무장 셋업이 트리거된 뒤 가격 경로가 쌓이면 자동 채점됩니다.</div>;
  }
  return (
    <div className="alertResponseSummary">
      <div className="terminalMetricGrid">
        <TerminalMetric label="셋업 표본" value={metricNumber(data, "total")} delta={`${metricNumber(data, "tested")} 검증`} tone="info" />
        <TerminalMetric label="적중률" value={metricPercent(data, "accuracy_pct")} tone={metricTone(data)} />
        <TerminalMetric label="미검증" value={metricNumber(data, "untested")} tone="neutral" />
      </div>
      <div className="calibrationTable">
        {rows.map((row) => (
          <div className="calibrationScoreRow" key={row.setupType}>
            <div>
              <strong>{setupTypeLabel(row.setupType)}</strong>
              <span>N={metricNumber(row, "total")} · correct {metricNumber(row, "correct")} · wrong {metricNumber(row, "wrong")}</span>
            </div>
            <StackBar row={row} />
            <b>{Number(row.total || 0) >= 10 ? metricPercent(row, "accuracy_pct") : SAMPLE_WARNING}</b>
          </div>
        ))}
      </div>
      <small>{String(data.sample_warning ?? "N<10 구간은 결론을 보류합니다.")}</small>
    </div>
  );
}

function BriefingPerformanceSummary({ data }: { data: Record<string, unknown> }) {
  const summary = asRecord(data.summary);
  const byStance: Array<Record<string, unknown> & { stance: string }> = Object.entries(asRecord(data.by_stance)).map(([stance, value]) => ({ stance, ...asRecord(value) }));
  const conflicted = asRecord(data.conflicted_honesty);
  if (!Number(data.total || 0)) {
    return <div className="terminalEmpty">아직 채점된 브리핑 스탠스가 없습니다. 브리핑 생성 후 가격 경로가 쌓이면 자동 채점됩니다.</div>;
  }
  return (
    <div className="alertResponseSummary">
      <div className="terminalMetricGrid">
        <TerminalMetric label="브리핑 표본" value={metricNumber(data, "total")} delta={`${metricNumber(summary, "tested")} 검증`} tone="info" />
        <TerminalMetric label="스탠스 적중률" value={metricPercent(summary, "accuracy_pct")} tone={metricTone(summary)} />
        <TerminalMetric label="충돌 판정 정직성" value={metricPercent(conflicted, "honesty_pct")} tone={metricTone({ accuracy_pct: conflicted.honesty_pct })} />
      </div>
      <div className="calibrationTable">
        {byStance.map((row) => (
          <div className="calibrationScoreRow" key={row.stance}>
            <div>
              <strong>{briefingStanceLabel(row.stance)}</strong>
              <span>N={metricNumber(row, "total")} · 검증 {metricNumber(row, "tested")}</span>
            </div>
            <StackBar row={row} />
            <b>{Number(row.tested || 0) >= 10 ? metricPercent(row, "accuracy_pct") : SAMPLE_WARNING}</b>
          </div>
        ))}
      </div>
      <small>{String(data.sample_warning ?? "N<10 구간은 결론을 보류합니다.")}</small>
    </div>
  );
}

function metricNumber(bucket: Record<string, unknown>, key: string) {
  const value = bucket[key];
  return typeof value === "number" ? String(value) : "0";
}

function metricPercent(bucket: Record<string, unknown>, key: string) {
  const value = bucket[key];
  return typeof value === "number" ? `${value.toFixed(1)}%` : SAMPLE_WARNING;
}

function metricTone(bucket: Record<string, unknown>) {
  const value = bucket.accuracy_pct;
  if (typeof value !== "number") return "neutral" as const;
  if (value >= 70) return "positive" as const;
  if (value >= 50) return "warning" as const;
  return "negative" as const;
}

function judgmentTypeLabel(type: string) {
  const labels: Record<string, string> = {
    invalidation: "무효화 기준",
    take_profit: "익절 후보",
    planned_invalidation: "시나리오 무효화",
    planned_take_profit: "시나리오 익절",
    entry_checklist: "진입 체크리스트",
    alert_fired: "감시 트리거",
    scout_setup: "진입 전 셋업",
    analyst_briefing: "브리핑 스탠스",
    wyckoff_event: "와이코프 이벤트",
    liquidity_sweep: "유동성 스윕",
    harmonic_prz: "하모닉 PRZ"
  };
  return labels[type] ?? type;
}

function briefingStanceLabel(stance: string) {
  const labels: Record<string, string> = {
    long_leaning: "롱 우위",
    short_leaning: "숏 우위",
    conflicted: "충돌",
    insufficient: "근거 부족",
  };
  return labels[stance] ?? stance;
}

function alertRuleLabel(ruleId: string) {
  const labels: Record<string, string> = {
    trigger_near: "트리거 근접",
    invalidation_breach: "무효화 이탈",
    take_profit_hit: "익절 후보 도달",
    status_worsened: "상태 악화",
    health_drop: "건강도 급락",
    liq_proximity: "청산 접근",
    liq_unknown_high_lev: "청산가 미수신",
    funding_extreme: "펀딩 과열",
    oi_divergence: "OI 역행",
    liq_cluster_near: "청산 밀집대 근접",
    setup_near: "셋업 접근",
    setup_triggered: "셋업 트리거",
    setup_invalidated: "셋업 무효화"
  };
  return labels[ruleId] ?? ruleId;
}

function setupTypeLabel(type: string) {
  const labels: Record<string, string> = {
    harmonic_prz: "하모닉 PRZ",
    structure_level: "구조 레벨",
    wyckoff_event: "와이코프 이벤트",
    crowding_level: "수급+레벨",
    manual_price: "수동 가격"
  };
  return labels[type] ?? type;
}

function formatChange(change: Record<string, unknown>) {
  return `${String(change.parameter ?? "-")}: ${String(change.from ?? "-")} -> ${String(change.to ?? "-")}`;
}

function suggestionStatusLabel(suggestion: CalibrationSuggestion) {
  const labels: Record<CalibrationSuggestion["status"], string> = {
    pending: "평가 대기",
    scheduled: "자율 적용 예정",
    experiment: "섀도 실험 중",
    adopted: "자율 적용됨",
    approved: "수동 적용됨",
    rejected: "기각됨",
    vetoed: "거부됨",
    discarded: "폐기됨",
    dwell_blocked: "재변경 대기",
    rolled_back: "롤백됨",
  };
  return labels[suggestion.status] ?? suggestion.status;
}

function oosValidationLine(suggestion: CalibrationSuggestion): string {
  // WO-36 §4: 승인 결정은 OOS(학습/검증 분할) 근거와 함께 내려야 한다.
  const oos = suggestion.oos_validation;
  if (!oos || oos.sample_state !== "ok") {
    return "OOS 검증: 표본 부족 — 검증기간 성립 여부 미확인";
  }
  const train = oos.train ?? {};
  const validation = oos.validation ?? {};
  const holds = oos.holds_in_validation ? "검증기간에도 성립" : "검증기간 미성립 — 승인 주의";
  return `OOS 검증: 학습 ${train.rate_pct ?? "-"}% (N=${train.sample_size ?? "-"}) / 검증 ${validation.rate_pct ?? "-"}% (N=${validation.sample_size ?? "-"}) · ${holds}`;
}

function autonomyLine(suggestion: CalibrationSuggestion) {
  const meta = asRecord(suggestion.autonomy);
  const direction = String(meta.change_direction ?? "-");
  const deadline = typeof meta.veto_deadline_at === "string" ? new Date(meta.veto_deadline_at).toLocaleString() : "";
  if (suggestion.status === "scheduled") {
    return `변경 방향 ${direction} · 거부권 기한 ${deadline || "-"}`;
  }
  if (suggestion.status === "experiment") {
    const criteria = asRecord(meta.preregistered_criteria);
    return `변경 방향 ${direction} · 사전 기준 ${String(criteria.success ?? "고정됨")} · N≥${String(criteria.min_sample_size ?? 30)}`;
  }
  if (suggestion.status === "adopted") {
    return `자율 채택 · ${String(meta.adopted_at ?? "")}`;
  }
  if (suggestion.status === "vetoed") {
    return `거부권 행사 · ${String(meta.vetoed_at ?? "")}`;
  }
  if (suggestion.status === "discarded" || suggestion.status === "dwell_blocked") {
    return `자율 가드 · ${String(meta.reason ?? "-")}`;
  }
  return `변경 방향 ${direction}`;
}

function asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value) ? (value as Record<string, unknown>) : {};
}
