export type TaFocusLayer = "levels" | "liquidation_realized" | "liquidation_estimated" | "volume_profile" | "wyckoff" | "liquidity" | "harmonic" | "indicators" | "ema" | "onchain";
export type ChartLayerId = "plan" | "flow" | TaFocusLayer;
export type MinimalEvidenceLayer = "plan" | "levels" | "liquidity" | "wyckoff" | "harmonic" | "flow";

export type ChartLayerState = {
  plan: boolean;
  flow: boolean;
  ta: TaFocusLayer[];
};

export type MinimalChartEvidence = {
  layer: MinimalEvidenceLayer;
  label: string;
  price?: number | null;
  time?: number | null;
};

export const DEFAULT_LAYER_STATE: ChartLayerState = {
  plan: true,
  flow: false,
  ta: ["liquidation_realized"]
};

export const MINIMAL_FIXED_LAYER_STATE: ChartLayerState = {
  plan: false,
  flow: false,
  ta: []
};

export const CHART_LAYER_DEFS: Array<{ id: ChartLayerId; label: string; description: string }> = [
  { id: "plan", label: "플랜", description: "무효화·익절 박스와 가격 플래그" },
  { id: "levels", label: "레벨", description: "구조 지지/저항 존 (점수 상위 3+3)" },
  { id: "liquidation_realized", label: "청산밀집(실현)", description: "Bitget에서 실제 발생한 청산 가격 밀집 · 미래 예상 아님" },
  { id: "liquidation_estimated", label: "청산밀집(추정)", description: "Coinglass 추정 어댑터 미연동" },
  { id: "liquidity", label: "유동성", description: "동일 고저점·전고전저 풀과 확정 스윕" },
  { id: "volume_profile", label: "볼륨", description: "볼륨 프로파일 · 최다 거래 가격(POC)" },
  { id: "wyckoff", label: "와이코프", description: "국면 박스와 이벤트 마커" },
  { id: "harmonic", label: "하모닉", description: "패턴 구조와 반전 후보 구간(PRZ)" },
  { id: "ema", label: "EMA", description: "EMA 20~55 리본의 배열·압축·추세 전환" },
  { id: "onchain", label: "온체인", description: "Hyperliquid 등록 지갑의 확정 체결 관측" }
];

const VISIBLE_LAYER_IDS = new Set(CHART_LAYER_DEFS.map((layer) => layer.id));

export const TA_FOCUS_LAYERS: TaFocusLayer[] = ["levels", "liquidation_realized", "liquidation_estimated", "volume_profile", "wyckoff", "liquidity", "harmonic", "indicators", "ema", "onchain"];
export const MAX_COMPARE_LAYERS = 2;

export function isTaLayer(id: ChartLayerId): id is TaFocusLayer {
  return (TA_FOCUS_LAYERS as string[]).includes(id);
}

export function layerActive(state: ChartLayerState, id: ChartLayerId): boolean {
  if (id === "plan") return state.plan;
  if (id === "flow") return state.flow;
  return state.ta.includes(id);
}

export function activeFocusLayers(state: ChartLayerState): ChartLayerId[] {
  return [...state.ta, ...(state.flow ? (["flow"] as ChartLayerId[]) : [])];
}

function focusState(state: ChartLayerState, active: ChartLayerId[]): ChartLayerState {
  const unique = [...new Set(active.filter((id) => id !== "plan"))].slice(-MAX_COMPARE_LAYERS);
  return {
    ...state,
    flow: unique.includes("flow"),
    ta: unique.filter(isTaLayer)
  };
}

export function setFocusedLayer(state: ChartLayerState, id: ChartLayerId): ChartLayerState {
  if (id === "plan") return { ...state, plan: true };
  return focusState(state, [id]);
}

/** 일반 클릭은 독립 토글, shift 비교 추가는 최대 2개다. */
export function toggleLayer(state: ChartLayerState, id: ChartLayerId, additive = false): ChartLayerState {
  if (id === "plan") return { ...state, plan: !state.plan };
  const active = activeFocusLayers(state);
  if (!additive) {
    return focusStateUnbounded(state, active.includes(id) ? active.filter((layer) => layer !== id) : [...active, id]);
  }
  if (active.includes(id)) return focusState(state, active.filter((layer) => layer !== id));
  if (active.length >= MAX_COMPARE_LAYERS) return state;
  return focusState(state, [...active, id]);
}

function focusStateUnbounded(state: ChartLayerState, active: ChartLayerId[]): ChartLayerState {
  const unique = [...new Set(active.filter((id) => id !== "plan"))];
  return {
    ...state,
    flow: unique.includes("flow"),
    ta: unique.filter(isTaLayer)
  };
}

/** 아코디언 동기화용 대표 TA 레이어. */
export function focusedTaLayer(state: ChartLayerState): TaFocusLayer | null {
  return state.ta[0] ?? null;
}

const STORAGE_KEY = "fce.chartLayers.v2";

export function loadLayerState(): ChartLayerState {
  if (typeof window === "undefined") return DEFAULT_LAYER_STATE;
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    if (!raw) return DEFAULT_LAYER_STATE;
    const parsed = JSON.parse(raw) as Partial<ChartLayerState>;
    const base: ChartLayerState = {
      plan: typeof parsed.plan === "boolean" ? parsed.plan : DEFAULT_LAYER_STATE.plan,
      flow: false,
      ta: []
    };
    const storedFocus: ChartLayerId[] = [
      ...(Array.isArray(parsed.ta) ? parsed.ta.filter((layer): layer is TaFocusLayer => (TA_FOCUS_LAYERS as string[]).includes(layer) && VISIBLE_LAYER_IDS.has(layer)) : []),
      ...(parsed.flow === true && VISIBLE_LAYER_IDS.has("flow") ? (["flow"] as ChartLayerId[]) : [])
    ];
    return focusStateUnbounded(base, storedFocus);
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
