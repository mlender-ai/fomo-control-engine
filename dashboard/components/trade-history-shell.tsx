"use client";

import Link from "next/link";
import { BrainCircuit, FileClock, NotebookPen, RefreshCw } from "lucide-react";
import { FormEvent, useEffect, useMemo, useState } from "react";
import { TerminalMetric, TerminalPanel, TerminalTable, TerminalWarning } from "@/components/terminal";
import { api, type CalibrationSummary, type JudgmentScore, type Trade, type TradeTimeline } from "@/lib/api";
import { formatPrice, signedPercent } from "@/lib/format";

export function TradeHistoryShell() {
  const [trades, setTrades] = useState<Trade[]>([]);
  const [selectedTradeId, setSelectedTradeId] = useState("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [calibration, setCalibration] = useState<CalibrationSummary | null>(null);
  const [calibrationBusy, setCalibrationBusy] = useState("");

  async function load() {
    setError("");
    setLoading(true);
    try {
      const nextTrades = await api.trades();
      setTrades(nextTrades);
      setSelectedTradeId((current) => current || nextTrades[0]?.id || "");
      void api.reviewCalibration().then(setCalibration).catch(() => undefined);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load trade history");
    } finally {
      setLoading(false);
    }
  }

  async function vetoSuggestion(suggestionId: string) {
    setCalibrationBusy(suggestionId);
    setError("");
    try {
      await api.vetoCalibrationSuggestion(suggestionId);
      setCalibration(await api.reviewCalibration());
    } catch (err) {
      setError(err instanceof Error ? err.message : "Calibration veto failed");
    } finally {
      setCalibrationBusy("");
    }
  }

  useEffect(() => {
    void load();
  }, []);

  const selectedTrade = trades.find((trade) => trade.id === selectedTradeId) ?? trades[0];
  const stats = useMemo(() => summarizeTrades(trades), [trades]);

  return (
    <div className="page">
      <header className="pageHeader">
        <div>
          <p className="eyebrow">Trade History</p>
          <h1>청산 후 복기와 의사결정 기억</h1>
          <p className="subtle">진입/이탈 이유, 점수 변화, 포지션 이벤트를 거래 단위로 다시 봅니다.</p>
        </div>
        <button className="button secondary" onClick={load} disabled={loading}>
          <RefreshCw size={16} />
          Refresh
        </button>
      </header>

      {error ? <TerminalWarning tone="error">{error}</TerminalWarning> : null}

      <section className="grid four">
        <TerminalMetric label="Closed Trades" value={trades.length} tone="info" />
        <TerminalMetric label="Win Rate" value={`${stats.winRate.toFixed(1)}%`} tone={stats.winRate >= 50 ? "positive" : "warning"} />
        <TerminalMetric label="Average PnL" value={signedPercent(stats.averagePnl)} tone={stats.averagePnl >= 0 ? "positive" : "negative"} />
        <TerminalMetric label="Total PnL" value={`${stats.totalPnl.toFixed(2)} USDT`} tone={stats.totalPnl >= 0 ? "positive" : "negative"} />
      </section>

      {calibration ? (
        <section className="grid two">
          <TerminalPanel title="판단 캘리브레이션" subtitle={calibration.sample_warning} status="accent">
            <div className="terminalMetricGrid">
              <TerminalMetric label="전체 판단" value={metricNumber(calibration.totals, "total")} delta={`${metricNumber(calibration.totals, "tested")} tested`} tone="info" />
              <TerminalMetric label="전체 적중률" value={metricPercent(calibration.totals, "accuracy_pct")} tone={metricTone(calibration.totals)} />
              <TerminalMetric label="무효화 적중률" value={metricPercent(calibration.invalidation, "accuracy_pct")} delta={`${metricNumber(calibration.invalidation, "total")} samples`} tone={metricTone(calibration.invalidation)} />
              <TerminalMetric label="익절 도달률" value={metricPercent(calibration.take_profit, "reach_rate_pct")} delta={`${metricNumber(calibration.take_profit, "total")} targets`} tone="warning" />
            </div>
            <CalibrationWeeklyReport calibration={calibration} />
            <ConfidenceCurve rows={calibration.confidence_curve ?? []} />
          </TerminalPanel>

          <TerminalPanel title="파라미터 자율 피드" subtitle="조임 변경은 거부권 창 이후 자동 적용됩니다" status={calibration.suggestions.length ? "warning" : "neutral"}>
            {calibration.suggestion_status_counts ? (
              <div className="terminalMetaRow">
                <span>예정 {calibration.suggestion_status_counts.scheduled ?? 0}</span>
                <span>실험 {calibration.suggestion_status_counts.experiment ?? 0}</span>
                <span>자율 적용 {calibration.suggestion_status_counts.adopted ?? 0}</span>
              </div>
            ) : null}
            {calibration.suggestions.length ? (
              <div className="eventTimeline">
                {calibration.suggestions.map((suggestion) => (
                  <div className={`eventItem severity-${suggestion.status === "adopted" || suggestion.status === "approved" ? "low" : "medium"}`} key={suggestion.id}>
                    <div>
                      <strong>{suggestion.title}</strong>
                      <span>{suggestion.status} · N={suggestion.sample_size}</span>
                    </div>
                    <p>{suggestion.rationale}</p>
                    {["pending", "scheduled", "experiment"].includes(suggestion.status) ? (
                      <div className="actionGroup">
                        <button className="button secondary" onClick={() => vetoSuggestion(suggestion.id)} disabled={calibrationBusy === suggestion.id}>
                          거부
                        </button>
                      </div>
                    ) : null}
                  </div>
                ))}
              </div>
            ) : (
              <div className="terminalEmpty">아직 조정 제안이 없습니다. 표본이 10개 이상 쌓이면 표시됩니다.</div>
            )}
          </TerminalPanel>
        </section>
      ) : null}

      <section className="grid two">
        <TerminalPanel title="Closed Trade Tape" subtitle="청산된 거래만 복기 대상으로 표시합니다" status={trades.length ? "ok" : "neutral"}>
          <TerminalTable<Trade>
            data={trades}
            idKey="id"
            emptyLabel="No closed trades yet"
            columns={[
              {
                key: "symbol",
                header: "Symbol",
                width: 112,
                render: (trade) => (
                  <button className="terminalDisclosure" type="button" onClick={() => setSelectedTradeId(trade.id)}>
                    {trade.symbol}
                  </button>
                )
              },
              { key: "direction", header: "Side", width: 76, render: (trade) => trade.direction.toUpperCase() },
              { key: "entry_price", header: "Entry", align: "end", render: (trade) => formatPrice(trade.entry_price) },
              { key: "exit_price", header: "Exit", align: "end", render: (trade) => formatPrice(trade.exit_price) },
              { key: "pnl_percent", header: "PnL", align: "end", render: (trade) => <span className={trade.pnl_percent >= 0 ? "successText" : "dangerText"}>{signedPercent(trade.pnl_percent)}</span> },
              { key: "score", header: "Score", render: (trade) => `${trade.entry_score ?? "-"} -> ${trade.exit_score ?? "-"}` },
              { key: "created_at", header: "Time", render: (trade) => new Date(trade.created_at).toLocaleString() },
              {
                key: "detail",
                header: "Detail",
                width: 92,
                render: (trade) => (
                  <Link className="terminalDisclosure" href={`/trades/${trade.id}`}>
                    Open
                  </Link>
                )
              }
            ]}
          />
        </TerminalPanel>

        <TerminalPanel title="Selected Trade Review" subtitle={selectedTrade ? `${selectedTrade.symbol} · ${selectedTrade.exit_reason}` : "No trade selected"} status={selectedTrade?.pnl_percent && selectedTrade.pnl_percent < 0 ? "warning" : "neutral"}>
          {selectedTrade ? (
            <div className="grid">
              <div className="terminalMetricGrid">
                <TerminalMetric label="Entry" value={formatPrice(selectedTrade.entry_price)} tone="neutral" />
                <TerminalMetric label="Exit" value={formatPrice(selectedTrade.exit_price)} tone="neutral" />
                <TerminalMetric label="PnL" value={signedPercent(selectedTrade.pnl_percent)} tone={selectedTrade.pnl_percent >= 0 ? "positive" : "negative"} />
                <TerminalMetric label="Amount" value={`${selectedTrade.pnl_amount.toFixed(2)} USDT`} tone={selectedTrade.pnl_amount >= 0 ? "positive" : "negative"} />
                <TerminalMetric label="Hold" value={`${selectedTrade.holding_minutes}m`} tone="info" />
              </div>
              <p className="reviewText">{selectedTrade.review_text || "No review text recorded."}</p>
              <div className="terminalMetricGrid">
                <TerminalMetric label="판단 채점" value={scorecardValue(selectedTrade, "total")} delta={`${scorecardValue(selectedTrade, "tested")} tested`} tone="info" />
                <TerminalMetric label="적중률" value={scorecardPercent(selectedTrade)} tone={scorecardTone(selectedTrade)} />
              </div>
              {selectedTrade.memo ? <p className="memoText">{selectedTrade.memo}</p> : null}
            </div>
          ) : (
            <div className="terminalEmpty">No closed trades yet</div>
          )}
        </TerminalPanel>
      </section>
    </div>
  );
}

export function TradeDetailShell({ tradeId }: { tradeId: string }) {
  const [timeline, setTimeline] = useState<TradeTimeline | null>(null);
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");

  async function load() {
    setError("");
    try {
      setTimeline(await api.tradeTimeline(tradeId));
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load trade timeline");
    }
  }

  useEffect(() => {
    void load();
  }, [tradeId]);

  async function rerunReview() {
    setBusy("review");
    setNotice("");
    setError("");
    try {
      await api.reviewTrade(tradeId);
      await load();
      setNotice("복기 문장을 다시 생성했습니다.");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Review generation failed");
    } finally {
      setBusy("");
    }
  }

  async function saveMemo(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    setBusy("memo");
    setNotice("");
    setError("");
    try {
      await api.updateTradeMemo(tradeId, String(form.get("memo") ?? ""));
      await load();
      setNotice("거래 복기 메모를 저장했습니다.");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Memo save failed");
    } finally {
      setBusy("");
    }
  }

  if (!timeline) {
    return (
      <div className="page">
        {error ? <TerminalWarning tone="error">{error}</TerminalWarning> : null}
        <TerminalPanel title="Loading Trade" subtitle={tradeId} status="neutral">
          <div className="terminalEmpty">Loading trade timeline...</div>
        </TerminalPanel>
      </div>
    );
  }

  const trade = timeline.trade;

  return (
    <div className="page">
      <header className="pageHeader">
        <div>
          <p className="eyebrow">Trade Review Detail</p>
          <h1>{trade.symbol} {trade.direction.toUpperCase()} 복기</h1>
          <p className="subtle">진입과 이탈 사이의 스냅샷/이벤트/메모를 연결해서 실수를 분리합니다.</p>
        </div>
        <div className="actionGroup">
          <Link className="button secondary" href="/trades">
            <FileClock size={16} />
            History
          </Link>
          <button className="button" onClick={rerunReview} disabled={busy === "review"}>
            <BrainCircuit size={16} />
            Rebuild Review
          </button>
        </div>
      </header>

      {error ? <TerminalWarning tone="error">{error}</TerminalWarning> : null}
      {notice ? <TerminalWarning tone="info">{notice}</TerminalWarning> : null}

      <section className="grid four">
        <TerminalMetric label="PnL" value={signedPercent(trade.pnl_percent)} delta={`${trade.pnl_amount.toFixed(2)} USDT`} tone={trade.pnl_percent >= 0 ? "positive" : "negative"} />
        <TerminalMetric label="Entry / Exit" value={`${trade.entry_score ?? "-"} -> ${trade.exit_score ?? "-"}`} delta="score" tone={(trade.exit_score ?? 0) >= (trade.entry_score ?? 0) ? "positive" : "warning"} />
        <TerminalMetric label="Hold Time" value={`${trade.holding_minutes}m`} tone="info" />
        <TerminalMetric label="Snapshots" value={timeline.snapshots.length} delta={`${timeline.events.length} events`} tone="agent" />
      </section>

      <section className="grid two">
        <TerminalPanel title="AI Review" subtitle={trade.exit_reason} status={trade.pnl_percent >= 0 ? "ok" : "warning"}>
          <p className="reviewText">{trade.review_text || "No review text recorded."}</p>
        </TerminalPanel>
        <TerminalPanel title="Review Memo" subtitle="청산 이후 본인이 확인한 이유와 감정 상태를 기록합니다" status="accent">
          <form className="positionMemoForm" onSubmit={saveMemo}>
            <textarea name="memo" defaultValue={trade.memo} rows={8} placeholder="잘한 점, 실수, 다음에 바꿀 기준" />
            <button className="button" type="submit" disabled={busy === "memo"}>
              <NotebookPen size={16} />
              Save Memo
            </button>
          </form>
        </TerminalPanel>
      </section>

      <section className="grid">
        <TerminalPanel title="판단 채점표" subtitle="무효화·익절·와이코프·PRZ 판단을 실제 종료 경로와 대조합니다" status={timeline.judgment_scores.length ? "ok" : "neutral"}>
          <TerminalTable<JudgmentScore>
            data={timeline.judgment_scores}
            idKey="id"
            emptyLabel="채점 가능한 판단 원장이 아직 없습니다."
            columns={[
              { key: "judgment_type", header: "판단", width: 132, render: (score) => judgmentTypeLabel(score.judgment_type) },
              { key: "outcome", header: "결과", width: 96, render: (score) => <span className={outcomeClass(score.outcome)}>{outcomeLabel(score.outcome)}</span> },
              { key: "price", header: "가격", align: "end", width: 112, render: (score) => claimPrice(score) },
              { key: "confidence", header: "신뢰도", align: "end", width: 92, render: (score) => score.confidence ?? "-" },
              { key: "detail", header: "근거", render: (score) => score.detail },
              { key: "created_at", header: "채점 시각", width: 168, render: (score) => new Date(score.created_at).toLocaleString() }
            ]}
          />
        </TerminalPanel>
      </section>

      <section className="grid two">
        <TerminalPanel title="Position Events" subtitle="보유 중 발생한 리스크/상태 이벤트" status={timeline.events.length ? "warning" : "neutral"}>
          {timeline.events.length ? (
            <div className="eventTimeline">
              {timeline.events.map((event) => (
                <div className={`eventItem severity-${event.severity}`} key={event.id}>
                  <div>
                    <strong>{event.title}</strong>
                    <span>{new Date(event.created_at).toLocaleString()} · {event.event_type}</span>
                  </div>
                  <p>{event.description}</p>
                </div>
              ))}
            </div>
          ) : (
            <div className="terminalEmpty">No events recorded for this trade.</div>
          )}
        </TerminalPanel>
        <TerminalPanel title="Snapshots" subtitle="청산 전 상태 기록" status={timeline.snapshots.length ? "ok" : "neutral"}>
          <TerminalTable
            data={timeline.snapshots}
            idKey="id"
            emptyLabel="No snapshots recorded"
            columns={[
              { key: "created_at", header: "Time", render: (snapshot) => new Date(snapshot.created_at).toLocaleString() },
              { key: "health_score", header: "Health", align: "end", render: (snapshot) => `${snapshot.health_score}/100` },
              { key: "risk_score", header: "Risk", align: "end", render: (snapshot) => `${snapshot.risk_score}/100` },
              { key: "pnl_percent", header: "PnL", align: "end", render: (snapshot) => signedPercent(snapshot.pnl_percent) },
              { key: "status_label", header: "Status", render: (snapshot) => snapshot.status_label }
            ]}
          />
        </TerminalPanel>
      </section>
    </div>
  );
}

function summarizeTrades(trades: Trade[]) {
  const totalPnl = trades.reduce((sum, trade) => sum + trade.pnl_amount, 0);
  const averagePnl = trades.length ? trades.reduce((sum, trade) => sum + trade.pnl_percent, 0) / trades.length : 0;
  const winRate = trades.length ? (trades.filter((trade) => trade.pnl_percent > 0).length / trades.length) * 100 : 0;
  return { totalPnl, averagePnl, winRate };
}

function CalibrationWeeklyReport({ calibration }: { calibration: CalibrationSummary }) {
  const weekly = calibration.weekly_report;
  if (!weekly) {
    return null;
  }
  const totals = asRecord(weekly.totals);
  const highlights = Array.isArray(weekly.highlights) ? weekly.highlights.map(String).slice(0, 3) : [];
  return (
    <div className="calibrationBlock">
      <div className="terminalMetaRow">
        <strong>주간 성적표</strong>
        <span>검증 {metricNumber(totals, "tested")}</span>
        <span>적중률 {metricPercent(totals, "accuracy_pct")}</span>
      </div>
      {highlights.length ? (
        <ul className="compactList">
          {highlights.map((item) => (
            <li key={item}>{item}</li>
          ))}
        </ul>
      ) : (
        <div className="terminalEmpty compact">최근 7일 표본이 아직 충분하지 않습니다.</div>
      )}
    </div>
  );
}

function ConfidenceCurve({ rows }: { rows: Array<Record<string, unknown>> }) {
  if (!rows.length) {
    return (
      <div className="calibrationBlock">
        <div className="terminalEmpty compact">신뢰도 곡선은 confidence가 있는 판단 표본이 쌓이면 표시됩니다.</div>
      </div>
    );
  }
  return (
    <div className="calibrationBlock">
      <div className="terminalMetaRow">
        <strong>신뢰도 곡선</strong>
        <span>N&lt;10 결론 보류</span>
      </div>
      <div className="confidenceCurve">
        {rows.slice(0, 8).map((row) => (
          <div className={`confidenceBucket state-${String(row.calibration_state ?? "insufficient_sample")}`} key={String(row.bucket)}>
            <span>{String(row.bucket)}</span>
            <strong>{metricPercent(row, "accuracy_pct")}</strong>
            <small>N={metricNumber(row, "tested")} · {String(row.conclusion ?? "표본 부족")}</small>
          </div>
        ))}
      </div>
    </div>
  );
}

function metricNumber(bucket: Record<string, unknown>, key: string) {
  const value = bucket[key];
  return typeof value === "number" ? String(value) : "-";
}

function metricPercent(bucket: Record<string, unknown>, key: string) {
  const value = bucket[key];
  return typeof value === "number" ? `${value.toFixed(1)}%` : "표본 부족";
}

function metricTone(bucket: Record<string, unknown>) {
  const value = bucket.accuracy_pct;
  if (typeof value !== "number") {
    return "neutral" as const;
  }
  if (value >= 70) {
    return "positive" as const;
  }
  if (value >= 50) {
    return "warning" as const;
  }
  return "negative" as const;
}

function scorecardValue(trade: Trade, key: string) {
  const value = trade.judgment_scorecard?.[key as keyof typeof trade.judgment_scorecard];
  return typeof value === "number" ? String(value) : "0";
}

function scorecardPercent(trade: Trade) {
  const value = trade.judgment_scorecard?.accuracy_pct;
  return typeof value === "number" ? `${value.toFixed(1)}%` : "표본 부족";
}

function scorecardTone(trade: Trade) {
  const value = trade.judgment_scorecard?.accuracy_pct;
  if (typeof value !== "number") {
    return "neutral" as const;
  }
  if (value >= 70) {
    return "positive" as const;
  }
  if (value >= 50) {
    return "warning" as const;
  }
  return "negative" as const;
}

function judgmentTypeLabel(type: string) {
  const labels: Record<string, string> = {
    invalidation: "무효화",
    take_profit: "익절 후보",
    wyckoff_event: "와이코프",
    liquidity_sweep: "유동성 스윕",
    harmonic_prz: "하모닉 PRZ",
    analyst_briefing: "브리핑 스탠스"
  };
  return labels[type] ?? type;
}

function outcomeLabel(outcome: string) {
  const labels: Record<string, string> = {
    correct: "적중",
    wrong: "오판",
    whipsaw: "휩쏘",
    untested: "미검증"
  };
  return labels[outcome] ?? outcome;
}

function outcomeClass(outcome: string) {
  if (outcome === "correct") {
    return "successText";
  }
  if (outcome === "wrong") {
    return "dangerText";
  }
  if (outcome === "whipsaw") {
    return "warningText";
  }
  return "mutedText";
}

function claimPrice(score: JudgmentScore) {
  const price = score.claim.price;
  if (typeof price === "number") {
    return formatPrice(price);
  }
  const low = score.claim.low;
  const high = score.claim.high;
  if (typeof low === "number" && typeof high === "number") {
    return `${formatPrice(low)}~${formatPrice(high)}`;
  }
  return "-";
}

function asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value) ? (value as Record<string, unknown>) : {};
}
