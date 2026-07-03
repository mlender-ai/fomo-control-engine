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
  status: "open" | "closed" | "missing_from_exchange" | "needs_exit_record";
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
  detected_source: string;
  synced_at: string | null;
  memo: string;
  entry_memo: string;
  planned_stop_price: number | null;
  planned_take_profit_price: number | null;
  thesis_text: string;
  opened_at: string;
  closed_at: string | null;
};

export type PositionHealthComponents = {
  thesis_integrity: number;
  chart_structure: number;
  risk_safety: number;
  momentum_volume: number;
  liquidity_funding: number;
};

export type PositionState = {
  position: Position;
  mark_price: number | null;
  pnl_percent: number;
  pnl_amount: number | null;
  liquidation_distance_pct: number | null;
  health_score: number;
  status: "healthy" | "watch" | "risk_rising" | "thesis_weakening" | "critical" | "unknown";
  status_label: string;
  risk_score: number;
  score_change: number;
  entry_score: number;
  current_score: number;
  analysis: {
    position_analysis: {
      symbol: string;
      direction: "long" | "short";
      health_score: number;
      status: string;
      status_label: string;
      thesis_integrity: number;
      chart_structure: number;
      risk_safety: number;
      momentum_volume: number;
      liquidity_funding: number;
      entry_score: number;
      current_score: number;
      score_change: number;
    };
    wyckoff: {
      accumulation_score: number;
      distribution_score: number;
      phase_hint: string;
      spring_candidate: boolean;
      sos_candidate: boolean;
      lps_candidate: boolean;
      structure_comment: string;
    };
    technical: {
      trend: string;
      trend_alignment: string;
      rsi_state: string;
      macd_state: string;
      bollinger_state: string;
      volume_state: string;
      support_status: string;
      resistance_status: string;
      open_interest: string | number;
      funding: string | number;
      break_of_structure: boolean;
      higher_low: boolean;
    };
    risk: {
      liquidation_distance_pct: number | null;
      risk_score: number;
      atr_risk: string;
      drawdown_from_peak_pct: number;
      profit_giveback_pct: number;
      price_distance_from_entry_pct: number | null;
      critical_levels: Array<{ type: string; price: number; meaning: string }>;
    };
    reason_codes: string[];
  };
  score_json: {
    entry_score: number;
    current_score: number;
    score_change: number;
    health_components: PositionHealthComponents;
    entry_breakdown: ScoreBreakdown;
    fomo_index: number;
  };
};

export type PositionSnapshot = {
  id: string;
  position_id: string;
  symbol: string;
  mark_price: number | null;
  pnl_percent: number;
  pnl_amount: number | null;
  liquidation_price: number | null;
  liquidation_distance_pct: number | null;
  health_score: number;
  status_label: string;
  risk_score: number;
  score_json: PositionState["score_json"];
  analysis_json: PositionState["analysis"];
  created_at: string;
};

export type PositionInsight = {
  id: string;
  position_id: string;
  snapshot_id: string | null;
  insight_type: string;
  health_score: number;
  status_label: string;
  input_json: PositionState["analysis"];
  insight_text: string;
  created_at: string;
};

export type PositionEvent = {
  id: string;
  position_id: string;
  event_type: string;
  severity: "low" | "medium" | "high" | "critical" | string;
  title: string;
  description: string;
  data: Record<string, unknown>;
  created_at: string;
};

export type LivePositionPayload = {
  position: Position;
  state: PositionState;
  latest_snapshot: PositionSnapshot;
  latest_insight: PositionInsight | null;
  recent_events: PositionEvent[];
};

export type LivePositionsResponse = {
  provider: string;
  positions: LivePositionPayload[];
  open_count: number;
  needs_exit_record_count: number;
  timestamp: string;
};

export type LivePositionDetail = LivePositionPayload & {
  snapshots: PositionSnapshot[];
  insights: PositionInsight[];
  events: PositionEvent[];
  monitoring_logs: Array<Record<string, unknown>>;
};

export type ChartCandle = {
  time: number;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
};

export type ChartPriceLevel = {
  price: number;
  strength?: "weak" | "medium" | "strong" | string;
  label: string;
};

export type VolumeProfileBin = {
  price_low: number;
  price_high: number;
  volume: number;
  buy_volume_proxy: number;
  sell_volume_proxy: number;
};

