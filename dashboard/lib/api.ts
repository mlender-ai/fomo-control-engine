export const API_BASE_URL = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://127.0.0.1:8875";

export type ScoreBreakdown = {
  structure: number;
  volume: number;
  liquidity: number;
  momentum: number;
  risk: number;
  fomo: number;
};

export type Report = {
  id: string;
  symbol: string;
  timeframe: string;
  price: number;
  change_24h: number;
  entry_score: number;
  scores: ScoreBreakdown;
  state_label: string;
  raw_json: Record<string, unknown>;
  report: string;
  created_at: string;
};

export type Position = {
  id: string;
  symbol: string;
  direction: "long" | "short";
  entry_price: number;
  quantity: number;
  leverage: number;
  status: "open" | "closed";
  entry_score: number | null;
  current_score: number | null;
  current_price: number | null;
  pnl_percent: number;
  memo: string;
  opened_at: string;
  closed_at: string | null;
};

export type Trade = {
  id: string;
  position_id: string;
  symbol: string;
  direction: "long" | "short";
  entry_price: number;
  exit_price: number;
  quantity: number;
  pnl_percent: number;
  pnl_amount: number;
  entry_score: number | null;
  exit_score: number | null;
  holding_minutes: number;
  exit_reason: string;
  review_text: string;
  created_at: string;
};

export type MarketSummary = {
  reports: Report[];
  positions: Position[];
  trades: Trade[];
  market_data_provider: string;
};

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE_URL}${path}`, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...(init?.headers ?? {})
    },
    cache: "no-store"
  });

  if (!response.ok) {
    let message = `API request failed: ${response.status}`;
    try {
      const payload = await response.json();
      if (typeof payload.detail === "string") {
        message = payload.detail;
      }
    } catch {
      // Keep the status-based message when the response is not JSON.
    }
    throw new Error(message);
  }

  return response.json() as Promise<T>;
}

export const api = {
  summary: () => request<MarketSummary>("/api/market/summary"),
  report: (symbol: string) => request<Report>(`/api/reports/${symbol}`),
  createReport: (symbol: string) =>
    request<Report>("/api/reports", {
      method: "POST",
      body: JSON.stringify({ symbol, timeframe: "4h" })
    }),
  positions: () => request<Position[]>("/api/positions"),
  createPosition: (payload: {
    symbol: string;
    direction: "long" | "short";
    entry_price: number;
    quantity: number;
    leverage: number;
    memo: string;
  }) =>
    request<Position>("/api/positions", {
      method: "POST",
      body: JSON.stringify(payload)
    }),
  monitor: (positionId: string) =>
    request(`/api/positions/${positionId}/monitor`, {
      method: "POST"
    }),
  exit: (positionId: string, payload: { exit_price: number; exit_reason: string; memo: string }) =>
    request<Trade>(`/api/positions/${positionId}/exit`, {
      method: "POST",
      body: JSON.stringify(payload)
    }),
  trades: () => request<Trade[]>("/api/trades")
};
