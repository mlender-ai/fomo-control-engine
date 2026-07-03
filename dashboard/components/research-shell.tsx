"use client";

import Link from "next/link";
import { Play, RefreshCw } from "lucide-react";
import { FormEvent, useEffect, useState } from "react";
import { TerminalMetric, TerminalPanel, TerminalScoreBadge, TerminalTable, TerminalWarning } from "@/components/terminal";
import { api, type DecisionMemory, type ResearchRun } from "@/lib/api";

export function ResearchShell() {
  const [runs, setRuns] = useState<ResearchRun[]>([]);
  const [memories, setMemories] = useState<DecisionMemory[]>([]);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const [creating, setCreating] = useState(false);

  async function load() {
    setLoading(true);
    setError("");
    try {
      const [researchResult, memoryResult] = await Promise.all([api.researchRuns(), api.memories()]);
      setRuns(researchResult.research_runs);
      setMemories(memoryResult.memories);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load research runs");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    void load();
  }, []);

  async function createRun(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    setCreating(true);
    setError("");
    try {
      const result = await api.createResearchRun({
        symbol: String(form.get("symbol") || "BTCUSDT").toUpperCase(),
        timeframe: String(form.get("timeframe") || "4h")
      });
      event.currentTarget.reset();
      await load();
      window.location.href = `/research/${result.research_run_id}`;
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to create research run");
    } finally {
      setCreating(false);
    }
  }

  const latest = runs[0];

  return (
    <div className="page">
      <header className="pageHeader">
        <div>
          <p className="eyebrow">Agentic Research</p>
          <h1>Research Runs</h1>
          <p className="subtle">동일 market snapshot을 Bull, Bear, Risk Guardian, FOMO Gatekeeper 관점에서 검토합니다.</p>
        </div>
        <button className="button secondary" onClick={load} disabled={loading}>
          <RefreshCw size={16} />
          Refresh
        </button>
      </header>

      {error ? <TerminalWarning tone="error">{error}</TerminalWarning> : null}

      <section className="grid four">
        <TerminalMetric label="Total Runs" value={runs.length} tone="info" />
        <TerminalMetric label="Latest Symbol" value={latest?.symbol ?? "-"} delta={latest?.timeframe?.toUpperCase() ?? "No run"} tone="agent" />
        <TerminalMetric label="Latest Entry" value={latest?.entry_score ?? "-"} tone="positive" />
        <TerminalMetric label="Decision Memories" value={memories.length} tone="warning" />
      </section>

      <TerminalPanel title="Create Research Run" subtitle="No order intent is generated; this is a pre-trade review record" status="accent">
        <form className="formGrid" onSubmit={createRun}>
          <input name="symbol" placeholder="BTCUSDT" required />
          <select name="timeframe" defaultValue="4h">
            <option value="1h">1H</option>
            <option value="4h">4H</option>
            <option value="1d">1D</option>
          </select>
          <button className="button" type="submit" disabled={creating}>
            <Play size={16} />
            {creating ? "Running" : "Run Review"}
          </button>
        </form>
      </TerminalPanel>

      <TerminalPanel title="Research Timeline" subtitle="Stored report tree and final FOMO gate label" status={runs.length ? "ok" : "neutral"}>
        {loading ? (
          <div className="terminalEmpty">Loading research runs...</div>
        ) : (
          <TerminalTable<ResearchRun>
            data={runs}
            idKey="research_run_id"
            emptyLabel="No research runs yet"
            columns={[
              {
                key: "symbol",
                header: "Symbol",
                width: 120,
                render: (run) => (
                  <Link href={`/research/${run.research_run_id}`}>
                    <strong>{run.symbol}</strong>
                  </Link>
                )
              },
              { key: "timeframe", header: "TF", width: 70, render: (run) => run.timeframe.toUpperCase() },
              { key: "entry_score", header: "Entry", align: "center", render: (run) => <TerminalScoreBadge score={run.entry_score} type="entry" /> },
              { key: "fomo_index", header: "FOMO", align: "center", render: (run) => <TerminalScoreBadge score={run.fomo_index} type="fomo" /> },
              { key: "bull", header: "Bull", align: "center", render: (run) => agentConfidence(run, "bull_researcher") },
              { key: "bear", header: "Bear", align: "center", render: (run) => agentConfidence(run, "bear_researcher") },
              { key: "risk", header: "Risk", align: "center", render: (run) => agentConfidence(run, "risk_guardian") },
              { key: "final_action_label", header: "Gatekeeper", render: (run) => run.final_action_label },
              { key: "created_at", header: "Created", render: (run) => new Date(run.created_at).toLocaleString() }
            ]}
          />
        )}
      </TerminalPanel>

      <TerminalPanel title="Decision Memory" subtitle="Evidence-backed memory connected to reports and trades" status={memories.length ? "ok" : "neutral"}>
        <TerminalTable<DecisionMemory>
          data={memories.slice(0, 12)}
          idKey="id"
          emptyLabel="No decision memory yet"
          columns={[
            { key: "symbol", header: "Symbol", width: 120, render: (memory) => memory.symbol ?? "GLOBAL" },
            { key: "memory_type", header: "Type", width: 150, render: (memory) => memory.memory_type },
            { key: "summary", header: "Summary", render: (memory) => memory.summary },
            { key: "weight", header: "Weight", align: "end", width: 90, render: (memory) => memory.weight },
            { key: "created_at", header: "Created", render: (memory) => new Date(memory.created_at).toLocaleString() }
          ]}
        />
      </TerminalPanel>
    </div>
  );
}

function agentConfidence(run: ResearchRun, agent: string) {
  const value = run.agents.find((item) => item.agent === agent)?.confidence;
  return value === undefined ? "-" : Math.round(value);
}
