"use client";

import { useEffect, useState } from "react";
import { BadgeCheck, Bookmark, Calculator, CircleAlert, CircleCheck, CircleSlash } from "lucide-react";
import { api, type EntryChecklistItem, type EntrySimulation } from "@/lib/api";
import { formatPrice, signedPercent } from "@/lib/format";
import { plainifyTaText } from "@/lib/labels/taGlossary";

/** 진입 시뮬레이터 (WO-FCE-13). read-only — 주문 API 없음. */
export function EntrySimulator({ symbol, markPrice, timeframe }: { symbol: string; markPrice: number | null; timeframe: string }) {
  const [direction, setDirection] = useState<"long" | "short">("long");
  const [entryPrice, setEntryPrice] = useState("");
  const [leverage, setLeverage] = useState("10");
  const [margin, setMargin] = useState("100");
  const [marginMode, setMarginMode] = useState("isolated");
  const [sim, setSim] = useState<EntrySimulation | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");

  // 심볼/타임프레임이 바뀌면 이전 결과를 비운다.
  useEffect(() => {
    setSim(null);
    setNotice("");
  }, [symbol, timeframe]);

  async function runSimulation() {
    const lev = Number(leverage);
    if (!Number.isFinite(lev) || lev <= 0) {
      setError("레버리지를 확인하세요.");
      return;
    }
    setLoading(true);
    setError("");
    setNotice("");
    try {
      const result = await api.simulateEntry({
        symbol,
        direction,
        entry_price: entryPrice ? Number(entryPrice) : null,
        leverage: lev,
        margin_usdt: margin ? Number(margin) : null,
        margin_mode: marginMode,
        timeframe
      });
      setSim(result);
    } catch (err) {
      setError(err instanceof Error ? err.message : "시뮬레이션에 실패했습니다.");
    } finally {
      setLoading(false);
    }
  }

  async function saveScenario() {
    if (!sim) return;
    setLoading(true);
    setError("");
    try {
      await api.saveScenario({
        symbol,
        direction: sim.direction,
        entry_price: sim.entry_price,
        leverage: sim.leverage,
        margin_usdt: sim.margin_usdt,
        margin_mode: sim.margin_mode,
        timeframe,
        note: ""
      });
      setNotice("시나리오를 저장했습니다. 실제 진입 시 이 심볼·방향으로 자동 매칭됩니다.");
    } catch (err) {
      setError(err instanceof Error ? err.message : "시나리오 저장에 실패했습니다.");
    } finally {
      setLoading(false);
    }
  }

  return (
    <section className="focusPanel entrySimulatorPanel" data-testid="entry-simulator">
      <div className="focusPanelHeader">
        <div>
          <h2>진입 시뮬레이션</h2>
          <p>가상 진입의 R:R·추정 청산·체크리스트 · 주문 실행 없음</p>
        </div>
        <span>{markPrice ? `현재가 ${formatPrice(markPrice)}` : "-"}</span>
      </div>

      <div className="simInputs">
        <div className="simDirectionToggle" role="group" aria-label="방향 선택">
          <button className={direction === "long" ? "active long" : ""} onClick={() => setDirection("long")} type="button">롱</button>
          <button className={direction === "short" ? "active short" : ""} onClick={() => setDirection("short")} type="button">숏</button>
        </div>
        <label className="simField">
          <span>진입가 (비우면 현재가)</span>
          <input value={entryPrice} onChange={(e) => setEntryPrice(e.target.value)} type="number" step="any" placeholder={markPrice ? String(markPrice) : "지정가"} />
        </label>
        <div className="simFieldRow">
          <label className="simField">
            <span>레버리지</span>
            <input value={leverage} onChange={(e) => setLeverage(e.target.value)} type="number" step="1" min="1" />
          </label>
          <label className="simField">
            <span>증거금 (USDT)</span>
            <input value={margin} onChange={(e) => setMargin(e.target.value)} type="number" step="any" min="0" />
          </label>
          <label className="simField">
            <span>마진 모드</span>
            <select value={marginMode} onChange={(e) => setMarginMode(e.target.value)}>
              <option value="isolated">격리</option>
              <option value="cross">교차</option>
            </select>
          </label>
        </div>
        <button className="button" data-testid="simulator-run" onClick={() => void runSimulation()} disabled={loading} type="button">
          <Calculator size={16} />
          {loading ? "계산 중" : "시뮬레이션"}
        </button>
      </div>

      {error ? <div className="actionPlanWarning">{error}</div> : null}
      {notice ? <div className="simNotice">{notice}</div> : null}

      {sim ? <SimulationResult sim={sim} onSave={() => void saveScenario()} saving={loading} /> : null}
    </section>
  );
}

