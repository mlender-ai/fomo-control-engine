"""FOMO LAB ENG-02 — 그림자: 같은 신호 · 하나만 다르게 · 본 트랙 영향 0 · 동시 3 · 파라미터 1 · 승인은 기록만."""

from __future__ import annotations

import json
from dataclasses import fields

import pytest

from app.backtest.signatures import signatures_from_analysis
from app.db.models import WatchlistItem
from app.db.repository import MemoryRepository
from app.paper import shadows
from app.paper.policy import PaperPolicy
from app.paper.service import run_paper_engine
from tests.test_paper_policy import BASE_TIME, _settings


def _payload() -> tuple[dict, dict]:
    analysis = {
        "symbol": "TESTUSDT",
        "timeframe": "4h",
        "asset_class": "crypto",
        "mark_price": 100,
        "candles": [{"time": int(BASE_TIME.timestamp()), "open": 100, "high": 101, "low": 99, "close": 100, "volume": 1_000}],
        "liquidity": {
            "sweeps": [
                {
                    "id": "sweep-1",
                    "confirmed": True,
                    "side": "sell_side",
                    "type": "sweep",
                    "grade": "Strong",
                    "confidence": 80,
                    "timestamp": int(BASE_TIME.timestamp()),
                }
            ]
        },
    }
    signature = signatures_from_analysis(analysis)[0]
    payload = {
        "analysis": analysis,
        "historical_backtest": {"stats": [{"signature_key": signature["key"], "signature": signature, "sample_size": 40, "win_1r_ci": [55.0, 70.0]}]},
        "analyst_briefing": {
            "confluence": {
                "stance": "long_leaning",
                "stance_state": {"stance": "long_leaning", "flipped": True, "transitioning": False},
                "long_evidence": [{"claim": str(i)} for i in range(4)],
                "short_evidence": [],
            }
        },
        "gauges": {"bar_state": {"provisional": False}},
    }
    simulation = {
        "rr_ratio": 2.0,
        "survives_to_invalidation": True,
        "checklist": [{"status": "pass"}] * 6,
        "checklist_passed": 6,
        "checklist_total": 6,
        "action_plan": {"invalidation": {"price": 98.5}, "take_profit": [{"price": 110}]},
    }
    return payload, simulation


def _run(repo, active_file, monkeypatch):
    payload, simulation = _payload()
    monkeypatch.setattr(shadows, "ACTIVE_FILE", active_file)
    repo.upsert_watchlist_item(WatchlistItem(symbol="TESTUSDT", asset_class="crypto"))
    repo.upsert_paper_engine_state("TESTUSDT", "4h", {"last_bar_at": BASE_TIME.isoformat()})
    return run_paper_engine(
        repo,
        _settings(),
        analysis_loader=lambda _s, _t: payload,
        simulation_loader=lambda *_a: simulation,
        now=BASE_TIME,
    )


def _main_ledger(repo) -> list[tuple]:
    return [(t.symbol, t.direction.value, t.entry_price, t.quantity, t.stop_price, t.take_profit_price, t.status) for t in repo.list_paper_trades()]


def test_shadow_does_not_touch_main_track(tmp_path, monkeypatch):
    """본 트랙 영향 0 — 그림자를 켜도 `paper_trades` 가 한 글자도 다르지 않다."""
    none = tmp_path / "none.json"
    with_shadow = tmp_path / "active.json"
    with_shadow.write_text(json.dumps([{"number": 7, "param": "risk_budget_usdt", "value": 10.0}]))

    plain, shadowed = MemoryRepository(), MemoryRepository()
    first = _run(plain, none, monkeypatch)
    second = _run(shadowed, with_shadow, monkeypatch)

    assert first["opened"] == second["opened"] == 1
    assert _main_ledger(plain) == _main_ledger(shadowed)
    assert plain.list_paper_shadow_trades() == []
    shadow_rows = shadowed.list_paper_shadow_trades(shadow=7)
    assert len(shadow_rows) == 1
    # 같은 신호 · 다른 것은 하나 — 1R 금액이 4배라 수량만 다르다
    (_, s), main = shadow_rows[0], shadowed.list_paper_trades()[0]
    assert (s.symbol, s.direction, s.entry_price, s.stop_price) == (main.symbol, main.direction, main.entry_price, main.stop_price)
    assert s.quantity != main.quantity
    assert s.entry_evidence["shadow"] == 7
    assert any(e.get("kind") == "shadow_opened" for e in second["events"])


