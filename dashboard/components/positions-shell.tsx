"use client";

import { Activity, LogOut, Plus } from "lucide-react";
import { FormEvent, useEffect, useState } from "react";
import { api, type Position } from "@/lib/api";
import { formatPrice, signedPercent } from "@/lib/format";

export function PositionsShell() {
  const [positions, setPositions] = useState<Position[]>([]);
  const [loading, setLoading] = useState(true);
  const [busyId, setBusyId] = useState("");
  const [error, setError] = useState("");

  async function load() {
    setError("");
    setLoading(true);
    try {
      setPositions(await api.positions());
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load positions");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    void load();
  }, []);

  async function createPosition(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    setError("");
    try {
      await api.createPosition({
        symbol: String(form.get("symbol") || "BTCUSDT").toUpperCase(),
        direction: String(form.get("direction") || "long") as "long" | "short",
        entry_price: Number(form.get("entry_price")),
        quantity: Number(form.get("quantity")),
        leverage: Number(form.get("leverage") || 1),
        memo: String(form.get("memo") || "")
      });
      event.currentTarget.reset();
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to create position");
    }
  }

  async function monitor(positionId: string) {
    setBusyId(positionId);
    setError("");
    try {
      await api.monitor(positionId);
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to monitor position");
    } finally {
      setBusyId("");
    }
  }

  async function exit(position: Position) {
    const exitPrice = position.current_price ?? position.entry_price;
    setBusyId(position.id);
    setError("");
    try {
      await api.exit(position.id, {
        exit_price: exitPrice,
        exit_reason: "대시보드에서 수동 청산 기록",
        memo: ""
      });
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to exit position");
    } finally {
      setBusyId("");
    }
  }

  return (
    <div className="page">
      <header className="pageHeader">
        <div>
          <p className="eyebrow">Position tracking</p>
          <h1>Positions</h1>
          <p className="subtle">진입 기록을 저장하고 현재 점수 변화와 PnL을 점검합니다.</p>
        </div>
      </header>

      {error ? <div className="panel dangerText">{error}</div> : null}

      <section className="panel">
        <div className="panelHeader">
          <h2>Manual Entry</h2>
        </div>
        <form className="formGrid" onSubmit={createPosition}>
          <input name="symbol" placeholder="BTCUSDT" required />
          <select name="direction" defaultValue="long">
            <option value="long">Long</option>
            <option value="short">Short</option>
          </select>
          <input name="entry_price" type="number" step="0.0001" placeholder="Entry price" required />
          <input name="quantity" type="number" step="0.0001" placeholder="Quantity" required />
          <input name="leverage" type="number" step="0.1" defaultValue="1" min="1" />
          <textarea name="memo" placeholder="진입 이유 또는 손절 기준" />
          <button className="button" type="submit">
            <Plus size={16} />
            Save Entry
          </button>
        </form>
      </section>

      <section className="panel">
        <div className="panelHeader">
          <h2>Position List</h2>
        </div>
        {loading ? (
          <div className="empty">Loading positions...</div>
        ) : positions.length ? (
          <table className="table">
            <thead>
              <tr>
                <th>Ticker</th>
                <th>Side</th>
                <th>Entry</th>
                <th>Current</th>
                <th>Leverage</th>
                <th>Size</th>
                <th>PnL</th>
                <th>Liq.</th>
                <th>Score</th>
                <th>Status</th>
                <th>Action</th>
              </tr>
            </thead>
            <tbody>
              {positions.map((position) => (
                <tr key={position.id}>
                  <td>
                    <strong>{position.symbol}</strong>
                  </td>
                  <td>{position.direction}</td>
                  <td>{formatPrice(position.entry_price)}</td>
                  <td>{position.mark_price ? formatPrice(position.mark_price) : position.current_price ? formatPrice(position.current_price) : "-"}</td>
                  <td>{position.leverage}x</td>
                  <td>{position.quantity}</td>
                  <td className={position.pnl_percent >= 0 ? "successText" : "dangerText"}>{signedPercent(position.pnl_percent)}</td>
                  <td>{position.liquidation_price ? formatPrice(position.liquidation_price) : "-"}</td>
                  <td>
                    {position.entry_score ?? "-"} → {position.current_score ?? "-"}
                  </td>
                  <td>{position.status} · {position.source}</td>
                  <td>
                    <button className="button secondary" onClick={() => monitor(position.id)} disabled={position.status !== "open" || busyId === position.id} title="Monitor">
                      <Activity size={16} />
                      Monitor
                    </button>{" "}
                    <button className="button secondary" onClick={() => exit(position)} disabled={position.status !== "open" || busyId === position.id} title="Record Exit">
                      <LogOut size={16} />
                      Record Exit
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : (
          <div className="empty">No positions yet</div>
        )}
      </section>
    </div>
  );
}
