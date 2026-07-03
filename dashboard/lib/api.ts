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
  provider: string;
  data_quality: {
    ohlcv_ok: boolean;
    funding_ok: boolean;
    open_interest_ok: boolean;
    min_candles_met: boolean;
    fallback_used: boolean;
    candles: number;
    last_candle_at: string | null;
  };
  created_at: string;
};

export type Position = {
  id: string;
  symbol: string;
  direction: "long" | "short";
  entry_price: number;
  quantity: number;
  leverage: number;
  status: "open" | "closed" | "missing_from_exchange";
  entry_score: number | null;
  current_score: number | null;
  current_price: number | null;
  mark_price: number | null;
  pnl_percent: number;
  unrealized_pl: number | null;
  liquidation_price: number | null;
  margin_mode: string | null;
  position_mode: string | null;
  margin_ratio: number | null;
  break_even_price: number | null;
  source: string;
  synced_at: string | null;
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

export type SystemStatus = {
  app: string;
  status: string;
  service: string;
  environment: string;
  market_data_provider: string;
  database: string;
  database_url: string;
  bitget_public_api: string;
  bitget_private_api: string;
  default_symbols: string[];
  timestamp: string;
};

export type BitgetConnectionTest = {
  provider: string;
  public_market_data: { ok: boolean; sample_symbol: string; candles: number; error?: string };
  funding_rate: { ok: boolean; value: number | null };
  open_interest: { ok: boolean; value: number | null };
  private_positions: { status: string; ok: boolean; count: number; error?: string };
};

export type BitgetSyncResult = {
  provider: string;
  status: string;
  synced: number;
  created: number;
  updated: number;
  missing_from_exchange: number;
  error?: string;
};

export type AgentSummary = {
  id: string;
  agent: string;
  stance: string;
  confidence: number;
  text_output: string;
  raw_json: Record<string, unknown>;
};

export type ResearchRun = {
  research_run_id: string;
  symbol: string;
  timeframe: string;
  entry_score: number;
  fomo_index: number;
  state_label: string;
  final_action_label: string;
  summary: string;
  agents: AgentSummary[];
  created_at: string;
  raw_input?: Record<string, unknown>;
  raw_output?: Record<string, unknown>;
};

export type ShadowProfile = {
  shadow_id: string;
  created_at: string;
  total_trades: number;
  profitable_trades: number;
  losing_trades: number;
  profile_text: string;
  rules: Array<Record<string, unknown>>;
  fomo_patterns: Array<Record<string, unknown>>;
  common_mistakes: Array<Record<string, unknown>>;
  attribution: Record<string, unknown>;
};

export type ValidationRun = {
  id: string;
  strategy_type: string;
  symbol: string;
  timeframe: string;
  params: Record<string, unknown>;
  summary: Record<string, number>;
  results: Record<string, unknown>;
  warnings: string[];
  created_at: string;
};

export type DecisionMemory = {
  id: string;
  symbol: string | null;
  memory_type: string;
  summary: string;
  evidence: Record<string, unknown>;
  weight: number;
  created_at: string;
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
  systemStatus: () => request<SystemStatus>("/api/system/status"),
  testBitgetConnection: () =>
    request<BitgetConnectionTest>("/api/system/bitget/test-connection", {
      method: "POST"
    }),
  syncBitgetPositions: () =>
    request<BitgetSyncResult>("/api/account/bitget/sync-positions", {
      method: "POST"
    }),
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
  trades: () => request<Trade[]>("/api/trades"),
  createResearchRun: (payload: { symbol: string; timeframe: string; mode?: string }) =>
    request<ResearchRun>("/api/research-runs", {
      method: "POST",
      body: JSON.stringify({ mode: "entry_review", ...payload })
    }),
  researchRuns: () => request<{ research_runs: ResearchRun[] }>("/api/research-runs"),
  researchRun: (runId: string) => request<ResearchRun>(`/api/research-runs/${runId}`),
  extractShadow: () =>
    request<ShadowProfile>("/api/shadow/extract", {
      method: "POST",
      body: JSON.stringify({ min_trades: 10, min_profitable_trades: 5 })
    }),
  shadowProfiles: () => request<{ shadow_profiles: ShadowProfile[] }>("/api/shadow"),
  runValidation: (payload?: { symbol?: string; timeframe?: string }) =>
    request<ValidationRun>("/api/validation/run", {
      method: "POST",
      body: JSON.stringify({
        strategy_type: "entry_score_threshold",
        symbol: payload?.symbol ?? "BTCUSDT",
        timeframe: payload?.timeframe ?? "4h",
        params: { entry_score_min: 75, risk_score_max: 60, fomo_index_max: 70 },
        validation: {
          monte_carlo: { n_simulations: 500, seed: 42 },
          bootstrap: { n_bootstrap: 500, confidence: 0.95, seed: 42 },
          walk_forward: { n_windows: 5 }
        }
      })
    }),
  validationRuns: () => request<{ validation_runs: ValidationRun[] }>("/api/validation/runs"),
  memories: () => request<{ memories: DecisionMemory[] }>("/api/memory")
};