def test_broken_shadow_file_never_breaks_main(tmp_path, monkeypatch):
    bad = tmp_path / "active.json"
    bad.write_text("{not json")
    repo = MemoryRepository()
    assert _run(repo, bad, monkeypatch)["opened"] == 1


def test_load_active_enforces_limits(tmp_path):
    path = tmp_path / "active.json"
    path.write_text(
        json.dumps(
            [
                {"number": 1, "param": "max_entry_cost_r", "value": 0.12},
                {"number": 2, "param": "risk_mode", "value": "structural"},
                {"number": 3, "param": "risk_budget_usdt", "value": 10},
                {"number": 4, "param": "max_entry_cost_r", "value": 0.1},  # 같은 파라미터 두 번 — 거부
                {"number": 5, "param": "min_rr", "value": 1.0},  # 허용 목록 밖 — 거부
                {"number": 6, "param": "risk_mode", "value": "yolo"},  # 모드 밖 — 거부
                {"number": 8, "param": "risk_budget_usdt", "value": 5, "changes": {"leverage": 10}},  # 둘 이상 — 거부
            ]
        )
    )
    active = shadows.load_active(path)
    assert [s.number for s in active] == [1, 2, 3]
    assert len(active) <= shadows.MAX_ACTIVE


def test_shadow_policy_changes_exactly_one_field():
    base = PaperPolicy()
    changed = shadows.shadow_policy(base, shadows.Shadow(number=1, param="max_entry_cost_r", value=0.12))
    differing = [f.name for f in fields(PaperPolicy) if getattr(changed, f.name) != getattr(base, f.name)]
    assert differing == ["max_entry_cost_r"]


def test_decision_is_only_recorded(tmp_path):
    """`/approve` 는 기록만 — 정책 파일도 본 트랙도 건드리지 않는다."""
    path = tmp_path / "decisions.jsonl"
    row = shadows.record_decision("approve", 7, reason="", chat_id=123, at=BASE_TIME, path=path)
    assert row["number"] == 7
    assert json.loads(path.read_text().strip())["action"] == "approve"


@pytest.mark.parametrize("value", [True, "0.1", None])
def test_numeric_param_rejects_non_numbers(tmp_path, value):
    path = tmp_path / "active.json"
    path.write_text(json.dumps([{"number": 1, "param": "max_entry_cost_r", "value": value}]))
    assert shadows.load_active(path) == []


def test_shadow_exits_on_its_own_ledger(tmp_path, monkeypatch):
    """다음 봉이 손절선을 뚫으면 그림자도 **자기 원장에서** 닫힌다."""
    from datetime import timedelta

    active = tmp_path / "active.json"
    active.write_text(json.dumps([{"number": 9, "param": "risk_budget_usdt", "value": 10.0}]))
    repo = MemoryRepository()
    _run(repo, active, monkeypatch)
    payload, simulation = _payload()
    later = BASE_TIME + timedelta(hours=4)
    payload["analysis"]["candles"].append({"time": int(later.timestamp()), "open": 99, "high": 99.5, "low": 90, "close": 91, "volume": 1_000})
    result = run_paper_engine(repo, _settings(), analysis_loader=lambda _s, _t: payload, simulation_loader=lambda *_a: simulation, now=later)
    ((_, shadow_trade),) = repo.list_paper_shadow_trades(shadow=9)
    assert shadow_trade.status == "closed"
    assert shadow_trade.exit_reason in {"invalidation_breach", "liquidation"}
    assert repo.list_paper_trades(status="closed")  # 본 트랙도 제 규칙대로 닫혔다
    assert any(e.get("kind") == "shadow_close" for e in result["events"])
