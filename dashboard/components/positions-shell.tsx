"use client";

import { Activity, LogOut, Plus, RefreshCw } from "lucide-react";
import { FormEvent, useEffect, useMemo, useState } from "react";
import { TerminalMetric, TerminalPanel, TerminalTable, TerminalWarning } from "@/components/terminal";
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

  async function recordExit(position: Position) {
    const exitPrice = position.current_price ?? position.mark_price ?? position.entry_price;
    setBusyId(position.id);
    setError("");
    try {
      await api.exit(position.id, {
        exit_price: exitPrice,
        exit_reason: "대시보드에서 내부 청산 기록",
        memo: "Read-only 기록 업데이트이며 거래소 주문이 아닙니다."
      });
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to record exit");
    } finally {
      setBusyId("");
    }
  }

  const openPositions = useMemo(() => positions.filter((position) => position.status === "open"), [positions]);
  const totalUnrealized = useMemo(
    () => positions.reduce((sum, position) => sum + (position.unrealized_pl ?? 0), 0),
    [positions]
  );
  const maxRisk = useMemo(
    () => Math.max(0, ...positions.map((position) => position.margin_ratio ?? 0)),
    [positions]
  );

  return (
    <div className="page">
      <header className="pageHeader">
        <div>
          <p className="eyebrow">Position tracking</p>
          <h1>Positions</h1>
          <p className="subtle">거래소 read-only position sync와 수동 진입 기록을 한 화면에서 추적합니다.</p>
        </div>
        <button className="button secondary" onClick={load} disabled={loading}>
          <RefreshCw size={16} />
          Refresh
        </button>
      </header>

      {error ? <TerminalWarning tone="error">{error}</TerminalWarning> : null}

      <section className="grid four">
        <TerminalMetric label="Open Positions" value={openPositions.length} tone={openPositions.length ? "warning" : "neutral"} />
        <TerminalMetric label="Tracked Total" value={positions.length} tone="info" />
        <TerminalMetric label="Unrealized PnL" value={`${totalUnrealized.toFixed(2)} USDT`} tone={totalUnrealized >= 0 ? "positive" : "negative"} />
        <TerminalMetric label="Max Margin Ratio" value={maxRisk ? maxRisk.toFixed(4) : "-"} tone={maxRisk > 0.8 ? "negative" : "neutral"} />
      </section>

      <TerminalPanel title="Manual Entry Record" subtitle="Local journal input only; this does not submit an exchange order" status="accent">
        <form className="formGrid" onSubmit={createPosition}>
          <input name="symbol" placeholder="BTCUSDT" required />
          <select name="direction" defaultValue="long">
            <option value="long">Long</option>
            <option value="short">Short</option>
          </select>
          <input name="entry_price" type="number" step="0.0001" placeholder="Entry price" required />
          <input name="quantity" type="number" step="0.0001" placeholder="Quantity" required />
          <input name="leverage" type="number" step="0.1" defaultValue="1" min="1" />
          <textarea name="memo" placeholder="진입 이유, 무효화 기준, 감정 상태" />
          <button className="button" type="submit">
            <Plus size={16} />
            Save Entry Record
          </button>
        </form>
      </TerminalPanel>

      <TerminalPanel title="Position Monitor" subtitle="Monitor updates score and PnL; record exit only writes an internal journal row" status={openPositions.length ? "warning" : "neutral"}>
        {loading ? (
          <div className="terminalEmpty">Loading positions...</div>
        ) : (
          <TerminalTable<Position>
            data={positions}
            idKey="id"
            emptyLabel="No positions yet"
            columns={[
              { key: "symbol", header: "Ticker", width: 112, render: (position) => <strong>{position.symbol}</strong> },
              { key: "direction", header: "Side", width: 76, render: (position) => position.direction.toUpperCase() },
              { key: "entry_price", header: "Entry", align: "end", render: (position) => formatPrice(position.entry_price) },
              { key: "mark_price", header: "Mark", align: "end", render: (position) => position.mark_price ? formatPrice(position.mark_price) : position.current_price ? formatPrice(position.current_price) : "-" },
              { key: "leverage", header: "Lev", align: "end", width: 72, render: (position) => `${position.leverage}x` },
              { key: "quantity", header: "Size", align: "end", render: (position) => position.quantity },
              { key: "pnl_percent", header: "PnL", align: "end", render: (position) => <span className={position.pnl_percent >= 0 ? "successText" : "dangerText"}>{signedPercent(position.pnl_percent)}</span> },
              { key: "liquidation_price", header: "Liq", align: "end", render: (position) => position.liquidation_price ? formatPrice(position.liquidation_price) : "-" },
              { key: "score", header: "Score", render: (position) => `${position.entry_score ?? "-"} -> ${position.current_score ?? "-"}` },
              { key: "status", header: "Status", render: (position) => `${position.status} · ${position.source}` },
              {
                key: "actions",
                header: "Record",
                width: 260,
                render: (position) => (
                  <div className="actionGroup">
                    <button className="button secondary" onClick={() => monitor(position.id)} disabled={position.status !== "open" || busyId === position.id} title="Refresh monitoring snapshot">
                      <Activity size={15} />
                      Monitor
                    </button>
                    <button className="button secondary" onClick={() => recordExit(position)} disabled={position.status !== "open" || busyId === position.id} title="Record internal exit only">
                      <LogOut size={15} />
                      Record Exit
                    </button>
                  </div>
                )
              }
            ]}
          />
        )}
      </TerminalPanel>
    </div>
  );
}
