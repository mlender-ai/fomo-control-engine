"use client";

import { useEffect, useState } from "react";
import { TerminalMetric, TerminalPanel, TerminalRawJson, TerminalScoreBadge, TerminalTable, TerminalWarning } from "@/components/terminal";
import { api, type AgentSummary, type ResearchRun } from "@/lib/api";

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

  const bull = findAgent(run, "bull_researcher");
  const bear = findAgent(run, "bear_researcher");
  const risk = findAgent(run, "risk_guardian");
  const gatekeeper = findAgent(run, "fomo_gatekeeper");

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
        <TerminalPanel title="Agent Confidence" subtitle="Structured agent output stored with this run" status="accent">
          <TerminalTable<AgentSummary>
            data={run.agents}
            idKey="id"
            emptyLabel="No agents recorded"
            columns={[
              { key: "agent", header: "Agent", render: (agent) => agent.agent },
              { key: "stance", header: "Stance", render: (agent) => agent.stance },
              { key: "confidence", header: "Confidence", align: "end", render: (agent) => Math.round(agent.confidence) }
            ]}
          />
        </TerminalPanel>
      </section>

      <section className="grid two">
        <AgentPanel title="Bull Researcher" agent={bull} status="ok" />
        <AgentPanel title="Bear Researcher" agent={bear} status="warning" />
      </section>

      <section className="grid two">
        <AgentPanel title="Risk Guardian" agent={risk} status="error" />
        <AgentPanel title="FOMO Gatekeeper" agent={gatekeeper} status="accent" />
      </section>

      <TerminalPanel title="Raw Research Tree" subtitle="Stored input/output payload for reproducibility" status="neutral">
        <TerminalRawJson data={{ raw_input: run.raw_input, raw_output: run.raw_output }} label={`${run.symbol} research tree`} />
      </TerminalPanel>
    </div>
  );
}

function AgentPanel({ title, agent, status }: { title: string; agent?: AgentSummary; status: "ok" | "warning" | "error" | "accent" }) {
  return (
    <TerminalPanel title={title} subtitle={agent?.stance ?? "No output"} status={status}>
      {agent ? (
        <div className="grid">
          <TerminalMetric label="Confidence" value={Math.round(agent.confidence)} tone={status === "error" ? "negative" : status === "warning" ? "warning" : "agent"} />
          <p className="reportText">{agent.text_output}</p>
          <TerminalRawJson data={agent.raw_json} label={`${agent.agent} raw`} />
        </div>
      ) : (
        <div className="terminalEmpty">No agent output recorded</div>
      )}
    </TerminalPanel>
  );
}

function findAgent(run: ResearchRun, agent: string) {
  return run.agents.find((item) => item.agent === agent);
}
