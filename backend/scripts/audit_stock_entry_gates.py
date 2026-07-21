from __future__ import annotations

import argparse
from pathlib import Path

from app.stock_paper.audit import audit_entry_gates, render_gate_audit_markdown


def main() -> None:
    parser = argparse.ArgumentParser(description="Replay persisted stock analyses through versioned entry policies.")
    parser.add_argument("database", type=Path)
    parser.add_argument("--source-version", default="stock-v2")
    parser.add_argument("--observed-to")
    parser.add_argument("--policy", action="append", type=Path, required=True)
    args = parser.parse_args()
    audit = audit_entry_gates(
        args.database,
        source_version=args.source_version,
        policy_paths=tuple(args.policy),
        observed_to=args.observed_to,
    )
    print(render_gate_audit_markdown(audit))


if __name__ == "__main__":
    main()
