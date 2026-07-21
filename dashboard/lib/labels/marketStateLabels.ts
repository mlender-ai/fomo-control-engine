const trendLabels: Record<string, string> = {
  neutral_to_bullish: "중립에서 상승 전환 중",
  bullish: "상승 우세",
  bearish: "하락 우세",
  bearish_to_neutral: "하락 후 중립 전환 중",
  neutral: "중립",
  unknown: "확인 필요"
};

const rsiLabels: Record<string, string> = {
  cooling_from_overbought: "과열 식는 중",
  recovering_from_oversold: "과매도 회복 중",
  oversold_or_weak: "과매도 또는 약세",
  overbought: "과열",
  neutral: "중립",
  unknown: "확인 필요"
};

const macdLabels: Record<string, string> = {
  bullish_but_weakening: "상승세지만 힘 약화",
  bearish_or_weak: "약세 또는 하락 압력",
  bullish: "상승 신호",
  bearish: "하락 신호",
  neutral: "중립",
  unknown: "확인 필요"
};

const bollingerLabels: Record<string, string> = {
  inside_band: "밴드 안쪽",
  upper_band_touch: "상단 밴드 근접",
  lower_band_touch: "하단 밴드 근접",
  reentered_from_lower_band: "하단 이탈 후 재진입",
  breakout_upper: "상단 돌파",
  breakdown_lower: "하단 이탈",
  above_upper_band: "상단 밴드 위",
  near_lower_band: "하단 밴드 근접",
  neutral: "중립",
  unknown: "확인 필요"
};

const volumeLabels: Record<string, string> = {
  declining_after_push: "상승 후 거래량 둔화",
  volume_expanding: "거래량 증가",
  expanding: "거래량 증가",
  drying_up: "거래량 감소",
  rebound_with_volume: "거래량 동반 반등",
  weak_rebound: "약한 반등",
  climax_candidate: "클라이맥스 후보",
  absorption_candidate: "흡수 후보",
  delta_imbalanced: "체결 델타 불균형",
  balanced_flow: "균형 체결",
  data_unavailable: "체결 데이터 부족",
  normal: "보통",
  neutral: "중립",
  unknown: "확인 필요"
};

const supportLabels: Record<string, string> = {
  holding: "지지 유지",
  broken: "지지 이탈",
  near: "지지선 근접",
  at_risk: "지지 훼손 위험",
  unknown: "확인 필요"
};

const resistanceLabels: Record<string, string> = {
  not_near: "저항과 거리 있음",
  nearby: "저항 근접",
  testing: "저항 테스트 중",
  broken: "저항 돌파",
  unknown: "확인 필요"
};

const phaseHintLabels: Record<string, string> = {
  early_accumulation: "초기 매집 후보",
  neutral_range: "중립 박스권",
  distribution_warning: "분산 주의",
  spring_candidate: "스프링 후보",
  test_candidate: "테스트 후보",
  sos_confirmed: "SOS 확인",
  lps_candidate: "LPS 후보",
  selling_climax: "셀링 클라이맥스",
  buying_climax: "바잉 클라이맥스",
  automatic_rally: "자동 반등",
  automatic_reaction: "자동 반락",
  secondary_test: "2차 테스트",
  utad_candidate: "UTAD 후보",
  sow_confirmed: "SOW 확인",
  lpsy_candidate: "LPSY 후보",
  accumulation_phase_a: "매집 Phase A",
  accumulation_phase_b: "매집 Phase B",
  accumulation_phase_c: "매집 Phase C",
  accumulation_phase_d: "매집 Phase D",
  accumulation_phase_e: "매집 Phase E",
  distribution_phase_a: "분산 Phase A",
  distribution_phase_b: "분산 Phase B",
  distribution_phase_c: "분산 Phase C",
  distribution_phase_d: "분산 Phase D",
  distribution_phase_e: "분산 Phase E",
  trending: "추세 진행 중",
  undetermined: "판정 보류",
  accumulation: "매집",
  distribution: "분산",
  aligned: "상위 흐름과 정렬",
  conflicting: "상위 흐름과 충돌",
  neutral: "중립",
  unknown: "확인 필요"
};

const atrRiskLabels: Record<string, string> = {
  high: "높음",
  medium: "중간",
  low: "낮음",
  unknown: "확인 필요"
};

const criticalLevelLabels: Record<string, string> = {
  support: "지지선",
  resistance: "저항선",
  invalidation: "무효화 가격",
  take_profit: "익절 기준",
  stop: "손절 기준",
  entry: "진입가",
  mark: "현재가",
  liquidation: "청산가"
};

const eventTypeLabels: Record<string, string> = {
  snapshot_created: "상태 스냅샷",
  position_synced: "포지션 동기화",
  insight_created: "인사이트 생성",
  risk_event: "리스크 이벤트",
  health_drop: "건강도 하락",
  exit_recorded: "이탈 기록"
};

const connectionStatusLabels: Record<string, string> = {
  configured: "설정됨",
  missing_credentials: "키 미설정",
  error: "오류",
  ok: "정상",
  disabled: "비활성",
  success: "완료",
  loading: "확인 중",
  available: "사용 가능"
};

