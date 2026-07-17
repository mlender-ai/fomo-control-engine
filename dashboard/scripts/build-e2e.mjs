import { spawnSync } from "node:child_process";
import { readFileSync, writeFileSync } from "node:fs";

const generatedTypeConfigFiles = ["next-env.d.ts", "tsconfig.json"];
const originals = new Map(
  generatedTypeConfigFiles.map((file) => [file, readFileSync(file)])
);

let result;
try {
  result = spawnSync(
    process.platform === "win32" ? "npm.cmd" : "npm",
    ["run", "build"],
    {
      env: { ...process.env, FCE_NEXT_DIST_DIR: ".next-e2e" },
      stdio: "inherit"
    }
  );
} finally {
  for (const [file, contents] of originals) {
    writeFileSync(file, contents);
  }
}

if (result.error) {
  throw result.error;
}
process.exitCode = result.status ?? 1;
