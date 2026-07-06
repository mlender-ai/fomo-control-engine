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
  entry_direction_score: number | null;
  current_score: number | null;
  current_price: number | null;
  mark_price: number | null;
  pnl_percent: number;
  unrealized_pl: number | null;
  margin_size: number | null;
  pnl_source: "exchange" | "computed";
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
  scenario_id: string | null;
  opened_at: string;
  closed_at: string | null;
};

export type PositionHealthComponents = {
  survival: number;
  pnl_state: number;
  thesis_integrity: number;
  structure: number;
  flow: number;
  chart_structure: number;
  risk_safety: number;
  momentum_volume: number;
  liquidity_funding: number;
  pnl_protection: number;
  liquidation_buffer: number;
  direction_alignment: number;
  formula_version: string;
};

export type PositionState = {
  position: Position;
  as_of: string;
  mark_price: number | null;
  pnl_percent: number;
  pnl_amount: number | null;
  pnl_source: "exchange" | "computed";
  liquidation_distance_pct: number | null;
  health_score: number;
  status: "healthy" | "watch" | "risk_rising" | "thesis_weakening" | "critical" | "unknown";
  status_label: string;
  severity_rank: number;
  risk_score: number;
  score_change: number;
  entry_direction_score: number;
  current_direction_score: number;
  thesis_delta: number;
  entry_score: number;
  current_score: number;
  analysis: {
    position_analysis: {
      symbol: string;
      direction: "long" | "short";
      health_score: number;
      status: string;
      status_label: string;
      severity_rank: number;
      survival: number;
      pnl_state: number;
      thesis_integrity: number;
      structure: number;
      flow: number;
      chart_structure: number;
      risk_safety: number;
      momentum_volume: number;
      liquidity_funding: number;
      pnl_protection: number;
      liquidation_buffer: number;
      direction_alignment: number;
      health_formula_version: string;
      entry_score: number;
      current_score: number;
      score_change: number;
      entry_direction_score: number;
      current_direction_score: number;
      thesis_delta: number;
    };
    wyckoff: {
      accumulation_score: number;
      distribution_score: number;
      phase?: string;
      phase_hint: string;
      side?: "accumulation" | "distribution" | "neutral" | string;
      evidence_event_ids?: string[];
      spring_candidate: boolean;
      sos_candidate: boolean;
      lps_candidate: boolean;
      test_candidate?: boolean;
      utad_candidate?: boolean;
      sow_candidate?: boolean;
      lpsy_candidate?: boolean;
      range?: WyckoffRange | null;
      events?: WyckoffMarker[];
      mtf?: WyckoffMtf;
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
    derivatives?: DerivativesContext;
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
  as_of: string;
  mark_price: number | null;
  pnl_percent: number;
  pnl_amount: number | null;
  pnl_source: "exchange" | "computed";
  liquidation_price: number | null;
  liquidation_distance_pct: number | null;
  health_score: number;
  severity_rank: number;
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
  as_of: string;
  health_score: number;
  status_label: string;
  input_json: PositionState["analysis"];
  action_plan: PositionActionPlan;
  insight_text: string;
  insight_source: "llm" | "template" | "fallback_template" | string;
  fallback_reason: string | null;
  auto_generated: boolean;
  age_minutes: number | null;
  is_stale: boolean;
  price_drift_pct: number | null;
  basis_mark_price: number | null;
  stale_reasons: string[];
  created_at: string;
};

export type PositionActionPlanItem = {
  price: number | null;
  basis: string;
  distance_pct: number | null;
  action: string;
};

export type PositionWatchTrigger = {
  condition: string;
  meaning: string;
};

export type PositionActionPlan = {
  as_of: string;
  mark_price: number | null;
  invalidation: PositionActionPlanItem | null;
  engine_invalidation: PositionActionPlanItem | null;
  take_profit: PositionActionPlanItem[];
  watch_triggers: PositionWatchTrigger[];
  liquidation: {
    price: number | null;
    warning: string | null;
  };
  headline_action?: string | null;
};

export type AnalystEvidence = {
  engine: string;
  claim: string;
  direction: "long" | "short" | "neutral" | string;
  weight: number;
  confidence: number;
  as_of: string | null;
  score?: number;
  source?: string;
  calibration?: {
    sample_size: number;
    accuracy_pct: number | null;
    factor: number;
    applied: boolean;
  };
  stale?: boolean;
  stale_minutes?: number | null;
};

export type AnalystConfluence = {
  symbol: string;
  timeframe: string;
  generated_at: string;
  data_as_of: string | null;
  max_engine_age_minutes: number | null;
  stance: "long_leaning" | "short_leaning" | "conflicted" | "insufficient" | string;
  stance_label: string;
  composite_score: number;
  long_score: number;
  short_score: number;
  long_evidence: AnalystEvidence[];
  short_evidence: AnalystEvidence[];
  counter_evidence: AnalystEvidence[];
  evidence_count: number;
  neutral_evidence?: AnalystEvidence[];
  calibration_policy?: Record<string, unknown>;
};

export type AnalystBriefing = {
  symbol: string;
  timeframe: string;
  context: "pre_entry" | "position" | string;
  briefing_source: "deterministic" | "llm" | string;
  confluence: AnalystConfluence;
  scenario: string[];
  hit_rates: string[];
  text: string;
  llm_text: string | null;
  llm_source: string;
  warnings: string[];
};

export type InsightStatus = {
  has_insight: boolean;
  is_stale: boolean;
  age_minutes: number | null;
  price_drift_pct: number | null;
  reasons: string[];
  message: string;
  insight_created_at: string | null;
  current_snapshot_created_at: string;
  current_as_of: string;
  generated_for: {
    snapshot_id: string | null;
    as_of: string;
    mark_price: number | null;
    pnl_percent: number | null;
    health_score: number;
    status_label: string;
  } | null;
  current: {
    snapshot_id: string;
    as_of: string;
    mark_price: number | null;
    pnl_percent: number;
    health_score: number;
    status_label: string;
    mark_delta_pct?: number | null;
    pnl_delta_points?: number | null;
    health_delta?: number | null;
  };
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
  action_plan?: PositionActionPlan | null;
  analyst_briefing?: AnalystBriefing | null;
  latest_insight: PositionInsight | null;
  insight_status: InsightStatus;
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
  session?: string | null;
  is_regular_session?: boolean | null;
};

export type ChartPriceLevel = {
  price: number;
  score: number;
  touches: number;
  last_touch_at: string;
  kind: "support" | "resistance" | string;
  sources: string[];
  strength?: "weak" | "medium" | "strong" | string;
  label: string;
};

export type ChartInvalidationLevel = {
  price: number | null;
  label: string;
  source?: string;
  score?: number;
  touches?: number;
  last_touch_at?: string;
  kind?: string;
  sources?: string[];
  strength?: "weak" | "medium" | "strong" | string;
};

export type VolumeProfileBin = {
  price_low: number;
  price_high: number;
  volume: number;
  method: "trade_fills" | "ohlcv_estimated" | "mixed" | string;
  buy_volume?: number;
  sell_volume?: number;
  delta?: number;
};

export type TradeFlowBucket = {
  time: number;
  buy_volume: number;
  sell_volume: number;
  delta: number;
  trades: number;
  method: "trade_fills" | string;
};

export type CvdPoint = {
  time: number;
  value: number;
  delta: number;
  method: "trade_fills" | string;
};

export type LiquidityPool = {
  id: string;
  price: number;
  kind: "eqh" | "eql" | "old_high" | "old_low" | string;
  touch_count: number;
  touches?: number;
  first_seen: string;
  last_touch_at: string;
  swept: boolean;
  swept_at: string | null;
  score: number;
  side: "buy_side" | "sell_side" | string;
  grade: "Weak" | "Mid" | "Strong" | string;
  label: string;
};

export type LiquiditySweep = {
  id: string;
  type: "liquidity_sweep" | "htf_range_sweep" | string;
  side: "buy_side" | "sell_side" | string;
  pool_id: string;
  pool_kind: string;
  pool_price: number;
  price: number;
  wick_extreme: number;
  time: string;
  timestamp: number;
  return_at: string;
  return_candles: number;
  depth_pct: number;
  depth_atr: number;
  volume_ratio: number;
  volume_confirmed: boolean;
  confirmed: boolean;
  status: "confirmed" | "unconfirmed" | string;
  confidence: number;
  grade: "Weak" | "Mid" | "Strong" | string;
  expected_move: "up" | "down" | string;
  wyckoff_equivalent: "spring_candidate" | "utad_candidate" | string;
  label: string;
  basis: string;
  components: Record<string, number>;
};

export type LiquidityStructureShift = {
  state: string;
  event: "BOS" | "CHoCH" | null | string;
  direction?: "up" | "down" | string;
  level?: number;
  close?: number;
  trend_before?: string;
  label?: string;
};

export type LiquidityDealingRange = {
  source: string;
  high: number;
  low: number;
  midpoint: number;
  position_pct: number;
  zone: "deep_premium" | "premium" | "equilibrium" | "discount" | "deep_discount" | string;
  label: string;
} | null;

export type LiquidityContext = {
  method: string;
  as_of: string | null;
  reference_price: number | null;
  pools: LiquidityPool[];
  sweeps: LiquiditySweep[];
  rejected_sweeps: LiquiditySweep[];
  htf_range_sweeps: LiquiditySweep[];
  structure_shift: LiquidityStructureShift;
  dealing_range: LiquidityDealingRange;
  wyckoff_crosscheck: Record<string, unknown>;
  limits: Record<string, unknown>;
  notes: string[];
};

export type WyckoffMarker = {
  id?: string;
  time: number;
  price: number;
  type: string;
  label: string;
  side?: "accumulation" | "distribution" | "neutral" | string;
  confidence: number;
  components?: {
    depth_significance: number;
    return_speed: number;
    volume_confirmation: number;
    level_strength: number;
    liquidity_confirmation?: number;
    [key: string]: number | undefined;
  };
  level_price?: number;
  level_kind?: "support" | "resistance" | string;
  level_score?: number;
};

export type WyckoffRange = {
  support: { price: number; score: number; touches: number; sources: string[] };
  resistance: { price: number; score: number; touches: number; sources: string[] };
  start_time: number;
  end_time: number;
  candles_inside: number;
  width_pct: number;
  atr: number;
};

export type WyckoffMtf = {
  htf_phase: string | null;
  htf_trend: string | null;
  alignment: "aligned" | "conflicting" | "neutral" | string;
};

export type WyckoffPhase = {
  phase: string;
  side: "accumulation" | "distribution" | "neutral" | string;
  evidence_event_ids: string[];
  phase_evidence?: WyckoffMarker[];
};

export type SymbolScenario = {
  invalidation: PositionActionPlanItem | null;
  take_profit: PositionActionPlanItem[];
  watch_triggers: PositionWatchTrigger[];
};

export type SymbolScenarios = {
  long: SymbolScenario;
  short: SymbolScenario;
};

export type CatalogSymbolInfo = {
  symbol: string;
  base_coin: string;
  quote_coin: string;
  status: string;
  asset_class: "crypto" | "stock" | "index" | "unknown";
  source_category?: string;
  funding_rate_interval_hours?: number | null;
  updated_at: string;
};

export type WatchlistEntry = {
  symbol: string;
  added_at: string;
  note: string;
  default_timeframe: string;
  asset_class: "crypto" | "stock" | "index" | "unknown";
};

export type ScoutScanRow = {
  symbol: string;
  asset_class?: "crypto" | "stock" | "index" | "unknown";
  session?: {
    state?: string;
    label?: string;
    next_open_at?: string | null;
    seconds_until_open?: number | null;
  } | null;
  timeframe: string;
  as_of?: string;
  note?: string;
  error?: string;
  long_score?: number;
  short_score?: number;
  wyckoff_phase?: string;
  top_event?: { label: string; confidence: number } | null;
  harmonic_active?: boolean;
  prz_distance_pct?: number | null;
  nearest_level_distance_pct?: number | null;
  liquidity_nearest_pool?: {
    price: number;
    distance_pct: number;
    label: string;
    kind?: string;
    side?: string;
    touch_count?: number;
    score?: number;
    grade?: string;
  } | null;
  liquidity_pool_distance_pct?: number | null;
  volume_state?: string;
  change_24h?: number;
  funding_rate?: number;
  funding_state?: string | null;
  crowding_score?: number | null;
  setup_proximity_pct?: number | null;
  mark_price?: number | null;
  setup_candidates?: Array<Record<string, unknown>>;
};

export type ArmedSetup = {
  id: string;
  symbol: string;
  timeframe: string;
  source: "auto" | "manual";
  setup_type: string;
  direction: "long" | "short" | null;
  trigger_price: number | null;
  trigger_label: string;
  trigger_condition: string;
  distance_pct: number | null;
  confidence: number | null;
  basis: string;
  status: "armed" | "triggered" | "invalidated" | "disarmed";
  preview: Record<string, unknown>;
  created_at: string;
  updated_at: string;
  last_seen_at: string;
};

export type ScoutScanResponse = {
  rows: ScoutScanRow[];
  armed_setups?: ArmedSetup[];
  scanned_at: string;
  cache_ttl_seconds: number;
  count: number;
  rate_budget?: Record<string, unknown>;
};

export type ScoutAnalysisResponse = {
  symbol: string;
  timeframe: string;
  as_of: string;
  cache_age_seconds: number;
  analysis: PositionChartAnalysis;
  summary: ScoutScanRow;
  analyst_briefing?: AnalystBriefing | null;
};

export type EntryChecklistItem = {
  key: string;
  label: string;
  status: "pass" | "fail" | "na";
  reason: string;
};

export type EntrySimulation = {
  symbol: string;
  direction: "long" | "short";
  entry_price: number;
  leverage: number;
  margin_usdt: number | null;
  margin_mode: string;
  estimated_liquidation: number | null;
  estimated_liquidation_distance_pct: number | null;
  mmr_used: number;
  mmr_source: "exchange" | "default";
  liquidation_formula: string;
  action_plan: PositionActionPlan;
  invalidation_distance_pct: number | null;
  first_take_profit_distance_pct: number | null;
  rr_ratio: number | null;
  loss_usdt: number | null;
  profit_usdt: number | null;
  survives_to_invalidation: boolean | null;
  direction_score: number | null;
  mtf: WyckoffMtf;
  htf_conflict: boolean;
  checklist: EntryChecklistItem[];
  checklist_passed: number;
  checklist_total: number;
  verdict_line: string;
  analysis_as_of?: string;
  analyst_briefing?: AnalystBriefing | null;
  briefing_direction_conflict?: boolean;
};

export type EntryScenario = {
  id: string;
  symbol: string;
  direction: "long" | "short";
  entry_price: number;
  leverage: number;
  margin_usdt: number | null;
  margin_mode: string;
  timeframe: string;
  estimated_liquidation: number | null;
  action_plan: PositionActionPlan;
  checklist: EntryChecklistItem[];
  rr_ratio: number | null;
  analysis_as_of: string | null;
  note: string;
  linked_position_id: string | null;
  created_at: string;
};

export type ScenarioMatchResponse = {
  already_linked: boolean;
  scenario: EntryScenario | null;
  suggestion: {
    entry_memo: string;
    thesis_text: string;
    planned_stop_price: number | null;
    planned_take_profit_price: number | null;
    slippage_pct: number | null;
    slippage_flag: boolean;
  } | null;
};

export type HarmonicPoint = {
  label: "X" | "A" | "B" | "C" | "D" | string;
  time: number;
  price: number;
  kind: "high" | "low" | string;
  index: number;
};

export type HarmonicPattern = {
  id: string;
  name: string;
  label: string;
  direction: "bullish" | "bearish" | string;
  status: "completed" | "forming" | string;
  confidence: number;
  components: {
    ratio_fit: number;
    confluence: number;
    atr_significance: number;
  };
  points: HarmonicPoint[];
  ratios: Record<string, number>;
  ratio_checks: Array<{ name: string; value: number | null; target: string | null; miss: number }>;
  prz: { low: number; high: number; mid: number };
  confluence_sources: string[];
  basis: string;
};

export type HarmonicPrz = {
  pattern_id: string;
  pattern: string;
  direction: "bullish" | "bearish" | string;
  status: "completed" | "forming" | string;
  confidence: number;
  low: number;
  high: number;
  mid: number;
  basis: string;
};

export type DerivativeMetric = {
  id?: string;
  symbol: string;
  source: "bitget" | "coinglass" | string;
  tier: "bitget_public" | "coinglass" | string;
  as_of: string;
  open_interest: number | null;
  open_interest_value: number | null;
  oi_change_pct: number | null;
  funding: number | null;
  funding_next: string | null;
  taker_ls: number | null;
  top_ls: number | null;
  long_account_ratio: number | null;
  short_account_ratio: number | null;
  oi_weighted_funding: number | null;
  source_status: "ok" | "partial" | "locked" | "error" | string;
  coverage: Record<string, unknown>;
  notes: string[];
};

export type DerivativeSnapshot = {
  symbol: string;
  provider?: string;
  tier: "bitget_public" | "coinglass" | string;
  as_of: string;
  open_interest: number | null;
  open_interest_value: number | null;
  open_interest_change_pct: number | null;
  funding_rate: number | null;
  next_funding_time: string | null;
  long_short_ratio: number | null;
  long_account_ratio: number | null;
  short_account_ratio: number | null;
  taker_buy_sell_ratio: number | null;
  top_long_short_ratio: number | null;
  oi_weighted_funding_rate: number | null;
  liquidation_clusters: Array<Record<string, unknown>>;
  source_status: "ok" | "partial" | "locked" | "error" | string;
  notes: string[];
};

export type DerivativeSignals = {
  as_of: string | null;
  coverage: {
    metric_samples: number;
    liquidation_samples: number;
    sources?: string[];
  };
  oi_price_divergence: {
    state: string;
    label: string;
    meaning: string;
    price_change_pct: number;
    oi_change_pct: number;
  } | null;
  funding_state: {
    state: "neutral" | "overheated" | "extreme" | null | string;
    label: string;
    funding: number;
    abs_percentile_30d?: number;
    sample_size: number;
    required_samples?: number;
  } | null;
  crowding_score: {
    score: number;
    components: Record<string, number>;
    label: string;
    formula: string;
  } | null;
  liquidation_clusters: Array<Record<string, unknown>>;
};

export type DerivativesContext = {
  symbol?: string;
  as_of: string | null;
  latest: DerivativeSnapshot | null;
  coinglass: DerivativeSnapshot | null;
  signals: DerivativeSignals;
  metrics?: DerivativeMetric[];
  liquidation_events?: Array<Record<string, unknown>>;
  source_status?: string;
};

export type PositionChartAnalysis = {
  position_id: string;
  symbol: string;
  timeframe: string;
  asset_class?: "crypto" | "stock" | "index" | "unknown";
  session?: {
    asset_class?: string;
    state?: string;
    label?: string;
    timezone?: string;
    is_trading_session?: boolean;
    next_open_at?: string | null;
    seconds_until_open?: number | null;
  };
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
    invalidation: ChartInvalidationLevel[];
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
    source_methods: string[];
    has_trade_fills: boolean;
    coverage: Record<string, unknown> | null;
  };
  volume_xray: {
    relative_volume: number;
    relative_volume_method: string;
    volume_state: string;
    method: string;
    data_available: boolean;
    spike_detected: boolean;
    climax_candidate: boolean;
    absorption_candidate: boolean;
    rebound_with_volume: boolean;
    delta_ratio: number | null;
    cvd_change: number | null;
    notes: string[];
  };
  trade_flow: {
    method: string;
    source: string | null;
    data_available: boolean;
    coverage: Record<string, unknown> | null;
    buckets: TradeFlowBucket[];
    cvd: CvdPoint[];
    notes: string[];
  };
  liquidity: LiquidityContext;
  derivatives?: DerivativesContext;
  wyckoff: Record<string, unknown>;
  wyckoff_range: WyckoffRange | null;
  wyckoff_phase: WyckoffPhase;
  wyckoff_mtf: WyckoffMtf;
  wyckoff_markers: WyckoffMarker[];
  wyckoff_markers_low_confidence?: WyckoffMarker[];
  scenarios?: SymbolScenarios | null;
  harmonic: {
    pivots: HarmonicPoint[];
    patterns: HarmonicPattern[];
    min_confidence: number;
    atr_multiplier: number;
  };
  harmonic_patterns: HarmonicPattern[];
  harmonic_prz: HarmonicPrz[];
  data_quality: {
    candles: number;
    analysis_candles?: number;
    session_excluded_candles?: number;
    source: string;
    estimated_volume_profile: boolean;
    volume_profile_method: string;
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
  review_v2: Record<string, unknown>;
  judgment_scorecard: JudgmentScorecard;
  memo: string;
  created_at: string;
};

export type JudgmentOutcome = "correct" | "wrong" | "whipsaw" | "untested";

export type JudgmentScore = {
  id: string;
  judgment_id: string;
  position_id: string;
  trade_id: string | null;
  judgment_type: string;
  claim: Record<string, unknown>;
  confidence: number | null;
  outcome: JudgmentOutcome;
  detail: string;
  metrics: Record<string, unknown>;
  created_at: string;
};

export type JudgmentLedgerEntry = {
  id: string;
  judgment_id: string;
  position_id: string;
  source_type: string;
  source_id: string | null;
  as_of: string;
  type: string;
  claim: Record<string, unknown>;
  confidence: number | null;
  created_at: string;
};

export type JudgmentScorecard = {
  total?: number;
  tested?: number;
  correct?: number;
  wrong?: number;
  whipsaw?: number;
  untested?: number;
  accuracy_pct?: number | null;
  by_type?: Record<string, Record<string, number>>;
  scores?: JudgmentScore[];
};

export type TradeTimeline = {
  trade: Trade;
  snapshots: PositionSnapshot[];
  events: PositionEvent[];
  monitoring_logs: Array<Record<string, unknown>>;
  judgments: JudgmentLedgerEntry[];
  judgment_scores: JudgmentScore[];
};

export type CalibrationSuggestion = {
  id: string;
  suggestion_type: string;
  title: string;
  rationale: string;
  proposed_change: Record<string, unknown>;
  sample_size: number;
  status: "pending" | "approved" | "rejected";
  created_at: string;
  updated_at: string;
};

export type EngineParamVersion = {
  id: string;
  param: string;
  old_value: unknown;
  new_value: unknown;
  suggestion_id: string | null;
  status: "active" | "superseded";
  approved_at: string;
  created_at: string;
};

export type CalibrationSummary = {
  generated_at: string;
  sample_floor?: number;
  totals: Record<string, unknown>;
  invalidation: Record<string, unknown>;
  take_profit: Record<string, unknown>;
  judgment_types?: Record<string, Record<string, unknown>>;
  confidence_curve?: Array<Record<string, unknown>>;
  level_quality?: Record<string, Array<Record<string, unknown>>>;
  score_contexts?: Record<string, number>;
  alert_response_summary?: Record<string, unknown>;
  scout_setup_summary?: Record<string, unknown>;
  briefing_performance?: Record<string, unknown>;
  wyckoff_confidence: Array<Record<string, unknown>>;
  suggestion_status_counts?: Record<string, number>;
  weekly_report?: Record<string, unknown>;
  engine_params?: EngineParamVersion[];
  suggestions: CalibrationSuggestion[];
  sample_warning: string;
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
  demo_mode?: boolean;
  market_data_provider: string;
  database: string;
  database_url: string;
  bitget_public_api: string;
  bitget_private_api: string;
  default_symbols: string[];
  refresh_policy: {
    live_position_sync_interval_seconds: number;
    insight_stale_after_minutes: number;
    insight_price_drift_stale_pct: number;
    insight_auto_refresh_enabled: boolean;
    insight_model: string;
    insight_min_regeneration_interval_minutes: number;
  };
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
  auto_closed: number;
  exit_record_errors: Array<{ position_id: string; symbol: string; error: string }>;
  open_count?: number;
  needs_exit_record_count?: number;
  positions?: LivePositionPayload[];
  timestamp?: string;
  error?: string;
};

export type AlertRuleSetting = {
  id: string;
  label: string;
  severity: "info" | "action" | "warn" | "critical";
  enabled: boolean;
  threshold: number | null;
  cooldown_minutes: number;
};

export type AlertSettings = {
  telegram: {
    configured: boolean;
    alerts_enabled: boolean;
    quiet_hours_enabled: boolean;
    quiet_hours_start: string;
    quiet_hours_end: string;
    quiet_hours_timezone: string;
    daily_summary_time: string;
    chat_ids_configured: number;
  };
  rules: AlertRuleSetting[];
};

export type AlertSettingsUpdate = {
  rules?: Record<string, { enabled?: boolean; threshold?: number | null }>;
  quiet_hours_enabled?: boolean;
  quiet_hours_start?: string;
  quiet_hours_end?: string;
  daily_summary_time?: string;
};

export type AlertTestResult = {
  configured: boolean;
  sent: number;
};

export type RuleCheckSummary = {
  id: string;
  check: string;
  stance: string;
  rule_score: number;
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
  checklists: RuleCheckSummary[];
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
  alertSettings: () => request<AlertSettings>("/api/alerts/settings"),
  updateAlertSettings: (payload: AlertSettingsUpdate) =>
    request<AlertSettings>("/api/alerts/settings", {
      method: "PATCH",
      body: JSON.stringify(payload)
    }),
  sendTestAlert: () =>
    request<AlertTestResult>("/api/alerts/test", {
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
  searchSymbols: (query: string, limit = 20) =>
    request<{ symbols: CatalogSymbolInfo[] }>(`/api/symbols?query=${encodeURIComponent(query)}&limit=${limit}`),
  watchlist: () => request<{ items: WatchlistEntry[] }>("/api/watchlist"),
  addWatchlistItem: (payload: { symbol: string; note?: string; default_timeframe?: string }) =>
    request<{ item: WatchlistEntry }>("/api/watchlist", {
      method: "POST",
      body: JSON.stringify(payload)
    }),
  removeWatchlistItem: (symbol: string) =>
    request<{ removed: string }>(`/api/watchlist/${encodeURIComponent(symbol)}`, {
      method: "DELETE"
    }),
  scoutAnalysis: (symbol: string, timeframe = "4h", force = false) =>
    request<ScoutAnalysisResponse>(`/api/scout/${encodeURIComponent(symbol)}/analysis?timeframe=${encodeURIComponent(timeframe)}&force=${force}`),
  scoutBriefing: (symbol: string, timeframe = "4h", force = false) =>
    request<{ symbol: string; timeframe: string; as_of: string; analyst_briefing: AnalystBriefing }>(
      `/api/scout/${encodeURIComponent(symbol)}/briefing?timeframe=${encodeURIComponent(timeframe)}&force=${force}`
    ),
  scoutScan: (payload: { timeframe?: string | null; force?: boolean } = {}) =>
    request<ScoutScanResponse>("/api/scout/scan", {
      method: "POST",
      body: JSON.stringify(payload)
    }),
  scoutSetups: (symbol?: string, status?: string) =>
    request<{ setups: ArmedSetup[] }>(`/api/scout/setups${symbol || status ? `?${new URLSearchParams({ ...(symbol ? { symbol } : {}), ...(status ? { status } : {}) }).toString()}` : ""}`),
  disarmScoutSetup: (setupId: string) =>
    request<{ setup: ArmedSetup }>(`/api/scout/setups/${encodeURIComponent(setupId)}/disarm`, {
      method: "POST"
    }),
  simulateEntry: (payload: { symbol: string; direction: "long" | "short"; entry_price?: number | null; leverage: number; margin_usdt?: number | null; margin_mode?: string; timeframe?: string }) =>
    request<EntrySimulation>("/api/scout/simulate", {
      method: "POST",
      body: JSON.stringify(payload)
    }),
  saveScenario: (payload: { symbol: string; direction: "long" | "short"; entry_price: number; leverage: number; margin_usdt?: number | null; margin_mode?: string; timeframe?: string; note?: string }) =>
    request<{ scenario: EntryScenario }>("/api/scout/scenarios", {
      method: "POST",
      body: JSON.stringify(payload)
    }),
  listScenarios: (symbol?: string) =>
    request<{ scenarios: EntryScenario[] }>(`/api/scout/scenarios${symbol ? `?symbol=${encodeURIComponent(symbol)}` : ""}`),
  matchScenario: (positionId: string) => request<ScenarioMatchResponse>(`/api/scout/match/${encodeURIComponent(positionId)}`),
  linkScenario: (scenarioId: string, payload: { position_id: string; apply_prefill?: boolean }) =>
    request<{ linked: boolean; position_id: string; scenario_id: string; slippage_pct: number | null; slippage_flag: boolean; position: Position }>(`/api/scout/scenarios/${encodeURIComponent(scenarioId)}/link`, {
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
  reviewCalibration: () => request<CalibrationSummary>("/api/review/calibration"),
  reviewWeeklyCalibration: () => request<Record<string, unknown>>("/api/review/calibration/weekly"),
  approveCalibrationSuggestion: (suggestionId: string) =>
    request<CalibrationSuggestion>(`/api/review/calibration/suggestions/${suggestionId}/approve`, {
      method: "POST"
    }),
  rejectCalibrationSuggestion: (suggestionId: string) =>
    request<CalibrationSuggestion>(`/api/review/calibration/suggestions/${suggestionId}/reject`, {
      method: "POST"
    }),
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