const phraseLabels: Record<string, string> = {
  "Health Score": "건강도",
  "Entry Score": "진입 점수",
  "Estimated Volume Profile": "추정 볼륨 프로파일",
  "phase hint": "국면 힌트",
  "Accumulation": "매집",
  "Distribution": "분산",
  "Spring": "스프링",
  "UTAD": "UTAD",
  "SOS": "SOS",
  "SOW": "SOW",
  "LPS": "LPS",
  "LPSY": "LPSY",
  "SC": "SC",
  "BC": "BC",
  "AR": "AR",
  "ST": "ST",
  "LONG": "롱",
  "SHORT": "숏",
  "PnL": "손익률",
  "Synced from Bitget read-only position API": "Bitget read-only 포지션 API에서 동기화됨"
};

const sourceLabels: Record<string, string> = {
  bitget: "Bitget",
  demo: "Demo",
  mock: "Mock",
  live: "Live",
  loading: "확인 중"
};

const allCodeLabels: Record<string, string> = {
  ...trendLabels,
  ...rsiLabels,
  ...macdLabels,
  ...bollingerLabels,
  ...volumeLabels,
  ...supportLabels,
  ...resistanceLabels,
  ...phaseHintLabels,
  ...atrRiskLabels,
  ...criticalLevelLabels,
  ...eventTypeLabels,
  ...connectionStatusLabels,
  against_short: "숏 포지션에 불리",
  against_long: "롱 포지션에 불리",
  aligned_or_neutral: "포지션 방향과 중립 또는 정렬",
  aligned: "상위 흐름과 정렬",
  conflicting: "상위 흐름과 충돌",
  above: "위",
  below: "아래"
};

export function trendLabel(value: string | null | undefined): string {
  return labelFromMap(trendLabels, value);
}

export function rsiLabel(value: string | null | undefined): string {
  return labelFromMap(rsiLabels, value);
}

export function macdLabel(value: string | null | undefined): string {
  return labelFromMap(macdLabels, value);
}

export function bollingerLabel(value: string | null | undefined): string {
  return labelFromMap(bollingerLabels, value);
}

export function volumeStateLabel(value: string | null | undefined): string {
  return labelFromMap(volumeLabels, value);
}

export function supportStatusLabel(value: string | null | undefined): string {
  return labelFromMap(supportLabels, value);
}

export function resistanceStatusLabel(value: string | null | undefined): string {
  return labelFromMap(resistanceLabels, value);
}

export function phaseHintLabel(value: string | null | undefined): string {
  return labelFromMap(phaseHintLabels, value);
}

export function atrRiskLabel(value: string | null | undefined): string {
  return labelFromMap(atrRiskLabels, value);
}

export function criticalLevelTypeLabel(value: string | null | undefined): string {
  return labelFromMap(criticalLevelLabels, value);
}

export function genericMarketStateLabel(value: string | null | undefined): string {
  return labelFromMap(allCodeLabels, value);
}

export function directionLabel(value: string | null | undefined): string {
  if (value === "long") return "롱";
  if (value === "short") return "숏";
  return "방향 확인 필요";
}

export function timeframeLabel(value: string | null | undefined): string {
  if (value === "15m") return "15분봉";
  if (value === "1h") return "1시간봉";
  if (value === "4h") return "4시간봉";
  if (value === "12h") return "12시간봉";
  if (value === "1d") return "1일봉";
  return value ? value : "주기 확인 필요";
}

export function sourceLabel(value: string | null | undefined): string {
  if (!value) return "데이터 출처 확인 필요";
  return sourceLabels[value.toLowerCase()] ?? value;
}

export function connectionStatusLabel(value: string | null | undefined): string {
  return labelFromMap(connectionStatusLabels, value);
}

export function yesNoLabel(value: boolean): string {
  return value ? "예" : "아니오";
}

export function localizeMarketCodes(text: unknown): string {
  const normalized = stringifyMarketText(text);
  const phraseLocalized = Object.entries(phraseLabels).reduce((current, [phrase, label]) => current.replaceAll(phrase, label), normalized);
  return Object.entries(allCodeLabels)
    .sort(([left], [right]) => right.length - left.length)
    .reduce((current, [code, label]) => current.replace(new RegExp(`(^|[^A-Za-z0-9_])${escapeRegExp(code)}(?=$|[^A-Za-z0-9_])`, "g"), `$1${label}`), phraseLocalized);
}

function stringifyMarketText(value: unknown): string {
  if (value === null || value === undefined) return "";
  if (typeof value === "string") return value;
  if (typeof value === "number" || typeof value === "boolean") return String(value);
  if (Array.isArray(value)) return value.map((item) => stringifyMarketText(item)).filter(Boolean).join(" · ");
  if (typeof value === "object") {
    const record = value as Record<string, unknown>;
    const direct = record.text ?? record.label ?? record.claim ?? record.condition ?? record.action ?? record.basis ?? record.meaning;
    if (direct !== undefined && direct !== value) return stringifyMarketText(direct);
    const parts = [
      record.price !== undefined ? stringifyMarketText(record.price) : "",
      record.direction !== undefined ? stringifyMarketText(record.direction) : "",
      record.type !== undefined ? stringifyMarketText(record.type) : "",
      record.engine !== undefined ? stringifyMarketText(record.engine) : "",
      record.implication !== undefined ? stringifyMarketText(record.implication) : "",
    ].filter(Boolean);
    if (parts.length) return parts.join(" · ");
  }
  return "";
}

function labelFromMap(map: Record<string, string>, value: string | null | undefined): string {
  if (!value) return "확인 필요";
  return map[value] ?? value.replaceAll("_", " ");
}

function escapeRegExp(value: string): string {
  return value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}
