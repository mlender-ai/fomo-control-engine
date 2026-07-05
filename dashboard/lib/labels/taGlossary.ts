import { localizeMarketCodes } from "./marketStateLabels";

/**
 * TA 용어 3단 라벨 시스템 (WO-FCE-11).
 *
 * 표기 규칙: short가 1차 표기, 원어는 괄호 — "고점 돌파 실패(UTAD)".
 * plain은 툴팁/ⓘ 상세용. 예측 단정 문구 금지 ("하락합니다" X, "~경계 신호" O).
 *
 * 완결성 보증: 각 glossary는 term 유니언 타입의 Record라서
 * 키가 빠지면 빌드(tsc)에서 에러가 난다.
 */

export type TaGlossaryEntry = {
  term: string;
  short: string;
  plain: string;
  action_hint?: { long: string; short: string };
};

export type WyckoffEventTerm = "SC" | "AR" | "ST" | "Spring" | "Test" | "SOS" | "LPS" | "BC" | "UTAD" | "SOW" | "LPSY";

export const WYCKOFF_EVENT_GLOSSARY: Record<WyckoffEventTerm, TaGlossaryEntry> = {
  SC: {
    term: "SC",
    short: "매도 절정",
    plain: "공포성 투매로 거래량이 폭발한 캔들. 하락이 소진됐을 가능성을 보는 매집 초기 신호",
    action_hint: { long: "저점 확인 근거 후보", short: "숏 익절 검토 구간" }
  },
  AR: {
    term: "AR",
    short: "자동 반등/반락",
    plain: "절정 직후 반대 방향으로 튕긴 움직임. 레인지(박스)의 반대쪽 경계를 만든다",
    action_hint: { long: "박스 상단 저항 확인", short: "박스 하단 지지 확인" }
  },
  ST: {
    term: "ST",
    short: "재시험",
    plain: "절정 가격대를 다시 건드려 보는 움직임. 거래량이 줄면 그 방향의 압력이 약해졌다는 근거",
    action_hint: { long: "지지 유지 여부 확인", short: "저항 유지 여부 확인" }
  },
  Spring: {
    term: "Spring",
    short: "지지 이탈 후 복귀",
    plain: "박스 하단을 잠깐 이탈했다가 되돌아온 흔적. 마지막 물량 털기(매집 마무리) 경계 신호",
    action_hint: { long: "진입 논리 강화 근거 후보", short: "숏 논리 재점검" }
  },
  Test: {
    term: "Test",
    short: "이탈 재확인",
    plain: "이탈 후 복귀가 진짜였는지 낮은 거래량으로 다시 확인하는 움직임",
    action_hint: { long: "지지 반응 확인", short: "저항 반응 확인" }
  },
  SOS: {
    term: "SOS",
    short: "강세 확인",
    plain: "박스 상단을 거래량을 동반해 돌파한 움직임. 매집 완료 쪽에 무게를 싣는 근거",
    action_hint: { long: "롱 논리 유지 근거", short: "숏 손절 기준 점검" }
  },
  LPS: {
    term: "LPS",
    short: "마지막 지지 확인",
    plain: "돌파 후 되돌림이 얕게 끝나는 지점. 상승 지속 쪽 근거",
    action_hint: { long: "추가 진입 검토 참고", short: "숏 논리 약화 근거" }
  },
  BC: {
    term: "BC",
    short: "매수 절정",
    plain: "환희성 매수로 거래량이 폭발한 캔들. 상승이 소진됐을 가능성을 보는 분산 초기 신호",
    action_hint: { long: "익절 기준 점검", short: "고점 확인 근거 후보" }
  },
  UTAD: {
    term: "UTAD",
    short: "고점 돌파 실패",
    plain: "저항 위로 올렸다가 되밀린 흔적. 분산(고점권 매도) 경계 신호",
    action_hint: { long: "익절 기준 점검", short: "숏 논리 유지 근거" }
  },
  SOW: {
    term: "SOW",
    short: "약세 확인",
    plain: "박스 하단을 거래량을 동반해 이탈한 움직임. 분산 완료 쪽에 무게를 싣는 근거",
    action_hint: { long: "손절 기준 즉시 점검", short: "숏 논리 유지 근거" }
  },
  LPSY: {
    term: "LPSY",
    short: "마지막 반등 실패",
    plain: "이탈 후 반등이 약하게 끝나는 지점. 하락 지속 쪽 근거",
    action_hint: { long: "롱 논리 약화 근거", short: "추가 진입 검토 참고" }
  }
};

export type HarmonicPatternTerm = "gartley" | "bat" | "butterfly" | "crab" | "abcd";

