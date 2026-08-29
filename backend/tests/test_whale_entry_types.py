"""WO-FCE-WHALE-EXIT-REPLAY-01 2-3 — 진입·청산 복기 회귀."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from app.paper.whale_entry_types import (
    TYPE_HERD,
    TYPE_SOLO,
    TYPE_UNCLASSIFIED,
    UNAVAILABLE_CONTEXT,
    classify_entry,
    classify_exit,
    herd_context,
    performance_by_type,
)

NOW = datetime(2026, 8, 29, 12, 0, tzinfo=timezone.utc)


def _event(addr: str, *, minutes: int = 0, symbol: str = "BTCUSDT", side: str = "long", event: str = "open"):
    return SimpleNamespace(wallet_address=addr, symbol=symbol, side=side, event=event, event_at=NOW - timedelta(minutes=minutes))


def _herd(peers: int) -> dict:
    return {"peer_wallets": peers, "window_minutes": 30, "peers": []}


def test_herd_counts_distinct_peer_wallets_only() -> None:
    events = [_event("0xa", minutes=5), _event("0xa", minutes=6), _event("0xb", minutes=10)]
    assert herd_context(address="0xme", symbol="BTCUSDT", direction="long", at=NOW, peer_events=events)["peer_wallets"] == 2


def test_herd_excludes_the_wallet_itself() -> None:
    events = [_event("0xME", minutes=5), _event("0xb", minutes=5)]
    assert herd_context(address="0xme", symbol="BTCUSDT", direction="long", at=NOW, peer_events=events)["peer_wallets"] == 1


@pytest.mark.parametrize(
    "kwargs",
    [
        {"symbol": "ETHUSDT"},  # 다른 심볼
        {"side": "short"},  # 반대 방향
        {"event": "close"},  # 청산은 진입이 아니다
        {"minutes": 90},  # 창 밖
    ],
)
def test_herd_excludes_non_matching_events(kwargs: dict) -> None:
    events = [_event("0xb", **kwargs)]
    assert herd_context(address="0xme", symbol="BTCUSDT", direction="long", at=NOW, peer_events=events)["peer_wallets"] == 0


def test_herd_window_is_backward_only() -> None:
    """미래 체결은 진입 시점의 맥락이 아니다 — 넣으면 lookahead 다."""
    events = [_event("0xb", minutes=-5)]
    assert herd_context(address="0xme", symbol="BTCUSDT", direction="long", at=NOW, peer_events=events)["peer_wallets"] == 0


def test_two_or_more_peers_is_herd() -> None:
    result = classify_entry(herd=_herd(2), at=NOW, price_move_pct=None)
    assert result["entry_type"] == TYPE_HERD
    assert result["confidence"] > 0


def test_zero_peers_is_solo() -> None:
    assert classify_entry(herd=_herd(0), at=NOW, price_move_pct=None)["entry_type"] == TYPE_SOLO


def test_single_peer_is_unclassified_not_forced() -> None:
    """근거가 애매하면 분류율을 올리려고 억지로 배정하지 않는다(C6)."""
    result = classify_entry(herd=_herd(1), at=NOW, price_move_pct=None)
    assert result["entry_type"] == TYPE_UNCLASSIFIED
    assert result["confidence"] == 0.0


def test_every_classification_is_marked_an_estimate() -> None:
    for peers in (0, 1, 5):
        assert classify_entry(herd=_herd(peers), at=NOW, price_move_pct=None)["estimate"] is True


def test_unavailable_context_is_named_not_invented() -> None:
    """캔들이 없어 산출 못 한 맥락을 침묵시키지 않는다."""
    result = classify_entry(herd=_herd(0), at=NOW, price_move_pct=None)
    assert set(result["unavailable_context"]) == set(UNAVAILABLE_CONTEXT)
    assert "range_position" in result["unavailable_context"]


def test_exit_uses_the_whale_own_pnl_sign() -> None:
    assert classify_exit(closed_pnl=12.0, kind="close", held_seconds=3600)["exit_outcome"] == "take_profit"
    assert classify_exit(closed_pnl=-12.0, kind="close", held_seconds=3600)["exit_outcome"] == "stop"
    assert classify_exit(closed_pnl=0.0, kind="close", held_seconds=3600)["exit_outcome"] == "flat"


def test_exit_without_pnl_stays_unknown() -> None:
    result = classify_exit(closed_pnl=None, kind="reduce", held_seconds=None)
    assert result["exit_outcome"] is None
    assert "미상" in result["exit_label"]


def test_partial_exit_is_distinguished_from_full() -> None:
    assert classify_exit(closed_pnl=1.0, kind="close", held_seconds=1)["full_exit"] is True
    assert classify_exit(closed_pnl=1.0, kind="reduce", held_seconds=1)["full_exit"] is False


def test_exit_note_refuses_a_causal_claim() -> None:
    """C7 — '고래가 팔아서 떨어졌다'고 말하지 않는다."""
    note = classify_exit(closed_pnl=-1.0, kind="close", held_seconds=1)["note"]
    assert "원인" in note and "관측" in note


def test_performance_scores_each_type_separately() -> None:
    rows = [
        {"entry_type": TYPE_HERD, "net_pnl_usdt": 10.0},
        {"entry_type": TYPE_HERD, "net_pnl_usdt": -4.0},
        {"entry_type": TYPE_SOLO, "net_pnl_usdt": -6.0},
    ]
    result = performance_by_type(rows)
    assert result["by_type"][TYPE_HERD]["count"] == 2
    assert result["by_type"][TYPE_HERD]["net_usdt"] == 6.0
    assert result["by_type"][TYPE_HERD]["win_pct"] == 50.0
    assert result["by_type"][TYPE_HERD]["profit_factor"] == 2.5
    assert result["by_type"][TYPE_SOLO]["net_usdt"] == -6.0


def test_small_samples_carry_their_own_warning() -> None:
    result = performance_by_type([{"entry_type": TYPE_SOLO, "net_pnl_usdt": 1.0}])
    assert "N<30" in result["by_type"][TYPE_SOLO]["sample_note"]


def test_unclassified_share_is_reported_not_hidden() -> None:
    rows = [{"entry_type": TYPE_UNCLASSIFIED, "net_pnl_usdt": 1.0}, {"entry_type": TYPE_SOLO, "net_pnl_usdt": 1.0}]
    assert performance_by_type(rows)["unclassified_pct"] == 50.0


def test_open_trades_are_excluded_from_scoring() -> None:
    """미실현 거래를 성적에 넣으면 성적이 거짓이 된다."""
    rows = [{"entry_type": TYPE_SOLO, "net_pnl_usdt": None}, {"entry_type": TYPE_SOLO, "net_pnl_usdt": 2.0}]
    assert performance_by_type(rows)["by_type"][TYPE_SOLO]["count"] == 1


def test_type_performance_is_declared_out_of_selection() -> None:
    """C8 — 유형별 성적이 추종 자격으로 역류하지 않는다."""
    result = performance_by_type([{"entry_type": TYPE_SOLO, "net_pnl_usdt": 1.0}])
    assert "선정" in result["not_selection_criteria"]
    assert result["estimate"] is True


def test_daily_report_labels_exit_b_as_counterfactual_not_performance() -> None:
    """C11 — 반사실을 실적처럼 보내면 리포트가 거짓말이 된다."""
    from app.notify import daily_report_source

    src = __import__("pathlib").Path(daily_report_source.__file__).read_text()
    assert "반사실" in src
    assert "전환 근거 아님" in src


def test_whale_tab_payload_carries_both_replay_blocks() -> None:
    """2-4 — 대조가 화면에 없으면 진단이 텔레그램 한 줄로만 남는다."""
    from app.onchain import service

    src = __import__("pathlib").Path(service.__file__).read_text()
    assert '"exit_comparison"' in src
    assert '"entry_replay"' in src


def test_replay_block_survives_a_failing_dependency() -> None:
    """조회 실패가 고래 탭 전체를 죽이면 진단 자체가 사라진다."""
    from app.onchain.service import _exit_replay_block

    class Broken:
        def __getattr__(self, name: str):
            raise RuntimeError("ledger down")

    block = _exit_replay_block(Broken(), object())
    assert block["exit_comparison"]["available"] is False
    assert block["entry_replay"]["available"] is False


def test_event_cache_queries_each_wallet_symbol_once() -> None:
    """거래마다 조회하면 워커와 락을 다투며 진단 화면이 느려진다 — 느려서 안 보면 진단이 없다."""
    from app.paper.whale_exit_replay import EventCache

    calls: list[tuple] = []

    class Repo:
        def list_whale_events(self, *, wallet_address: str, symbol: str, limit: int):
            calls.append((wallet_address, symbol))
            return []

    cache = EventCache(Repo())
    for _ in range(20):
        cache.events("0xA", "BTCUSDT")
        cache.events("0xa", "btcusdt")
    cache.events("0xA", "ETHUSDT")
    assert len(calls) == 2
