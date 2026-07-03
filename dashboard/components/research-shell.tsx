"use client";

import Link from "next/link";
import { Play, RefreshCw } from "lucide-react";
import { FormEvent, useEffect, useState } from "react";
import { api, type DecisionMemory, type ResearchRun } from "@/lib/api";

export function ResearchShell() {
  const [runs, setRuns] = useState<ResearchRun[]>([]);
  const [memories, setMemories] = useState<DecisionMemory[]>([]);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);

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
    setError("");
    try {
      await api.createResearchRun({
        symbol: String(form.get("symbol") || "BTCUSDT").toUpperCase(),
        timeframe: String(form.get("timeframe") || "4h")
      });
      event.currentTarget.reset();
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to create research run");
    }
  }

  return (
    <div className="page">
      <header className="pageHeader">
        <div>
          <p className="eyebrow">Agentic Research</p>
          <h1>Research Runs</h1>
          <p className="subtle">동일 snapshot을 7개 관점으로 검토합니다. 매수/매도 지시가 아닙니다.</p>
        </div>
        <button className="button secondary" onClick={load} disabled={loading}>
          <RefreshCw size={16} />
          Refresh
        </button>
      </header>
      {error ? <div className="panel dangerText">{error}</div> : null}
      <section className="panel">
        <div className="panelHeader">
          <h2>Create Research Run</h2>
        </div>
        <form className="formGrid" onSubmit={createRun}>
          <input name="symbol" placeholder="BTCUSDT" required />
          <select name="timeframe" defaultValue="4h">
            <option value="1h">1H</option>
            <option value="4h">4H</option>
            <option value="1d">1D</option>
          </select>
          <button className="button" type="submit">
            <Play size={16} />
            Run Review
          </button>
        </form>
      </section>
      <section className="panel">
        <div className="panelHeader">
          <h2>Timeline</h2>
        </div>
        {loading ? (
          <div className="empty">Loading research runs...</div>
        ) : runs.length ? (
          <table className="table">
            <thead>
              <tr>
                <th>Symbol</th>
                <th>Score</th>
                <th>FOMO</th>
                <th>Final Label</th>
                <th>Bull</th>
                <th>Bear</th>
                <th>Created</th>
              </tr>
            </thead>
            <tbody>
              {runs.map((run) => (
                <tr key={run.research_run_id}>
                  <td>
                    <Link href={`/research/${run.research_run_id}`}>
                      <strong>{run.symbol}</strong>
                    </Link>
                  </td>
                  <td>{run.entry_score}</td>
                  <td>{run.fomo_index}</td>
                  <td>{run.final_action_label}</td>
                  <td>{agentConfidence(run, "bull_researcher")}</td>
                  <td>{agentConfidence(run, "bear_researcher")}</td>
                  <td>{new Date(run.created_at).toLocaleString()}</td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : (
          <div className="empty">No research runs yet</div>
        )}
      </section>
      <section className="panel">
        <div className="panelHeader">
          <h2>Decision Memory</h2>
        </div>
        {memories.length ? (
          <table className="table">
            <thead>
              <tr>
                <th>Symbol</th>
                <th>Type</th>
                <th>Summary</th>
                <th>Weight</th>
              </tr>
            </thead>
            <tbody>
              {memories.slice(0, 8).map((memory) => (
                <tr key={memory.id}>
                  <td>{memory.symbol ?? "global"}</td>
                  <td>{memory.memory_type}</td>
                  <td>{memory.summary}</td>
                  <td>{memory.weight}</td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : (
          <div className="empty">No decision memory yet</div>
        )}
      </section>
    </div>
  );
}

function agentConfidence(run: ResearchRun, agent: string) {
  return run.agents.find((item) => item.agent === agent)?.confidence ?? "-";
}