export const HARMONIC_PATTERN_GLOSSARY: Record<HarmonicPatternTerm, TaGlossaryEntry> = {
  gartley: {
    term: "Gartley",
    short: "가틀리 패턴",
    plain: "되돌림 비율이 특정 값(0.786 등)에 맞아떨어지는 5점 반전 패턴. 완성 지점 부근의 반응을 본다"
  },
  bat: {
    term: "Bat",
    short: "배트 패턴",
    plain: "깊은 되돌림(0.886)으로 완성되는 5점 반전 패턴. 완성 지점 부근의 반응을 본다"
  },
  butterfly: {
    term: "Butterfly",
    short: "버터플라이 패턴",
    plain: "시작점을 넘어서는 확장(1.27)으로 완성되는 반전 패턴. 과열 뒤 반전 경계 신호",
    action_hint: { long: "익절/반전 경계 점검", short: "진입 근거 후보" }
  },
  crab: {
    term: "Crab",
    short: "크랩 패턴",
    plain: "가장 깊은 확장(1.618)으로 완성되는 반전 패턴. 극단 이동 뒤 반전 경계 신호"
  },
  abcd: {
    term: "AB=CD",
    short: "대칭 이동 패턴",
    plain: "두 번의 가격 이동(AB, CD)이 같은 크기로 반복되는 패턴. 두 번째 이동이 끝나는 지점의 반응을 본다"
  }
};

export type VolumeStateTerm =
  | "climax_candidate"
  | "absorption_candidate"
  | "volume_expanding"
  | "delta_imbalanced"
  | "drying_up"
  | "balanced_flow"
  | "rebound_with_volume"
  | "declining_after_push"
  | "weak_rebound"
  | "data_unavailable";

export const VOLUME_STATE_GLOSSARY: Record<VolumeStateTerm, TaGlossaryEntry> = {
  climax_candidate: {
    term: "climax",
    short: "거래 절정 후보",
    plain: "거래량 폭발과 큰 가격 이동이 함께 나타난 상태. 추세 소진 가능성을 경계하는 신호",
    action_hint: { long: "다음 캔들 반응 확인 후 익절 검토", short: "다음 캔들 반응 확인" }
  },
  absorption_candidate: {
    term: "absorption",
    short: "물량 흡수 후보",
    plain: "큰 체결량에도 가격이 밀리지 않는 상태. 누군가 물량을 받아내고 있다는 경계 신호"
  },
  volume_expanding: {
    term: "volume_expanding",
    short: "거래량 확대",
    plain: "평소보다 거래가 늘어난 상태. 방향성 캔들이 이어지는지 확인이 필요"
  },
  delta_imbalanced: {
    term: "delta_imbalanced",
    short: "체결 쏠림",
    plain: "매수/매도 체결이 한쪽으로 크게 기운 상태. 포지션 방향과 일치하는지 확인"
  },
  drying_up: {
    term: "drying_up",
    short: "거래량 고갈",
    plain: "거래가 말라가는 상태. 이 구간의 돌파/이탈은 신뢰도를 낮게 본다"
  },
  balanced_flow: {
    term: "balanced_flow",
    short: "체결 균형",
    plain: "매수/매도 체결이 어느 쪽으로도 크게 치우치지 않은 상태"
  },
  rebound_with_volume: {
    term: "rebound_with_volume",
    short: "거래량 동반 반등",
    plain: "반등에 거래량이 붙은 상태. 포지션 방향과 반대라면 리스크 상승 경계 신호"
  },
  declining_after_push: {
    term: "declining_after_push",
    short: "이동 후 둔화",
    plain: "가격 이동 뒤 거래량이 줄어드는 상태. 추격보다 반응 확인이 우선"
  },
  weak_rebound: {
    term: "weak_rebound",
    short: "약한 반응",
    plain: "거래량 확장 없이 약한 반응만 이어지는 상태"
  },
  data_unavailable: {
    term: "data_unavailable",
    short: "체결 데이터 부족",
    plain: "실체결 데이터가 없어 수급 판정을 보류한 상태"
  }
};

export type MarketStructureTerm = "PRZ" | "POC" | "VAH" | "VAL" | "CVD" | "RR" | "ActionFlag" | "Range";