function SimulationResult({ sim, onSave, saving }: { sim: EntrySimulation; onSave: () => void; saving: boolean }) {
  return (
    <div className="simResult" data-testid="simulator-result">
      <div className={`simVerdict ${sim.htf_conflict ? "warn" : ""}`}>{sim.verdict_line}</div>

      {sim.briefing_direction_conflict ? (
        <div className="simDangerBanner">
          <CircleAlert size={16} />
          브리핑 스탠스와 반대 방향 시뮬레이션입니다. 반대 근거와 무효화 조건을 먼저 확인하세요.
        </div>
      ) : null}

      {sim.analyst_briefing ? (
        <div className="simBriefingLine">
          <strong>브리핑</strong>
          <span>{sim.analyst_briefing.confluence.stance_label} · 종합 {sim.analyst_briefing.confluence.composite_score}/100 · 반대 근거 {sim.analyst_briefing.confluence.counter_evidence.length}개</span>
        </div>
      ) : null}

      <KellyReferenceBlock sim={sim} />

      {sim.survives_to_invalidation === false ? (
        <div className="simDangerBanner">
          <CircleAlert size={16} />
          손절 계획이 추정 청산보다 늦습니다 — 레버리지 과다. 무효화 도달 전에 청산될 수 있습니다.
        </div>
      ) : null}

      <div className="simMetricGrid">
        <SimMetric label="손익비 R:R" value={sim.rr_ratio === null ? "-" : String(sim.rr_ratio)} tone={sim.rr_ratio !== null && sim.rr_ratio >= 1.5 ? "positive" : "warning"} />
        <SimMetric label="무효화 거리" value={sim.invalidation_distance_pct === null ? "-" : signedPercent(sim.invalidation_distance_pct)} tone="warning" />
        <SimMetric
          label="추정 청산 (산식 기준)"
          value={sim.estimated_liquidation === null ? "-" : `${formatPrice(sim.estimated_liquidation)}${sim.estimated_liquidation_distance_pct !== null ? ` (${signedPercent(sim.estimated_liquidation_distance_pct)})` : ""}`}
          tone="negative"
          title={sim.liquidation_formula}
        />
        <SimMetric label="1차 익절 거리" value={sim.first_take_profit_distance_pct === null ? "-" : signedPercent(sim.first_take_profit_distance_pct)} tone="positive" />
        {sim.loss_usdt !== null ? <SimMetric label="예상 손실" value={`-${sim.loss_usdt} USDT`} tone="negative" /> : null}
        {sim.profit_usdt !== null ? <SimMetric label="예상 이익" value={`+${sim.profit_usdt} USDT`} tone="positive" /> : null}
        <SimMetric label="상위 TF" value={sim.htf_conflict ? "방향 충돌" : "충돌 없음"} tone={sim.htf_conflict ? "negative" : "positive"} />
        <SimMetric label="방향 점수" value={sim.direction_score === null ? "-" : `${sim.direction_score}/100`} tone="info" />
      </div>

      <div className="simChecklist" data-testid="simulator-checklist">
        <div className="simChecklistHeader">
          <strong>진입 체크리스트</strong>
          <span>체크 {sim.checklist_passed}/{sim.checklist_total} 통과</span>
        </div>
        {sim.checklist.map((item) => (
          <ChecklistRow key={item.key} item={item} />
        ))}
        <p className="simChecklistNote">체크 전항 통과가 진입 신호는 아닙니다. 최종 판단은 사용자 몫입니다.</p>
      </div>

      <button className="button secondary" onClick={onSave} disabled={saving} type="button">
        <Bookmark size={16} />
        {saving ? "저장 중" : "시나리오 저장"}
      </button>
    </div>
  );
}

function KellyReferenceBlock({ sim }: { sim: EntrySimulation }) {
  const kelly = sim.kelly_reference;
  if (!kelly) return null;
  if (!kelly.available) {
    return (
      <div className="simKellyBlock muted">
        <strong>켈리 참고치</strong>
        <span>{kelly.reason ?? "검증된 시그니처 통계 부족"}</span>
        <small>{kelly.disclaimer}</small>
      </div>
    );
  }
  return (
    <div className="simKellyBlock">
      <div>
        <strong>하프 켈리 참고 상한</strong>
        <b>{kelly.half_kelly_fraction_pct?.toFixed(2)}%</b>
      </div>
      <span>
        {kelly.label ?? "동일 시그니처"} · N={kelly.sample_size ?? "-"} · 1R 승률 CI 하한 {kelly.win_rate_ci_low_pct?.toFixed(1)}% · 중앙 손익비 {kelly.median_rr?.toFixed(2)}R
      </span>
      <small>{kelly.disclaimer}</small>
      {kelly.position_sizing_note ? <small>{kelly.position_sizing_note}</small> : null}
    </div>
  );
}

function ChecklistRow({ item }: { item: EntryChecklistItem }) {
  const Icon = item.status === "pass" ? CircleCheck : item.status === "fail" ? CircleAlert : CircleSlash;
  return (
    <div className={`simChecklistRow status-${item.status}`}>
      <Icon size={15} />
      <div>
        <span>{plainifyTaText(item.label)}</span>
        {item.reason ? <small>{plainifyTaText(item.reason)}</small> : null}
      </div>
    </div>
  );
}

function SimMetric({ label, value, tone, title }: { label: string; value: string; tone: string; title?: string }) {
  return (
    <div className={`simMetric tone-${tone}`} title={title}>
      <span>{label}{title ? <BadgeCheck size={11} /> : null}</span>
      <strong>{value}</strong>
    </div>
  );
}