export type WyckoffMarker = {
  time: number;
  price: number;
  type: string;
  label: string;
  confidence: number;
};

export type PositionChartAnalysis = {
  position_id: string;
  symbol: string;
  timeframe: string;
  direction: "long" | "short";
  entry_price: number;
  mark_price: number;
  liquidation_price: number | null;
  candles: ChartCandle[];
  price_levels: {
    entry: number;
    mark: number;
    liquidation: number | null;
    support: ChartPriceLevel[];
    resistance: ChartPriceLevel[];
    invalidation: Array<{ price: number; label: string }>;
  };
  indicators: {
    rsi: Array<{ time: number; value: number }>;
    macd: Array<{ time: number; macd: number; signal: number; histogram: number }>;
    bollinger: {
      upper: Array<{ time: number; value: number }>;
      middle: Array<{ time: number; value: number }>;
      lower: Array<{ time: number; value: number }>;
    };
  };
  volume_profile: {
    bins: VolumeProfileBin[];
    poc_price: number;
    value_area_high: number;
    value_area_low: number;
    method: string;
  };
  volume_xray: {
    relative_volume: number;
    volume_state: string;
    spike_detected: boolean;
    climax_candidate: boolean;
    absorption_candidate: boolean;
    rebound_with_volume: boolean;
    notes: string[];
  };
  wyckoff_markers: WyckoffMarker[];
  data_quality: {
    candles: number;
    source: string;
    estimated_volume_profile: boolean;
    last_candle_at: string;
  };
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
  memo: string;
  created_at: string;
};

export type TradeTimeline = {
  trade: Trade;
  snapshots: PositionSnapshot[];
  events: PositionEvent[];
  monitoring_logs: Array<Record<string, unknown>>;
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
  positions?: LivePositionPayload[];
  timestamp?: string;
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
  livePositions: () => request<LivePositionsResponse>("/api/live/positions"),
  syncLivePositions: () =>
    request<BitgetSyncResult>("/api/live/positions/sync", {
      method: "POST"
    }),
  livePosition: (positionId: string) => request<LivePositionDetail>(`/api/live/positions/${positionId}`),
  positionChartAnalysis: (positionId: string, timeframe = "4h") =>
    request<PositionChartAnalysis>(`/api/live/positions/${positionId}/chart-analysis?timeframe=${encodeURIComponent(timeframe)}`),
  analyzeLivePosition: (positionId: string) =>
    request<LivePositionPayload>(`/api/live/positions/${positionId}/analyze`, {
      method: "POST"
    }),
  createPositionInsight: (positionId: string) =>
    request<LivePositionPayload>(`/api/live/positions/${positionId}/insight`, {
      method: "POST"
    }),
  positionSnapshots: (positionId: string) => request<{ snapshots: PositionSnapshot[] }>(`/api/live/positions/${positionId}/snapshots`),
  positionEvents: (positionId: string) => request<{ events: PositionEvent[] }>(`/api/live/positions/${positionId}/events`),
  updatePositionMemo: (
    positionId: string,
    payload: {
      memo?: string;
      entry_memo?: string;
      planned_stop_price?: number | null;
      planned_take_profit_price?: number | null;
      thesis_text?: string;
    }
  ) =>
    request<Position>(`/api/live/positions/${positionId}/memo`, {
      method: "PATCH",
      body: JSON.stringify(payload)
    }),
  recordLiveExit: (positionId: string, payload: { exit_price: number; exit_reason: string; memo: string }) =>
    request<Trade>(`/api/live/positions/${positionId}/record-exit`, {
      method: "POST",
      body: JSON.stringify(payload)
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
  trade: (tradeId: string) => request<Trade>(`/api/trades/${tradeId}`),
  reviewTrade: (tradeId: string) =>
    request<Trade>(`/api/trades/${tradeId}/review`, {
      method: "POST"
    }),
  tradeTimeline: (tradeId: string) => request<TradeTimeline>(`/api/trades/${tradeId}/timeline`),
  updateTradeMemo: (tradeId: string, memo: string) =>
    request<Trade>(`/api/trades/${tradeId}/memo`, {
      method: "PATCH",
      body: JSON.stringify({ memo })
    }),
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
