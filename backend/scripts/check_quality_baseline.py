from __future__ import annotations

import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "backend"
BASELINE_PATH = BACKEND / "quality-baseline.json"
COVERAGE_PATH = BACKEND / "coverage.json"


def load_json(path: Path) -> dict:
    return json.loads(path.read_text())


def enforce_coverage(baseline: dict) -> list[str]:
    if not COVERAGE_PATH.exists():
        return [f"missing coverage file: {COVERAGE_PATH}"]
    coverage = load_json(COVERAGE_PATH)
    config = baseline["coverage"]
    errors: list[str] = []
    total = float(coverage["totals"]["percent_covered"])
    if total < float(config["total_min_percent"]):
        errors.append(f"total coverage {total:.2f}% is below {config['total_min_percent']}%")

    files = coverage["files"]
    covered_lines = 0
    statements = 0
    for name, data in files.items():
        if any(name.startswith(path) or name == path for path in config["core_paths"]):
            summary = data["summary"]
            covered_lines += int(summary["covered_lines"])
            statements += int(summary["num_statements"])
    core_percent = 100.0 if statements == 0 else (covered_lines / statements) * 100
    if core_percent < float(config["core_min_percent"]):
        errors.append(f"core coverage {core_percent:.2f}% is below {config['core_min_percent']}%")
    print(f"coverage: total={total:.2f}% core={core_percent:.2f}%")
    return errors


def enforce_exception_count(baseline: dict) -> list[str]:
    config = baseline["exception_comments"]
    patterns = tuple(config["patterns"])
    allowed = int(config["allowed_count"])
    ignored_dirs = {".git", ".next", "node_modules", "__pycache__"}
    count = 0
    matches: list[str] = []
    for base in (ROOT / "backend", ROOT / "dashboard"):
        for path in base.rglob("*"):
            if not path.is_file() or any(part in ignored_dirs for part in path.parts):
                continue
            if path.suffix not in {".py", ".ts", ".tsx", ".js", ".mjs"}:
                continue
            try:
                text = path.read_text()
            except UnicodeDecodeError:
                continue
            for line_number, line in enumerate(text.splitlines(), start=1):
                if any(pattern in line for pattern in patterns):
                    count += 1
                    matches.append(f"{path.relative_to(ROOT)}:{line_number}:{line.strip()}")
    print(f"exception comments: {count}/{allowed}")
    if count > allowed:
        return ["exception comment count increased:\n" + "\n".join(matches)]
    return []


def main() -> int:
    baseline = load_json(BASELINE_PATH)
    errors = [*enforce_coverage(baseline), *enforce_exception_count(baseline)]
    if errors:
        for error in errors:
            print(error, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
