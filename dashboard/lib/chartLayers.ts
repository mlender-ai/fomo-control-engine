export type TaFocusLayer = "levels" | "volume_profile" | "wyckoff" | "harmonic" | "indicators";
export type ChartLayerId = "plan" | "scenario" | "flow" | TaFocusLayer;

export type ChartLayerState = {
  plan: boolean;
  scenario: boolean;
  flow: boolean;
  ta: TaFocusLayer[];
};

export const DEFAULT_LAYER_STATE: ChartLayerState = {
  plan: true,
  scenario: false,
  flow: false,
  ta: []
};

export const CHART_LAYER_DEFS: Array<{ id: ChartLayerId; label: string; description: string }> = [
  { id: "plan", label: "플랜", description: "무효화·익절 박스와 가격 플래그" },
  { id: "scenario", label: "시나리오", description: "예측이 아닌 조건 경로 가이드" },
  { id: "levels", label: "레벨", description: "구조 지지/저항 존 (점수 상위 3+3)" },
  { id: "volume_profile", label: "볼륨", description: "볼륨 프로파일 · 최다 거래 가격(POC)" },
  { id: "wyckoff", label: "와이코프", description: "국면 박스와 이벤트 마커" },
  { id: "harmonic", label: "하모닉", description: "패턴 구조와 반전 후보 구간(PRZ)" },
  { id: "flow", label: "수급", description: "체결 델타 · 누적 수급(CVD)" },
  { id: "indicators", label: "지표", description: "볼린저 밴드" }
];

export const TA_FOCUS_LAYERS: TaFocusLayer[] = ["levels", "volume_profile", "wyckoff", "harmonic", "indicators"];

export function isTaLayer(id: ChartLayerId): id is TaFocusLayer {
  return (TA_FOCUS_LAYERS as string[]).includes(id);
}

export function layerActive(state: ChartLayerState, id: ChartLayerId): boolean {
  if (id === "plan") return state.plan;
  if (id === "scenario") return state.scenario;
  if (id === "flow") return state.flow;
  return state.ta.includes(id);
}

/** 포커스 모드: TA 레이어는 상호 배타. additive(shift-클릭) = 비교 모드로 다중 선택. */
export function toggleLayer(state: ChartLayerState, id: ChartLayerId, additive = false): ChartLayerState {
  if (id === "plan") return { ...state, plan: !state.plan };
  if (id === "scenario") return { ...state, scenario: !state.scenario };
  if (id === "flow") return { ...state, flow: !state.flow };
  if (state.ta.includes(id)) {
    return { ...state, ta: state.ta.filter((layer) => layer !== id) };
  }
  return { ...state, ta: additive ? [...state.ta, id] : [id] };
}

/** 아코디언 동기화용 단일 포커스 TA (비교 모드에서는 첫 번째). */
export function focusedTaLayer(state: ChartLayerState): TaFocusLayer | null {
  return state.ta[0] ?? null;
}

const STORAGE_KEY = "fce.chartLayers.v1";

export function loadLayerState(): ChartLayerState {
  if (typeof window === "undefined") return DEFAULT_LAYER_STATE;
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    if (!raw) return DEFAULT_LAYER_STATE;
    const parsed = JSON.parse(raw) as Partial<ChartLayerState>;
    return {
      plan: typeof parsed.plan === "boolean" ? parsed.plan : DEFAULT_LAYER_STATE.plan,
      scenario: typeof parsed.scenario === "boolean" ? parsed.scenario : DEFAULT_LAYER_STATE.scenario,
      flow: typeof parsed.flow === "boolean" ? parsed.flow : DEFAULT_LAYER_STATE.flow,
      ta: Array.isArray(parsed.ta) ? parsed.ta.filter((layer): layer is TaFocusLayer => (TA_FOCUS_LAYERS as string[]).includes(layer)) : []
    };
  } catch {
    return DEFAULT_LAYER_STATE;
  }
}

export function saveLayerState(state: ChartLayerState): void {
  if (typeof window === "undefined") return;
  try {
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify(state));
  } catch {
    // localStorage 비활성 환경에서는 세션 상태로만 동작
  }
}
