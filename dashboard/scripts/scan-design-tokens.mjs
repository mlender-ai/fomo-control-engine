import { readFileSync } from "node:fs";
import { join } from "node:path";
import { fileURLToPath } from "node:url";

const root = fileURLToPath(new URL("..", import.meta.url));
const targets = [
  "components/live-position-cockpit.tsx",
  "components/position/PositionCandlestickChart.tsx",
  "components/position/PriceLevelOverlay.tsx",
  "components/position/PositionChart.tsx",
  "components/symbol-analysis-view.tsx",
  "lib/chartTheme.ts"
];

const checks = [
  { name: "hardcoded hex color", pattern: /#[0-9a-fA-F]{3,8}\b/g },
  {
    name: "inline px style",
    pattern: /\b(?:fontSize|borderRadius|padding|margin|gap|width|height|minHeight|maxWidth|minWidth)\s*:\s*["'`]?\d+px/g
  }
];

const violations = [];

for (const target of targets) {
  const path = join(root, target);
  const source = readFileSync(path, "utf8");
  for (const check of checks) {
    const matches = source.match(check.pattern) ?? [];
    for (const match of matches) {
      violations.push(`${target}: ${check.name}: ${match}`);
    }
  }
}

if (violations.length) {
  console.error("Design token scan failed:");
  for (const violation of violations) {
    console.error(`- ${violation}`);
  }
  process.exit(1);
}

console.log(`Design token scan passed (${targets.length} files).`);
