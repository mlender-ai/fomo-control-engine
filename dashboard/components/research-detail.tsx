"use client";

import { useEffect, useState } from "react";
import { TerminalMetric, TerminalPanel, TerminalRawJson, TerminalScoreBadge, TerminalTable, TerminalWarning } from "@/components/terminal";
import { api, type ResearchRun, type RuleCheckSummary } from "@/lib/api";

export function ResearchDetail({ runId }: { runId: string }) {
  const [run, setRun] = useState<ResearchRun | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    api.researchRun(runId).then(setRun).catch((err) => setError(err instanceof Error ? err.message : "Failed to load research run"));
  }, [runId]);

  if (error) {
    return (
      <div className="page">
        <TerminalWarning tone="error">{error}</TerminalWarning>
      </div>
    );
  }

  if (!run) {
    return (
      <div className="page">
        <div className="terminalEmpty">Loading research run...</div>
      </div>
    );
  }

  const bull = findCheck(run, "bull_case");
  const bear = findCheck(run, "bear_case");
  const risk = findCheck(run, "risk_guardian");
  const gatekeeper = findCheck(run, "fomo_gate");

  return (
    <div className="page">
      <header className="pageHeader">
        <div>
          <p className="eyebrow">Research detail</p>
          <h1>{run.symbol}</h1>
          <p className="subtle">{run.final_action_label} · Entry {run.entry_score} · FOMO {run.fomo_index} · {new Date(run.created_at).toLocaleString()}</p>
        </div>
        <TerminalScoreBadge score={run.entry_score} type="entry" />
      </header>

      <section className="grid four">
        <TerminalMetric label="Entry Score" value={run.entry_score} tone="positive" />
        <TerminalMetric label="FOMO Index" value={run.fomo_index} tone={run.fomo_index >= 70 ? "warning" : "neutral"} />
        <TerminalMetric label="State" value={run.state_label} tone="info" mono={false} />
        <TerminalMetric label="Gate" value={run.final_action_label} tone="agent" mono={false} />
      </section>

      <section className="grid two">
        <TerminalPanel title="Final Gatekeeper Report" subtitle="Decision review summary, not an order instruction" status={run.fomo_index >= 70 ? "warning" : "ok"}>
          <p className="reportText">{run.summary}</p>
        </TerminalPanel>
        <TerminalPanel title="Rule Checklist" subtitle="Deterministic checklist output stored with this run" status="accent">
          <TerminalTable<RuleCheckSummary>
            data={run.checklists}
            idKey="id"
            emptyLabel="No checklist recorded"
            columns={[
              { key: "check", header: "Check", render: (check) => check.check },
              { key: "stance", header: "Stance", render: (check) => check.stance },
              { key: "rule_score", header: "Rule Score", align: "end", render: (check) => Math.round(check.rule_score) }
            ]}
          />
        </TerminalPanel>
      </section>

      <section className="grid two">
        <RuleCheckPanel title="Bull Case" check={bull} status="ok" />
        <RuleCheckPanel title="Bear Case" check={bear} status="warning" />
      </section>

      <section className="grid two">
        <RuleCheckPanel title="Risk Guard" check={risk} status="error" />
        <RuleCheckPanel title="FOMO Gate" check={gatekeeper} status="accent" />
      </section>

      <TerminalPanel title="Raw Research Tree" subtitle="Stored input/output payload for reproducibility" status="neutral">
        <TerminalRawJson data={{ raw_input: run.raw_input, raw_output: run.raw_output }} label={`${run.symbol} research tree`} />
      </TerminalPanel>
    </div>
  );
}

function RuleCheckPanel({ title, check, status }: { title: string; check?: RuleCheckSummary; status: "ok" | "warning" | "error" | "accent" }) {
  return (
    <TerminalPanel title={title} subtitle={check?.stance ?? "No output"} status={status}>
      {check ? (
        <div className="grid">
          <TerminalMetric label="Rule Score" value={Math.round(check.rule_score)} tone={status === "error" ? "negative" : status === "warning" ? "warning" : "agent"} />
          <p className="reportText">{check.text_output}</p>
          <TerminalRawJson data={check.raw_json} label={`${check.check} raw`} />
        </div>
      ) : (
        <div className="terminalEmpty">No checklist output recorded</div>
      )}
    </TerminalPanel>
  );
}

function findCheck(run: ResearchRun, check: string) {
  return run.checklists.find((item) => item.check === check);
}
