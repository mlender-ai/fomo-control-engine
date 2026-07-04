export type TaLayer = "minimal" | "structure" | "wyckoff" | "harmonic" | "flow";

export const DEFAULT_TA_LAYER: TaLayer = "minimal";

export const TA_LAYERS: Array<{ id: TaLayer; label: string; description: string }> = [
  { id: "minimal", label: "기본", description: "캔들과 핵심 가격만 표시" },
  { id: "structure", label: "지지·저항", description: "구조 레벨과 매물대(볼륨 프로파일)" },
  { id: "wyckoff", label: "와이코프", description: "국면 박스와 이벤트 마커" },
  { id: "harmonic", label: "하모닉", description: "패턴 구조와 반전 후보 구간(PRZ)" },
  { id: "flow", label: "수급", description: "체결 델타와 누적 수급(CVD)" }
];

export function taLayerLabel(layer: TaLayer): string {
  return TA_LAYERS.find((item) => item.id === layer)?.label ?? layer;
}
