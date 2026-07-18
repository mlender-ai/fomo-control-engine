export const API_BASE_URL = process.env.NEXT_PUBLIC_API_BASE_URL ?? "";

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
  analysis_as_of?: string | null;
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
      as_of?: string;
      analysis_as_of?: string | null;
    };
    analysis_as_of?: string | null;
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
  label?: string;
  source?: string;
  reference_only?: boolean;
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
  reference_zones?: PositionActionPlanItem[];
  liquidation: {
    price: number | null;
    warning: string | null;
  };
  verdict_state?: "holding" | "weakening" | "danger" | "standby" | string;
  standby_reason?: string | null;
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
  chart_analysis?: PositionChartAnalysis | null;
  gauges?: CompactChartGauges | null;
  snapshots: PositionSnapshot[];
  insights: PositionInsight[];
  events: PositionEvent[];
  monitoring_logs: Array<Record<string, unknown>>;
  judgments: JudgmentLedgerEntry[];
  judgment_scores: JudgmentScore[];
};

export type CompactChartGauges = {
  direction: {
    active: boolean;
    needle: number;
    stance: string;
    stance_label: string;
    confidence?: number;
    transitioning: boolean;
    target?: string | null;
    preview_stance?: string | null;
    preview_stance_label?: string | null;
    flip_progress?: number | null;
    candles_in_state?: number | null;
    since?: string | null;
    last_flip_at?: string | null;
    previous_stance?: string | null;
    long_evidence_count?: number;
    short_evidence_count?: number;
    reason: string;
  };
  market_view?: {
    stance: string;
    stance_label: string;
    why: string;
    counter: string;
    next_price?: {
      label: string;
      price: number | null;
      detail: string;
    } | null;
  };
  position_context?: {
    active: boolean;
    direction?: "long" | "short" | string | null;
    alignment: "aligned" | "opposed" | "neutral" | string | null;
    headline: string | null;
    detail: string | null;
  };
  take_profit: {
    active: boolean;
    level: "낮음" | "중간" | "높음" | string | null;
    pressure: number | null;
    reason: string;
    components?: Record<string, unknown>;
  };
  tier2_overlays: Array<{
    engine: string;
    engine_label?: string;
    claim: string;
    direction: "long" | "short" | "neutral" | string;
    price?: number | null;
    qualification?: string | null;
  }>;
  event_pills?: Array<{
    id: string;
    time: number;
    price: number;
    direction: "long" | "short";
    label: string;
    confidence: number;
    win_1r_pct?: number | null;
    sample_size: number;
    qualification: "validated";
    confirmed: true;
  }>;
  event_pill_audit?: {
    window_events?: number;
    validated?: number;
    confirmed?: number;
    rendered: number;
    bottleneck?: string | null;
  };
  pill_diagnostics?: {
    window_events: number;
    validated: number;
    confirmed: number;
    rendered: number;
    bottleneck?: string | null;
  };
  stance_history?: Array<{
    time: number;
    stance: string;
    preview_stance?: string | null;
    transitioning: boolean;
    flipped: boolean;
    long_evidence_count: number;
    short_evidence_count: number;
    confidence: number;
    reason: string;
  }>;
  bar_state: {
    provisional: boolean;
    minutes_to_close?: number | null;
    bar_close_at?: string | null;
  };
  policy?: Record<string, unknown>;
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

export type CatalogStatus = {
  count: number;
  updated_at: string | null;
  last_error: string | null;
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
  long_score?: number | null;
  short_score?: number | null;
  long_evidence_count?: number;
  short_evidence_count?: number;
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
  entry_intent_distance_pct?: number | null;
  mark_price?: number | null;
  setup_candidates?: Array<Record<string, unknown>>;
  backtest_summary?: string | null;
  tracked?: boolean;
  confluence?: AnalystConfluence | null;
  full_alignment?: FullAlignment | null;
};

export type FullAlignment = {
  unanimous: boolean;
  eligible_count: number;
  direction: "long" | "short" | null;
  agreeing: number;
  dissenting: number;
  score: number;
  agreeing_modules: Array<{ engine: string; claim?: string; direction: string; score: number }>;
  dissenting_modules: Array<{ engine: string; claim?: string; direction: string; score: number }>;
  htf_aligned: boolean;
  transitioning: boolean;
  candles_in_state?: number | null;
  sample_size: number;
  win_1r_pct?: number | null;
  win_1r_ci?: [number, number] | null;
  sample_label: string;
  predictive_warning: boolean;
};

export type TrackedScoutItem = {
  symbol: string;
  tracking_source: "manual" | "engine";
  timeframe: string;
  stance?: string | null;
  stance_label?: string | null;
  one_line: string;
  trigger_distance_pct?: number | null;
  intent_zone?: { lower: number; upper: number } | null;
  armed_condition?: string | null;
  expires_in_days?: number | null;
  intent_ids: string[];
  setup_ids: string[];
  full_alignment?: FullAlignment | null;
};

export type BacktestCase = {
  symbol: string;
  timeframe: string;
  asset_class: string;
  as_of: string;
  entry_price: number;
  signature_key: string;
  signature: Record<string, unknown>;
  event: Record<string, unknown>;
  outcome: {
    win_1r: boolean;
    win_2r: boolean;
    mfe_r: number;
    mae_r: number;
    realized_rr: number;
    resolved_bars: number;
    risk_fallback?: boolean;
  };
  price_path: Array<{ time: string; close: number; high?: number; low?: number }>;
  disclaimer: string;
};

export type BacktestStat = {
  signature_key: string;
  signature?: Record<string, unknown>;
  label?: string;
  scope?: string;
  sample_size: number;
  win_1r_pct: number | null;
  win_1r_ci?: [number, number] | null;
  win_2r_pct: number | null;
  median_rr: number | null;
  avg_mfe_r: number | null;
  avg_mae_r: number | null;
  avg_resolution_bars?: number | null;
  unstable?: boolean | null;
  lifecycle_state?: "candidate" | "validated" | "degraded" | "quarantined" | string | null;
  lifecycle_note?: string | null;
  sample_warning?: string | null;
  disclaimer: string;
  cases: BacktestCase[];
};

export type HistoricalBacktest = {
  symbol: string;
  timeframe: string;
  asset_class?: string;
  generated_at: string;
  source: string;
  disclaimer: string;
  sample_floor: number;
  active_signatures: Array<Record<string, unknown>>;
  stats: BacktestStat[];
  case_count?: number | null;
  notes: string[];
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

export type EntryIntent = {
  id: string;
  symbol: string;
  timeframe: string;
  kind: "watch" | "zone";
  direction: "long" | "short" | null;
  zone_lower: number | null;
  zone_upper: number | null;
  conditions: Array<"price_in_zone" | "sweep_confirmed" | "wyckoff_event" | "volume_spike" | "briefing_aligned" | string>;
  tolerance: "tight" | "normal" | "loose";
  tolerance_pct: number;
  status: "active" | "triggered" | "partial" | "invalidated" | "expired" | "cancelled";
  note: string;
  preview: Record<string, unknown>;
  condition_state: Record<string, { met?: boolean; label?: string }>;
  expires_at: string;
  created_at: string;
  updated_at: string;
  last_seen_at: string;
};

export type ScoutScanResponse = {
  rows: ScoutScanRow[];
  armed_setups?: ArmedSetup[];
  entry_intents?: EntryIntent[];
  tracked?: TrackedScoutItem[];
  alignment_discoveries?: ScoutScanRow[];
  best_alignment?: ScoutScanRow | null;
  scanned_at: string;
  cache_ttl_seconds: number;
  count: number;
  rate_budget?: Record<string, unknown>;
};

export type UniverseDiscovery = {
  id: string;
  symbol: string;
  timeframe: string;
  asset_class: "crypto" | "stock" | "index" | "unknown" | string;
  signature_key: string;
  signature: Record<string, unknown>;
  status: "alerted" | "stored" | "rejected";
  gate_passed: boolean;
  gate_reasons: Array<{ code: string; passed: boolean; value?: unknown; threshold?: unknown }>;
  confidence: number | null;
  win_1r_pct: number | null;
  win_1r_ci?: [number, number] | null;
  sample_size: number | null;
  quote_volume_24h: number | null;
  current_price: number | null;
  message: string;
  payload: Record<string, unknown>;
  judgment_id: string | null;
  alerted_at: string | null;
  created_at: string;
  updated_at: string;
};

export type ScoutAnalysisResponse = {
  symbol: string;
  timeframe: string;
  as_of: string;
  cache_age_seconds: number;
  analysis: PositionChartAnalysis;
  summary: ScoutScanRow;
  historical_backtest?: HistoricalBacktest | null;
  analyst_briefing?: AnalystBriefing | null;
  gauges?: CompactChartGauges | null;
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
  invalidation_too_close: boolean;
  min_invalidation_distance_pct: number;
  first_take_profit_distance_pct: number | null;
  rr_ratio: number | null;
  rr_ratio_raw: number | null;
  rr_ratio_display: string | null;
  rr_display_cap: number;
  quality_anomalies: {
    invalidation_too_close: boolean;
    rr_above_display_cap: boolean;
  };
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
  kelly_reference?: KellyReference | null;
};

export type KellyReference = {
  available: boolean;
  published: boolean;
  reason?: string;
  disclaimer: string;
  basis?: string;
  signature_key?: string | null;
  label?: string;
  sample_size?: number;
  win_rate_ci_low_pct?: number;
  median_rr?: number;
  kelly_fraction_pct?: number;
  half_kelly_fraction_pct?: number;
  state?: string;
  input_margin_usdt?: number;
  position_sizing_note?: string;
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
  raw_json?: Record<string, unknown>;
};

export type MoneyFlowSignal = {
  state: "spot_led" | "futures_led" | "spot_absorb" | "delever" | "mixed";
  label: string;
  available: boolean;
  provisional: boolean;
  reason: string;
  source: "bitget_spot" | "coinglass_agg" | string;
  source_label: string;
  as_of: string | null;
  sample_size: number;
  required_samples?: number;
  confidence?: number;
  price_change_pct: number | null;
  predictive_warning?: boolean;
  candidate_sample_size?: number;
  candidate_win_1r_ci?: [number, number] | null;
  spot_cvd_delta_ratio: number | null;
  futures_cvd_delta_ratio: number | null;
  oi_change_pct: number | null;
  directions?: {
    price: "up" | "down" | "flat";
    spot_cvd: "up" | "down" | "flat";
    futures_cvd: "up" | "down" | "flat";
    oi: "up" | "down" | "flat";
  };
  thresholds?: Record<string, number>;
  spot_cvd: Array<{ time: number | string; value: number }>;
  futures_cvd: Array<{ time: number | string; value: number }>;
  coverage: Record<string, unknown>;
  notes?: string[];
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
  money_flow?: MoneyFlowSignal;
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

export type UnifiedLiquidationHeatmap = {
  symbol: string;
  source: "bitget_realized" | "coinglass_est";
  source_status: string;
  truth_label: string;
  timeframe_seconds: number | null;
  range: "12H" | "24H" | "3D" | "1W" | "1M";
  window_start: string;
  window_end: string;
  time_buckets: string[];
  price_bins: { count: number; min: number; max: number; step: number };
  grid: number[][];
  side_grid: { long: number[][]; short: number[][] };
  max_value_usd_estimated: number;
  n_events: number;
  available_events: number;
  sample_low: boolean;
  side_split: { long_usd_estimated: number; short_usd_estimated: number };
  position_split: { above_usd_estimated: number; below_usd_estimated: number };
  last_event_ts: string | null;
  top_zones: Array<{
    price_index: number;
    price_low: number;
    price_high: number;
    price_mid: number;
    total_usd_estimated: number;
    long_usd_estimated: number;
    short_usd_estimated: number;
    events: number;
  }>;
  events: Array<{
    timestamp: string;
    price: number;
    size_usd_estimated: number;
    raw_amount: number;
    side: "long" | "short";
    leverage: number | null;
    persisted_until: string | null;
  }>;
  filters: {
    side: "all" | "long" | "short";
    size: "all" | "q2_plus" | "q3_plus" | "q4" | "10x" | "25x" | "50x" | "100x";
    min_size_usd: number;
    mode: "persist" | "event";
    filter_basis: "leverage" | "size_quartile";
    leverage_available: boolean;
    leverage_minimum: number | null;
    available_thresholds: string[];
    quartile_thresholds_usd: Record<string, number>;
  };
  rendering: { normalization: "log1p"; default_opacity: number; raw_values_preserved: boolean };
  coverage: Record<string, unknown>;
  notes: string[];
};

export type OneLinerStance = "상방" | "하방" | "횡보" | "판단불가";

export type OneLinerLine = {
  module: "wyckoff" | "liquidity" | "volume" | "harmonic" | "levels" | "derivatives" | "indicators";
  module_label: string;
  stance: OneLinerStance;
  phrase: string;
  confidence_class: "강" | "중" | "약";
  evidence_ref: string;
};

export type OneLinerSummary = {
  lines: OneLinerLine[];
  counts: Record<OneLinerStance, number>;
  overall_stance: OneLinerStance;
  summary: string;
  policy: string;
};

export type OccOptionsSummary = {
  available: boolean;
  status: string;
  source: "occ_public" | string;
  source_label: string;
  underlying: string;
  as_of?: string;
  open_interest_basis?: "previous_settlement" | string;
  call_open_interest?: number;
  put_open_interest?: number;
  put_call_oi_ratio?: number | null;
  call_volume?: number | null;
  put_volume?: number | null;
  put_call_volume_ratio?: number | null;
  volume_date?: string | null;
  max_pain_price?: number | null;
  max_pain_expiry?: string | null;
  days_to_expiry?: number | null;
  max_pain_basis?: "nearest_expiry_open_interest" | string;
  contract_count?: number;
  top_call_contracts?: Array<{ expiry: string; strike: number; open_interest: number }>;
  top_put_contracts?: Array<{ expiry: string; strike: number; open_interest: number }>;
  notes?: string[];
};

export type PositionChartAnalysis = {
  detail_level?: "compact" | "full";
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
  options?: OccOptionsSummary | null;
  onchain?: OnchainChartContext;
  wyckoff: Record<string, unknown>;
  wyckoff_range: WyckoffRange | null;
  wyckoff_phase: WyckoffPhase;
  wyckoff_mtf: WyckoffMtf;
  wyckoff_markers: WyckoffMarker[];
  wyckoff_markers_low_confidence?: WyckoffMarker[];
  // WO-43: TA별 1줄 판정 (고정 어휘, 충돌 그대로 노출) — WO-47 스트립 UI 입력.
  one_liners?: OneLinerSummary;
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
  historical_backtest?: HistoricalBacktest | null;
};

export type OnchainWhaleReview = {
  signature_key: string;
  state: string;
  trust_status: "validating" | "review_ready" | "trusted" | "excluded";
  sample_size: number;
  observed_count: number;
  win_1r_pct: number | null;
  win_1r_ci: [number, number] | null;
  remaining_samples: number;
  cumulative_return_r: number;
  average_return_r: number | null;
  profit_factor_r: number | null;
  validation_started_at: string | null;
  validation_days: number;
  validation_required_days: number;
  validation_remaining_days: number;
  validation_progress_pct: number;
  validation_calendar_complete: boolean;
  promotion_eligible: boolean;
  warning?: string | null;
};

export type OnchainWhaleEvent = {
  id: string;
  wallet_address: string;
  wallet_label: string;
  coin: string;
  symbol: string;
  side: "long" | "short";
  event: "open" | "increase" | "reduce" | "close" | "flip";
  size_usd: number;
  entry_px: number | null;
  mark_px: number | null;
  unrealized_pnl: number | null;
  event_at: string;
  validation_state: string;
  sample_size: number;
  win_1r_pct: number | null;
  accuracy_label: string;
  alias_disclaimer: string;
};

export type OnchainChartMarker = {
  time: number;
  kind: "entry" | "exit";
  side: "long" | "short";
  event: string;
  count: number;
  size_usd: number;
  size_tier: 1 | 2 | 3;
  label: string;
  emphasized: boolean;
  items: OnchainWhaleEvent[];
};

export type OnchainChartContext = {
  supported: boolean;
  unsupported_reason?: string | null;
  symbol: string;
  markers: OnchainChartMarker[];
  validated_evidence: Array<Record<string, unknown>>;
  policy: string;
};

export type OnchainWhaleWallet = {
  address: string;
  address_short: string;
  label: string;
  source: string;
  active: boolean;
  last_polled_at: string | null;
  last_fill_at: string | null;
  alias_disclaimer: string;
  marker_emphasis: boolean;
  direction_eligible: boolean;
  leaderboard: {
    selection_rank: number;
    leaderboard_rank: number;
    selection_reason: string;
    account_value_usd: number;
    week_pnl_usd: number;
    week_roi: number;
    month_pnl_usd: number;
    month_roi: number;
    month_volume_usd: number;
    all_time_pnl_usd: number;
    all_time_roi: number;
    turnover: number;
    focus_positions: Array<{
      coin: string;
      side: "long" | "short";
      size_usd: number;
      entry_px: number | null;
    }>;
  } | null;
  review: OnchainWhaleReview;
  positions: Array<{
    coin: string;
    symbol: string;
    side: "long" | "short";
    size_usd: number;
    entry_px: number | null;
    mark_px: number | null;
    unrealized_pnl: number | null;
    liquidation_px: number | null;
    as_of: string;
  }>;
};

export type OnchainWhaleDashboard = {
  enabled: boolean;
  wallet_count: number;
  max_wallets: number;
  minimum_event_size_usd: number;
  wallets: OnchainWhaleWallet[];
  recent_events: OnchainWhaleEvent[];
  discovery: {
    enabled: boolean;
    status: string;
    as_of?: string | null;
    rows_scanned?: number;
    eligible_count?: number;
    selected_count: number;
    manual_count?: number;
    criteria?: Record<string, number>;
    position_scan?: {
      scanned_count: number;
      active_focus_count: number;
      errors: number;
      coverage: Record<string, OnchainWhaleCoverage>;
    };
    selection_policy?: {
      focus_symbols: string[];
      scan_limit: number;
      directional_slots: number;
      quality_slots: number;
      minimum_position_usd: number;
    };
    selected_coverage?: Record<string, OnchainWhaleCoverage>;
  };
  flow: {
    window_hours: number;
    bucket_hours: number;
    current_long_usd: number;
    current_short_usd: number;
    current_net_usd: number;
    flow_24h_usd: number;
    event_count_24h: number;
    timeline: Array<{
      time: number;
      long_in_usd: number;
      short_in_usd: number;
      long_out_usd: number;
      short_out_usd: number;
      net_usd: number;
      event_count: number;
    }>;
    symbols: Array<{
      symbol: string;
      long_usd: number;
      short_usd: number;
      net_usd: number;
      wallet_count: number;
      event_count_24h: number;
    }>;
  };
  symbol_activity: Record<string, {
    symbol: string;
    long_usd: number;
    short_usd: number;
    net_usd: number;
    long_wallet_count: number;
    short_wallet_count: number;
    wallet_count: number;
    positions: Array<{
      wallet_address: string;
      address_short: string;
      wallet_label: string;
      leaderboard_rank: number | null;
      selection_rank: number | null;
      side: "long" | "short";
      size_usd: number;
      entry_px: number | null;
      mark_px: number | null;
      unrealized_pnl: number | null;
      as_of: string | null;
    }>;
    recent_events: OnchainWhaleEvent[];
    as_of: string | null;
  }>;
  rate_budget: Record<string, unknown>;
  policy: string;
};

export type OnchainWhaleCoverage = {
  long_wallets: number;
  short_wallets: number;
  long_usd: number;
  short_usd: number;
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
  status:
    | "pending"
    | "scheduled"
    | "experiment"
    | "adopted"
    | "approved"
    | "rejected"
    | "vetoed"
    | "discarded"
    | "dwell_blocked"
    | "rolled_back";
  autonomy?: Record<string, unknown>;
  // WO-36 §4: 제안 근거가 검증기간(OOS)에서도 성립하는지 — 승인 화면 필수 첨부.
  oos_validation?: {
    split_ratio?: number;
    train?: { sample_size?: number; rate_pct?: number | null };
    validation?: { sample_size?: number; rate_pct?: number | null };
    holds_in_validation?: boolean;
    sample_state?: string;
  } | null;
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
  adopted_by?: "manual" | "autonomy" | "rollback" | string;
  metadata?: Record<string, unknown>;
  approved_at: string;
  created_at: string;
};

export type CalibrationSummary = {
  status?: "preparing" | "ready" | string;
  cache_status?: "preparing" | "ready" | string;
  computed_at?: string | null;
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
  autonomy?: Record<string, unknown>;
  candidate_review?: CandidateReviewStatus;
  weekly_report?: Record<string, unknown>;
  engine_params?: EngineParamVersion[];
  suggestions: CalibrationSuggestion[];
  sample_warning: string;
};

export type CandidateReviewItem = {
  engine: string;
  event_type: string;
  label: string;
  sample_size: number;
  win_1r_pct: number | null;
  win_2r_pct: number | null;
  win_1r_ci: [number, number] | null;
  remaining_samples: number;
  source_counts: { backtest?: number; live?: number };
  status: "candidate" | "promotion_proposed" | "validated" | "degraded" | string;
  promotion_signature_keys: string[];
  predictive_warning: boolean;
  lookahead_audit: "passed" | "blocked" | string;
};

export type CandidateReviewStatus = {
  generated_at: string;
  policy: string;
  veto_window_hours: number;
  pending_promotions: number;
  items: CandidateReviewItem[];
};

export type PerformanceMetrics = {
  sample_size: number;
  sample_sufficient: boolean;
  sample_warning?: string | null;
  gross_profit_usdt: number;
  gross_loss_usdt: number;
  net_profit_usdt: number;
  profit_factor: number | null;
  profit_factor_refs?: Record<string, number>;
  win_rate_pct: number | null;
  avg_win_usdt: number | null;
  avg_loss_usdt: number | null;
  payoff_ratio: number | null;
  avg_r: number | null;
  avg_r_method?: string;
  max_drawdown_pct: number | null;
  max_drawdown_usdt: number | null;
  max_drawdown_period?: Record<string, string | null>;
  longest_recovery_days: number;
  annualized_return_pct: number | null;
  calmar: number | null;
  calmar_published: boolean;
  sharpe: number | null;
  sortino: number | null;
  recovery_factor: number | null;
  risk_of_ruin: Record<string, unknown>;
  warnings: string[];
};

export type PerformanceSummary = {
  as_of: string;
  sample_floor: number;
  capital_base_usdt: number;
  disclaimer: string;
  overall: PerformanceMetrics;
  equity_curve: Array<Record<string, unknown>>;
  breakdowns: Record<string, Record<string, PerformanceMetrics>>;
  mdd_guard: Record<string, unknown>;
  scoreboard_cross_view: Record<string, unknown>;
};

export type PaperTrade = {
  id: string;
  symbol: string;
  timeframe: string;
  asset_class: string;
  direction: "long" | "short";
  status: "open" | "closed";
  entry_at: string;
  entry_price: number;
  margin_usdt: number;
  leverage: number;
  remaining_quantity: number;
  invalidation_price: number;
  take_profit_price: number;
  take_profit_2_price: number | null;
  entry_atr: number | null;
  target_plan: Record<string, unknown>;
  entry_evidence: Record<string, unknown>;
  checklist: Record<string, unknown>;
  stance_snapshot: Record<string, unknown>;
  exit_at: string | null;
  exit_price: number | null;
  exit_reason: string | null;
  net_pnl_usdt: number;
  net_return_pct: number;
  holding_bars: number;
  loss_tags: string[];
  exit_monitor?: {
    mark_price: number;
    invalidation_distance_pct: number;
    take_profit_distance_pct: number;
  };
};

export type PaperMetrics = {
  net_return_pct: number;
  win_rate_pct: number | null;
  profit_factor: number | null;
  mdd_pct: number;
  trade_count: number;
  scored_trade_count: number;
  neutral_count: number;
  audited_trade_count?: number;
  policy_invalid_count?: number;
  sample_sufficient: boolean;
};

export type PaperGateFunnel = {
  period_days: number;
  as_of: string;
  evaluations: number;
  entered: number;
  stages: Array<{
    id: string;
    label: string;
    count: number;
    rejection_top3?: Array<{ detail: string; count: number }>;
  }>;
  top_rejection: { id: string; label: string; count: number } | null;
  rejection_counts: Record<string, number>;
  entry_block_count?: number;
  checklist_pass_rates?: Array<{
    key: string;
    label: string;
    passed: number;
    evaluated: number;
    pass_rate_pct: number;
  }>;
  signature_gate_note?: string | null;
  pill_diagnostics?: {
    rendered: number;
    bottleneck: string | null;
    bottleneck_count: number;
  };
};

export type PaperDashboard = {
  scoreboard: {
    as_of: string;
    started_at: string | null;
    benchmark: { started: boolean; started_at: string | null; ends_at: string | null; reset_count: number };
    engine: PaperMetrics;
    user: PaperMetrics;
    user_fill_sync: {
      status: string;
      stored_fill_count: number;
      reconstructed_trade_count: number;
      last_fill_at?: string | null;
      last_success_at?: string | null;
      pnl_status: "reconstructed";
      note?: string;
    };
    equity_curve: {
      engine: Array<{ ts: string; return_pct: number }>;
      user: Array<{ ts: string; return_pct: number }>;
    };
    competition: {
      window: "benchmark_anchor";
      started_at: string;
      engine: PaperMetrics;
      user: PaperMetrics;
      engine_leading: boolean;
      verdict: string;
      equity_curve: { engine: Array<{ ts: string; return_pct: number }>; user: Array<{ ts: string; return_pct: number }> };
    };
    recent_28d: {
      window: "rolling_28d";
      started_at: string;
      engine: PaperMetrics;
      user: PaperMetrics;
      equity_curve: { engine: Array<{ ts: string; return_pct: number }>; user: Array<{ ts: string; return_pct: number }> };
    };
    rolling_4w: { engine: PaperMetrics; user: PaperMetrics; engine_leading: boolean; verdict: string };
    poor_performance: boolean;
    fairness_note: string;
  };
  open_trades: PaperTrade[];
  closed_trades: PaperTrade[];
  calibration: {
    computed_at?: string | null;
    weekly_report: Record<string, unknown>;
    suggestions: CalibrationSuggestion[];
    suggestion_status_counts: Record<string, number>;
    engine_params: EngineParamVersion[];
    signature_state_counts: Record<string, number>;
    candidate_review?: CandidateReviewStatus;
  };
  performance_action: { poor: boolean; summary: string; actions: Array<Record<string, unknown>> };
  gate_funnel: PaperGateFunnel;
  activation: {
    running: boolean;
    target_count: number;
    evaluations_24h: number;
    flip_count_7d?: number;
    entry_count_7d?: number;
    validation_slots?: { active: number; target: number };
    next_confirmed_bar_minutes?: number | null;
    items: Array<{ id: string; label: string; ok: boolean; value: string; reason: string | null }>;
  };
  live_orders_enabled: false;
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
    pulse_interval_hours: number;
    paper_alerts_enabled: boolean;
    chat_ids_configured: number;
  };
  scout: {
    auto_arm_enabled: boolean;
    auto_arm_symbol_limit: number;
    manual_tracking_symbol_limit: number;
  };
  rules: AlertRuleSetting[];
};

export type AlertSettingsUpdate = {
  rules?: Record<string, { enabled?: boolean; threshold?: number | null }>;
  quiet_hours_enabled?: boolean;
  quiet_hours_start?: string;
  quiet_hours_end?: string;
  daily_summary_time?: string;
  pulse_interval_hours?: number;
  paper_alerts_enabled?: boolean;
  scout_auto_arm_enabled?: boolean;
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
  const headers = new Headers(init?.headers);
  if (init?.body != null && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }
  const response = await fetch(`${API_BASE_URL}${path}`, {
    ...init,
    headers,
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
  sendTestAlert: (ruleId?: string) =>
    request<AlertTestResult>("/api/alerts/test", {
      method: "POST",
      body: JSON.stringify(ruleId ? { rule_id: ruleId } : {})
    }),
  livePositions: () => request<LivePositionsResponse>("/api/live/positions?compact=true"),
  syncLivePositions: () =>
    request<BitgetSyncResult>("/api/live/positions/sync", {
      method: "POST"
    }),
  livePosition: (positionId: string) => request<LivePositionDetail>(`/api/live/positions/${positionId}`),
  positionChartAnalysis: (positionId: string, timeframe = "4h", compact = false) =>
    request<PositionChartAnalysis>(
      `/api/live/positions/${positionId}/chart-analysis?timeframe=${encodeURIComponent(timeframe)}${compact ? "&compact=true" : ""}`
    ),
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
    request<{ symbols: CatalogSymbolInfo[]; catalog_status: CatalogStatus }>(
      `/api/symbols?query=${encodeURIComponent(query)}&limit=${limit}`
    ),
  refreshSymbolCatalog: () =>
    request<{ catalog_status: CatalogStatus }>("/api/symbols/refresh", { method: "POST" }),
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
  scoutAnalysis: (symbol: string, timeframe = "4h", force = false, detail = false) =>
    request<ScoutAnalysisResponse>(`/api/scout/${encodeURIComponent(symbol)}/analysis?timeframe=${encodeURIComponent(timeframe)}&force=${force}&detail=${detail}`),
  scoutBriefing: (symbol: string, timeframe = "4h", force = false) =>
    request<{ symbol: string; timeframe: string; as_of: string; historical_backtest?: HistoricalBacktest | null; analyst_briefing: AnalystBriefing }>(
      `/api/scout/${encodeURIComponent(symbol)}/briefing?timeframe=${encodeURIComponent(timeframe)}&force=${force}`
    ),
  scoutBacktest: (symbol: string, timeframe = "4h", force = false) =>
    request<{ symbol: string; timeframe: string; as_of: string; historical_backtest: HistoricalBacktest }>(
      `/api/scout/${encodeURIComponent(symbol)}/backtest?timeframe=${encodeURIComponent(timeframe)}&force=${force}`
    ),
  scoutScan: (payload: { timeframe?: string | null; force?: boolean } = {}) =>
    request<ScoutScanResponse>("/api/scout/scan", {
      method: "POST",
      body: JSON.stringify(payload)
    }),
  universeDiscoveries: (params: { symbol?: string; status?: string; limit?: number } = {}) =>
    request<{ discoveries: UniverseDiscovery[] }>(
      `/api/scout/discoveries${Object.keys(params).length ? `?${new URLSearchParams(
        Object.entries(params)
          .filter(([, value]) => value !== undefined && value !== "")
          .map(([key, value]) => [key, String(value)])
      ).toString()}` : ""}`
    ),
  universeScan: (payload: { timeframe?: string | null; force?: boolean } = {}) =>
    request<{
      discoveries: UniverseDiscovery[];
      alignment_discoveries?: ScoutScanRow[];
      best_alignment?: ScoutScanRow | null;
      rate_budget?: Record<string, unknown>;
    }>("/api/scout/universe/scan", {
      method: "POST",
      body: JSON.stringify(payload)
    }),
  scoutSetups: (symbol?: string, status?: string) =>
    request<{ setups: ArmedSetup[] }>(`/api/scout/setups${symbol || status ? `?${new URLSearchParams({ ...(symbol ? { symbol } : {}), ...(status ? { status } : {}) }).toString()}` : ""}`),
  disarmScoutSetup: (setupId: string) =>
    request<{ setup: ArmedSetup }>(`/api/scout/setups/${encodeURIComponent(setupId)}/disarm`, {
      method: "POST"
    }),
  entryIntents: (symbol?: string, status?: string) =>
    request<{ intents: EntryIntent[] }>(
      `/api/scout/intents${symbol || status ? `?${new URLSearchParams({ ...(symbol ? { symbol } : {}), ...(status ? { status } : {}) }).toString()}` : ""}`
    ),
  createEntryIntent: (
    symbol: string,
    payload: {
      kind?: "watch" | "zone";
      direction?: "long" | "short";
      zone_lower?: number | null;
      zone_upper?: number | null;
      price?: number | null;
      conditions?: string[];
      tolerance?: "tight" | "normal" | "loose";
      expires_at?: string | null;
      note?: string;
      timeframe?: string;
      leverage?: number;
    }
  ) =>
    request<{ intent: EntryIntent }>(`/api/scout/${encodeURIComponent(symbol)}/intents`, {
      method: "POST",
      body: JSON.stringify(payload)
    }),
  cancelEntryIntent: (intentId: string) =>
    request<{ intent: EntryIntent }>(`/api/scout/intents/${encodeURIComponent(intentId)}/cancel`, {
      method: "POST"
    }),
  simulateEntry: (payload: { symbol: string; direction: "long" | "short"; entry_price?: number | null; leverage: number; margin_usdt?: number | null; margin_mode?: string; timeframe?: string }) =>
    request<EntrySimulation>("/api/scout/simulate", {
      method: "POST",
      body: JSON.stringify(payload)
    }),
  performance: () => request<PerformanceSummary>("/api/performance"),
  paperDashboard: () => request<PaperDashboard>("/api/paper/dashboard"),
  onchainWhales: () => request<OnchainWhaleDashboard>("/api/onchain/whales"),
  addOnchainWhale: (payload: { address: string; label?: string }) => request<{ wallet: OnchainWhaleWallet }>("/api/onchain/whales", {
    method: "POST",
    body: JSON.stringify(payload)
  }),
  removeOnchainWhale: (address: string) => request<{ removed: string }>(`/api/onchain/whales/${encodeURIComponent(address)}`, { method: "DELETE" }),
  collectOnchainWhales: () => request<Record<string, unknown>>("/api/onchain/collect", { method: "POST" }),
  discoverOnchainWhales: () => request<Record<string, unknown>>("/api/onchain/discover", { method: "POST" }),
  startPaperBenchmark: (reset = false) => request<{ started: boolean; started_at: string; ends_at: string; target_count: number; created: boolean }>("/api/paper/benchmark/start", {
    method: "POST",
    body: JSON.stringify({ reset })
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
  unifiedLiquidationHeatmap: (
    symbol: string,
    timeframe: string,
    filters: { side: "all" | "long" | "short"; size: "all" | "q2_plus" | "q3_plus" | "q4" | "10x" | "25x" | "50x" | "100x"; range: "12H" | "24H" | "3D" | "1W" | "1M"; mode: "persist" | "event" }
  ) => {
    const query = new URLSearchParams({ symbol, tf: timeframe, ...filters, price_bins: "120", source: "realized" });
    return request<UnifiedLiquidationHeatmap>(`/api/liq/heatmap?${query.toString()}`);
  },
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
  vetoCalibrationSuggestion: (suggestionId: string) =>
    request<CalibrationSuggestion>(`/api/review/calibration/suggestions/${suggestionId}/veto`, {
      method: "POST"
    }),
  rejectCalibrationSuggestion: (suggestionId: string) =>
    request<CalibrationSuggestion>(`/api/review/calibration/suggestions/${suggestionId}/reject`, {
      method: "POST"
    }),
  approveSignatureRecovery: (signatureKey: string) =>
    request<Record<string, unknown>>(`/api/review/signatures/${encodeURIComponent(signatureKey)}/recover`, {
      method: "POST"
    }),
  approveCandidatePromotion: (signatureKey: string) =>
    request<Record<string, unknown>>(`/api/review/signatures/${encodeURIComponent(signatureKey)}/promotion/approve`, {
      method: "POST"
    }),
  vetoCandidatePromotion: (signatureKey: string) =>
    request<Record<string, unknown>>(`/api/review/signatures/${encodeURIComponent(signatureKey)}/promotion/veto`, {
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
