from __future__ import annotations

from collections import Counter
import json
from pathlib import Path
import sqlite3
from typing import Any

from .parameters import StockPaperParameters, load_stock_parameters
from .policy import evaluate_stock_entry


AUDIT_GATES = (
    "analysis_available",
    "confirmed_flip",
    "evidence",
    "checklist",
    "entry_score",
    "liquidation_safety",
    "risk_reward",
    "data_fresh",
)


def audit_entry_gates(
    database_path: Path,
    *,
    source_version: str,
    policy_paths: tuple[Path, ...],
    observed_to: str | None = None,
) -> dict[str, Any]:
    snapshots = _load_snapshots(database_path, source_version, observed_to=observed_to)
    policies = [load_stock_parameters(path) for path in policy_paths]
    return {
        "source_version": source_version,
        "snapshot_count": len(snapshots),
        "instrument_count": len({(item["market"], item["symbol"]) for item in snapshots}),
        "observed_from": min((item["observed_at"] for item in snapshots), default=None),
        "observed_to": max((item["observed_at"] for item in snapshots), default=None),
        "policies": [_audit_policy(snapshots, policy) for policy in policies],
        "freshness_replay_note": "Snapshots were persisted synchronously from observed candidates; historical data_fresh is replayed as true.",
    }


def render_gate_audit_markdown(audit: dict[str, Any]) -> str:
    lines = [
        f"source={audit['source_version']} · snapshots={audit['snapshot_count']} · instruments={audit['instrument_count']}",
        "",
        "| policy | gate | pass | reject | reject rate |",
        "|---|---|---:|---:|---:|",
    ]
    for policy in audit["policies"]:
        for gate in AUDIT_GATES:
            row = policy["gates"][gate]
            lines.append(f"| {policy['version']} | {gate} | {row['passed']} | {row['rejected']} | {row['rejection_rate_pct']:.1f}% |")
        lines.append(f"| {policy['version']} | **entered** | **{policy['entered']}** | — | — |")
    return "\n".join(lines)


def _load_snapshots(database_path: Path, source_version: str, *, observed_to: str | None) -> list[dict[str, Any]]:
    uri = f"file:{database_path.resolve()}?mode=ro"
    connection = sqlite3.connect(uri, uri=True)
    connection.row_factory = sqlite3.Row
    try:
        if observed_to:
            rows = connection.execute(
                """SELECT market, symbol, observed_at, payload
                FROM stock_paper_analysis_snapshots WHERE parameter_version=? AND observed_at<=?
                ORDER BY observed_at, id""",
                (source_version, observed_to),
            ).fetchall()
        else:
            rows = connection.execute(
                """SELECT market, symbol, observed_at, payload
                FROM stock_paper_analysis_snapshots WHERE parameter_version=?
                ORDER BY observed_at, id""",
                (source_version,),
            ).fetchall()
    finally:
        connection.close()
    return [
        {
            "market": str(row["market"]),
            "symbol": str(row["symbol"]),
            "observed_at": str(row["observed_at"]),
            "analysis": json.loads(row["payload"]),
        }
        for row in rows
    ]


def _audit_policy(snapshots: list[dict[str, Any]], policy: StockPaperParameters) -> dict[str, Any]:
    passed: Counter[str] = Counter()
    rejected: Counter[str] = Counter()
    entered: list[dict[str, Any]] = []
    for snapshot in snapshots:
        decision = evaluate_stock_entry(snapshot["analysis"], data_fresh=True, parameters=policy)
        for gate in AUDIT_GATES:
            status = decision.gate_results[gate]["status"]
            (passed if status == "passed" else rejected)[gate] += 1
        if decision.enter:
            state = (snapshot["analysis"].get("confluence") or {}).get("stance_state") or {}
            entered.append(
                {
                    "market": snapshot["market"],
                    "symbol": snapshot["symbol"],
                    "observed_at": snapshot["observed_at"],
                    "stance": state.get("stance"),
                    "flipped": state.get("flipped"),
                    "transitioning": state.get("transitioning"),
                    "entry_score": snapshot["analysis"].get("entry_score"),
                    "rr_ratio": snapshot["analysis"].get("rr_ratio"),
                    "invalidation": snapshot["analysis"].get("invalidation"),
                }
            )
    total = len(snapshots)
    return {
        "version": policy.version,
        "stance_gate_mode": policy.stance_gate_mode,
        "gates": {
            gate: {
                "passed": passed[gate],
                "rejected": rejected[gate],
                "rejection_rate_pct": round(rejected[gate] / total * 100, 1) if total else 0.0,
            }
            for gate in AUDIT_GATES
        },
        "entered": len(entered),
        "entry_snapshots": entered,
    }
