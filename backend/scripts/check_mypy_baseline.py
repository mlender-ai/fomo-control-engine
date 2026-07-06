from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


BACKEND = Path(__file__).resolve().parents[1]
BASELINE_PATH = BACKEND / "quality-baseline.json"


def main() -> int:
    config = json.loads(BASELINE_PATH.read_text())["mypy"]
    command = [
        sys.executable,
        "-m",
        "mypy",
        *config["paths"],
        "--ignore-missing-imports",
        "--follow-imports=silent",
    ]
    result = subprocess.run(command, cwd=BACKEND, text=True, capture_output=True, check=False)
    output = "\n".join(part for part in (result.stdout, result.stderr) if part)
    print(output)
    error_count = output.count(": error:")
    allowed = int(config["allowed_error_count"])
    print(f"mypy errors: {error_count}/{allowed}")
    if error_count > allowed:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
