export type Density = "simple" | "detailed";

export const DEFAULT_DENSITY: Density = "simple";

const STORAGE_KEY = "fce.density.v1";

export function loadDensity(): Density {
  if (typeof window === "undefined") return DEFAULT_DENSITY;
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    return raw === "detailed" ? "detailed" : DEFAULT_DENSITY;
  } catch {
    return DEFAULT_DENSITY;
  }
}

export function saveDensity(density: Density): void {
  if (typeof window === "undefined") return;
  try {
    window.localStorage.setItem(STORAGE_KEY, density);
  } catch {
    // localStorage 비활성 환경에서는 세션 기본값으로 동작
  }
}

/** 간단 모드: 신뢰도 숫자 대신 강/중/약 3단 텍스트. */
export function confidenceLabel(confidence: number, density: Density): string {
  if (density === "detailed") return `신뢰도 ${confidence}`;
  if (confidence >= 80) return "신뢰도 강";
  if (confidence >= 60) return "신뢰도 중";
  return "신뢰도 약";
}

/** 간단 모드에서 표시할 이벤트 수 상한. */
export function eventDisplayLimit(density: Density): number {
  return density === "simple" ? 2 : 4;
}
