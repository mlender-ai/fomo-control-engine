"use client";

import { useEffect, useState } from "react";
import { api, type ResearchRun } from "@/lib/api";

export function ResearchDetail({ runId }: { runId: string }) {
  const [run, setRun] = useState<ResearchRun | null>(null);
  const [error, setError] = useState("");
  const [showRaw, setShowRaw] = useState(false);

  useEffect(() => {
    api.researchRun(runId).then(setRun).catch((err) => setError(err instanceof Error ? err.message : "Failed to load research run"));
  }, [runId]);

  if (error) return <div className="page"><div className="panel dangerText">{error}</div></div>;
  if (!run) return <div className="page"><div className="empty">Loading research run...</div></div>;

  return (
    <div className="page">
      <header className="pageHeader">
        <div>
          <p className="eyebrow">Research detail</p>
          <h1>{run.symbol}</h1>
          <p className="subtle">{run.final_action_label} · Entry {run.entry_score} · FOMO {run.fomo_index}</p>
        </div>
        <button className="button secondary" onClick={() => setShowRaw((value) => !value)}>
          Raw JSON
        </button>
      </header>
      <section className="grid two">
        <div className="panel">
          <div className="panelHeader">
            <h2>Final Report</h2>
          </div>
          <p className="reportText">{run.summary}</p>
        </div>
        <div className="panel">
          <div className="panelHeader">
            <h2>Score Snapshot</h2>
          </div>
          <div className="metricGrid">
            <div className="metric"><span>Entry</span><strong>{run.entry_score}</strong></div>
            <div className="metric"><span>FOMO</span><strong>{run.fomo_index}</strong></div>
            <div className="metric"><span>State</span><strong>{run.state_label}</strong></div>
          </div>
        </div>
      </section>
      <section>
        <div className="panelHeader">
          <h2>Agent Cards</h2>
        </div>
        <div className="grid three">
          {run.agents.map((agent) => (
            <article className="panel" key={agent.agent}>
              <div className="panelHeader">
                <h3>{agent.agent}</h3>
                <span className="scorePill watch">{Math.round(agent.confidence)}</span>
              </div>
              <p className="subtle">{agent.stance}</p>
              <p className="reportText">{agent.text_output}</p>
            </article>
          ))}
        </div>
      </section>
      {showRaw ? (
        <section className="panel">
          <pre className="reportText">{JSON.stringify({ raw_input: run.raw_input, raw_output: run.raw_output }, null, 2)}</pre>
        </section>
      ) : null}
    </div>
  );
}