export const MARKET_STRUCTURE_GLOSSARY: Record<MarketStructureTerm, TaGlossaryEntry> = {
  PRZ: {
    term: "PRZ",
    short: "반전 후보 구간",
    plain: "하모닉 비율들이 겹치는 가격대. 도달 시 반전 반응이 나오는지 관찰하는 구간"
  },
  POC: {
    term: "POC",
    short: "최다 거래 가격",
    plain: "거래량이 가장 많이 쌓인 가격. 시장이 가장 오래 합의한 가격대라 지지/저항으로 자주 작동"
  },
  VAH: {
    term: "VAH",
    short: "매물대 상단",
    plain: "거래량의 70%가 몰린 구간의 위쪽 경계"
  },
  VAL: {
    term: "VAL",
    short: "매물대 하단",
    plain: "거래량의 70%가 몰린 구간의 아래쪽 경계"
  },
  CVD: {
    term: "CVD",
    short: "누적 체결 우위",
    plain: "매수 체결과 매도 체결의 차이를 누적한 선. 가격과 방향이 어긋나면 경계 신호"
  },
  RR: {
    term: "RR",
    short: "손익비 구간",
    plain: "초록 구간은 1차 익절 후보까지, 빨강 구간은 무효화 기준까지의 거리입니다. 가격 도달 시 행동 기준을 다시 확인합니다"
  },
  ActionFlag: {
    term: "ActionFlag",
    short: "행동 가격표",
    plain: "무효화·익절·감시 가격을 같은 가격축에 붙인 표식. 도달 시 액션 플랜의 조건을 확인합니다"
  },
  Range: {
    term: "Range",
    short: "가격 레인지",
    plain: "가격이 일정한 상단과 하단 사이에 머문 구간. 경계 반응을 확인합니다"
  }
};

export type StrengthTerm = "strong" | "medium" | "weak";

export const STRENGTH_GLOSSARY: Record<StrengthTerm, TaGlossaryEntry> = {
  strong: { term: "strong", short: "반응 강함", plain: "여러 번 반응했고 점수가 높은 레벨" },
  medium: { term: "medium", short: "반응 보통", plain: "반응 이력이 어느 정도 있는 레벨" },
  weak: { term: "weak", short: "반응 약함", plain: "반응 이력이 적어 신뢰도를 낮게 보는 레벨" }
};

const ALL_GLOSSARIES: Record<string, TaGlossaryEntry> = {
  ...WYCKOFF_EVENT_GLOSSARY,
  ...HARMONIC_PATTERN_GLOSSARY,
  ...VOLUME_STATE_GLOSSARY,
  ...MARKET_STRUCTURE_GLOSSARY,
  ...STRENGTH_GLOSSARY
};

export function taGlossaryEntry(term: string | null | undefined): TaGlossaryEntry | null {
  if (!term) return null;
  return ALL_GLOSSARIES[term] ?? ALL_GLOSSARIES[term.toLowerCase()] ?? null;
}

/** 1차 표기: "고점 돌파 실패(UTAD)". 사전에 없으면 원문 유지. */
export function taShortLabel(term: string | null | undefined): string {
  const entry = taGlossaryEntry(term);
  if (!entry) return term ?? "-";
  return `${entry.short}(${entry.term})`;
}

/** hover 툴팁용: plain 설명 (+방향별 힌트). */
export function taPlainTooltip(term: string | null | undefined, direction?: "long" | "short"): string {
  const entry = taGlossaryEntry(term);
  if (!entry) return term ?? "";
  const hint = direction && entry.action_hint ? `\n${direction === "long" ? "롱" : "숏"} 관점: ${entry.action_hint[direction]}` : "";
  return `${entry.short}(${entry.term})\n${entry.plain}${hint}`;
}

/** 백엔드 구버전/자유 텍스트에 남은 원어·snake_case 노출을 플레인 라벨로 치환한다. */
export function plainifyTaText(text: string | null | undefined): string {
  if (!text) return "";
  let result = localizeMarketCodes(text);
  for (const [code, entry] of Object.entries(VOLUME_STATE_GLOSSARY)) {
    result = result.split(code).join(entry.short);
  }
  result = result
    .replace(/strength\s+strong/g, STRENGTH_GLOSSARY.strong.short)
    .replace(/strength\s+medium/g, STRENGTH_GLOSSARY.medium.short)
    .replace(/strength\s+weak/g, STRENGTH_GLOSSARY.weak.short)
    .replace(/\bPRZ\b/g, "반전 후보 구간(PRZ)");
  return result;
}

export const WYCKOFF_EVENT_MIN_CONFIDENCE_FALLBACK = 55;
export const WYCKOFF_EVENT_DISPLAY_LIMIT = 4;

/** 백엔드가 분리해 주면 그대로 쓰고, 구버전 응답이면 같은 규칙으로 클라이언트에서 분리한다. */
export function splitWyckoffEvents<T extends { confidence: number }>(
  markers: T[],
  lowConfidenceFromBackend?: T[]
): { events: T[]; lowConfidence: T[] } {
  if (lowConfidenceFromBackend !== undefined) {
    return { events: markers, lowConfidence: lowConfidenceFromBackend };
  }
  return {
    events: markers.filter((marker) => marker.confidence >= WYCKOFF_EVENT_MIN_CONFIDENCE_FALLBACK).slice(-WYCKOFF_EVENT_DISPLAY_LIMIT),
    lowConfidence: markers.filter((marker) => marker.confidence < WYCKOFF_EVENT_MIN_CONFIDENCE_FALLBACK)
  };
}
