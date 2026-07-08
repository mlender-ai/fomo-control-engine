import { readFileSync } from "node:fs";
import { join } from "node:path";

const root = process.cwd();
const checks = [
  {
    file: "components/live-position-cockpit.tsx",
    patterns: [
      { pattern: 'data-budget-numbers-max="7"', label: "포지션 평결 카드 숫자 예산" },
      { pattern: 'data-budget-buttons-max="4"', label: "포지션 평결 카드 버튼 예산" },
      { pattern: 'data-testid="position-one-question-card"', label: "포지션 원-퀘스천 카드" }
    ]
  },
  {
    file: "components/scout-shell.tsx",
    patterns: [
      { pattern: 'data-budget-columns-max="4"', label: "스카우트 4컬럼 예산" },
      { pattern: 'data-budget-numbers-max="7"', label: "스카우트 평결 카드 숫자 예산" },
      { pattern: 'data-budget-numbers-max="6"', label: "스카우트 즉답 카드 숫자 예산" },
      { pattern: 'data-testid="scout-quick-answer"', label: "스카우트 즉답 카드" },
      { pattern: 'data-testid="scout-minimal-table"', label: "스카우트 미니멀 표" }
    ]
  }
];
const forbidden = [
  {
    file: "lib/chartLayers.ts",
    patterns: [
      { pattern: "MINIMAL_LAYER_PRESETS", label: "미니멀 통합 프리셋 재도입 금지" },
      { pattern: '"structure"', label: "미니멀 구조 프리셋 금지" }
    ]
  },
  {
    file: "components/position/PositionCandlestickChart.tsx",
    patterns: [
      { pattern: "chart-layer-preset-structure", label: "미니멀 구조 버튼 금지" },
      { pattern: "chart-layer-preset-all", label: "미니멀 전체 버튼 금지" }
    ]
  }
];

const failures = [];
for (const check of checks) {
  const content = readFileSync(join(root, check.file), "utf8");
  for (const item of check.patterns) {
    if (!content.includes(item.pattern)) {
      failures.push(`${check.file}: ${item.label} 누락`);
    }
  }
}
for (const check of forbidden) {
  const content = readFileSync(join(root, check.file), "utf8");
  for (const item of check.patterns) {
    if (content.includes(item.pattern)) {
      failures.push(`${check.file}: ${item.label}`);
    }
  }
}

if (failures.length) {
  console.error("미니멀 정보 예산 가드 실패");
  for (const failure of failures) console.error(`- ${failure}`);
  process.exit(1);
}

console.log("미니멀 정보 예산 가드 통과");
