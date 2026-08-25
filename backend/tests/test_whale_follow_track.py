"""WO-FCE-WHALE-FOLLOW-01 Phase 6 — 관찰 자격 · 추종 트랙 · 알림 계약.

Phase 5 는 승격자 0명이라 트랙을 배선하지 않았다. Phase 6 가 그 조건을 **대체**했다 —
승격 기준을 페이퍼의 전제로 쓰는 것이 순환이었기 때문이다. 승격 기준 자체는 그대로다.
"""

from __future__ import annotations

import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

import pytest

from app.db.models import Direction, MarketCandle, PaperTrade
from app.notify import delivery_gate, whale_follow_alerts
from app.onchain import follow_eligibility as fe
from app.onchain import participant_type
from app.paper import policy as paper_policy
from app.paper import whale_follow

REPO_ROOT = Path(__file__).resolve().parents[2]
NOW = datetime(2026, 8, 25, 12, 0, tzinfo=timezone.utc)

UNTOUCHABLE = ("backend/app/paper/policy.py", "backend/app/analyst", "backend/app/structure")


# ── 6-1 관찰 자격 ──────────────────────────────────────────────────────


def _status(**overrides):
    base = {
        "address": "0xabc",
        "sample_size": 25,
        "wins": 15,
        "ci_low": 40.0,
        "estimate": {"participant_type": participant_type.TYPE_DIRECTIONAL},
        "last_fill_at": NOW,
        "now": NOW,
    }
    return fe.observation_status(**{**base, **overrides})


def test_observation_bar_is_lower_than_promotion() -> None:
    """관찰은 N>=20 · 승률 점추정, 승격은 N>=30 · CI 하한 55%. 다른 문턱이다."""
    assert fe.OBSERVATION_MIN_SAMPLE == 20
    assert fe.OBSERVATION_MIN_WIN_PCT == 50.0
    # CI 하한이 승격 임계에 한참 못 미쳐도 관찰은 통과한다 — 그것이 분리의 요점이다.
    status = _status(sample_size=25, wins=15, ci_low=32.0)
    assert status.eligible is True
    assert status.ci_low == 32.0


def test_sample_below_bar_is_rejected() -> None:
    status = _status(sample_size=19)
    assert status.eligible is False
    assert "19/20" in status.reason


def test_win_rate_at_the_boundary_is_rejected() -> None:
    """50.0% 는 '초과' 가 아니다. 동전 던지기를 신호로 삼지 않는다."""
    status = _status(sample_size=34, wins=17)
    assert status.eligible is False
    assert "50.0%" in status.reason


def test_market_maker_is_never_eligible() -> None:
    """C4 — 보수성이 아니라 정의상 잡음이다."""
    status = _status(estimate={"participant_type": participant_type.TYPE_MARKET_MAKER}, sample_size=99, wins=99)
    assert status.eligible is False
    assert "C4" in status.reason


def test_basis_carry_is_never_eligible() -> None:
    status = _status(estimate={"participant_type": participant_type.TYPE_BASIS_CARRY}, sample_size=99, wins=99)
    assert status.eligible is False


def test_unclassified_is_eligible_but_flagged() -> None:
    """모르는 것을 배제하면 영원히 모른다. 허용하되 플래그한다(6-1 항목 3)."""
    status = _status(estimate={"participant_type": participant_type.TYPE_UNCLASSIFIED})
    assert status.eligible is True
    assert status.unclassified_flag is True
    assert "미분류" in status.reason


def test_idle_wallet_is_rejected() -> None:
    status = _status(last_fill_at=NOW - timedelta(days=30))
    assert status.eligible is False
    assert "활동 중이 아니다" in status.reason


def test_every_status_carries_the_unverified_label() -> None:
    """C8·C11 — 관찰 자격이 승격으로 읽히면 안 된다."""
    payload = _status().as_payload()
    assert payload["label"] == "미검증 관찰 자격"
    assert "승격 근거로 쓰지 않는다" in payload["not_promotion"]


def test_contaminated_sample_is_named_in_the_reason() -> None:
    """6-4 — 계수에서 뺐다는 사실이 사유에 남는다(C10)."""
    status = _status(sample_size=0, wins=0, excluded_sample=11, estimate={"participant_type": participant_type.TYPE_UNCLASSIFIED})
    assert status.eligible is False
    assert "오염 11건 제외" in status.reason


def test_eligibility_module_does_not_read_promotion_thresholds() -> None:
    """C1 — 두 축이 서로를 참조하지 않는다."""
    source = (REPO_ROOT / "backend/app/onchain/follow_eligibility.py").read_text(encoding="utf-8")
    for forbidden in ("WHALE_VALIDATION_DAYS", "trust_status", "_wallet_review"):
        assert forbidden not in source.replace("28일 · N>=30 · CI 하한 55%", ""), f"승격 판정을 참조한다: {forbidden}"


# ── 6-2 추종 트랙 ──────────────────────────────────────────────────────


class _Event:
    def __init__(self, address: str, symbol: str, side: str, event: str, at: datetime, size_usd: float = 500_000.0) -> None:
        self.wallet_address = address
        self.symbol = symbol
        self.side = side
        self.event = event
        self.event_at = at
        self.size_usd = size_usd
        self.wallet_label = "고래"


def test_only_increase_events_trigger_entries() -> None:
    """감액·청산은 진입 신호가 아니다 — 별도 판단이다(6-2 항목 2)."""
    events = [
        _Event("0xa", "BTCUSDT", "long", "increase", NOW),
        _Event("0xa", "ETHUSDT", "long", "reduce", NOW),
        _Event("0xa", "SOLUSDT", "short", "close", NOW),
        _Event("0xa", "XRPUSDT", "short", "flip", NOW),
    ]
    signals = whale_follow.entry_signals(events, eligible={"0xa": "observation"}, now=NOW)
    assert {signal["symbol"] for signal in signals} == {"BTCUSDT", "XRPUSDT"}


def test_ineligible_wallets_produce_no_signal() -> None:
    events = [_Event("0xbad", "BTCUSDT", "long", "increase", NOW)]
    assert whale_follow.entry_signals(events, eligible={"0xa": "observation"}, now=NOW) == []


def test_stale_signals_are_dropped() -> None:
    """지연이 커지면 신호가 아니라 소음이다."""
    events = [_Event("0xa", "BTCUSDT", "long", "increase", NOW - timedelta(hours=12))]
    assert whale_follow.entry_signals(events, eligible={"0xa": "observation"}, now=NOW) == []


def test_latest_event_per_wallet_symbol_wins() -> None:
    events = [
        _Event("0xa", "BTCUSDT", "long", "increase", NOW - timedelta(minutes=30)),
        _Event("0xa", "BTCUSDT", "short", "flip", NOW - timedelta(minutes=5)),
    ]
    signals = whale_follow.entry_signals(events, eligible={"0xa": "observation"}, now=NOW)
    assert len(signals) == 1
    assert signals[0]["direction"] == Direction.short


def _candidate(**overrides):
    bar = MarketCandle(timestamp=NOW, open=100.0, high=101.0, low=99.0, close=100.0, volume=10.0)
    policy = paper_policy.PaperPolicy(margin_usdt=100.0, leverage=5.0)
    base = {
        "signal": {
            "address": "0xa",
            "qualification": fe.QUALIFICATION_OBSERVATION,
            "symbol": "BTCUSDT",
            "direction": Direction.long,
            "event_at": NOW - timedelta(minutes=12),
            "event": "increase",
            "size_usd": 900_000.0,
            "wallet_label": "고래",
            "participant_type": "unclassified",
            "unclassified_flag": True,
            "sample_size": 38,
            "ci_low": 50.0,
        },
        "bar": bar,
        "asset_class": "crypto",
        "policy": policy,
        "invalidation": 97.0,
        "take_profit": 106.0,
        "take_profit_2": 112.0,
        "entry_atr": 2.0,
        "target_plan": {},
        "simulation": {},
        "timeframe": "4h",
    }
    return {**base, **overrides}


def test_open_follow_trade_reuses_policy_sizing() -> None:
    """C5 — 신규 사이징 구현 0건. `policy.open_trade` 가 만든 결과와 같아야 한다."""
    trade = whale_follow.open_follow_trade(_candidate(), now=NOW)
    expected = paper_policy.plan_position_size(entry_price=100.0, invalidation_price=97.0, policy=paper_policy.PaperPolicy(margin_usdt=100.0, leverage=5.0))
    assert trade.quantity == pytest.approx(expected["quantity"])
    assert (trade.target_plan or {})["sizing"]["mode"] == expected["mode"]


def test_entry_records_qualification_and_latency() -> None:
    """C3·C8 — 관찰 자격 진입은 그 사실이 원장에 남아야 한다. 지연도 함께."""
    trade = whale_follow.open_follow_trade(_candidate(), now=NOW)
    evidence = trade.entry_evidence
    assert evidence["track"] == "whale_follow"
    assert evidence["qualification"] == fe.QUALIFICATION_OBSERVATION
    assert evidence["unverified"] is True
    assert evidence["label"] == "미검증 관찰 자격 진입"
    assert evidence["signal_to_entry_seconds"] == pytest.approx(720.0)
    assert evidence["sample_size"] == 38
    assert "실주문이 아닌" in evidence["note"]


def test_direction_gates_are_excluded_from_the_gate_scope() -> None:
    """이 트랙의 가설은 고래 신호가 방향 판단을 대체한다는 것이다."""
    source = (REPO_ROOT / "backend/app/paper/whale_follow.py").read_text(encoding="utf-8")
    gates = source.split("def _safety_gates")[1].split("def ")[0]
    for excluded in ("confirmed_stance", "signature_gate", "regime_gate", "risk_reward", "checklist"):
        assert f'"{excluded}"' not in gates, f"방향 판단 게이트가 적용 범위에 들어왔다: {excluded}"
    for kept in ("freshness", "liquidation_safety", "action_levels", "invalidation_hygiene"):
        assert f'"{kept}"' in gates, f"안전 게이트가 빠졌다: {kept}"


def test_exit_does_not_reintroduce_stance_judgement() -> None:
    """진입에서 뺀 스탠스를 청산에서 되살리면 방향 판단이 뒷문으로 들어온다."""
    source = (REPO_ROOT / "backend/app/paper/whale_follow.py").read_text(encoding="utf-8")
    exits = source.split("def run_exits")[1].split("def performance_by_qualification")[0]
    assert "stance_state={}" in exits


def test_analysis_lookups_are_capped_per_run() -> None:
    """C9 — 진입 상한만으로는 부족하다. 전부 거부되면 분석 조회가 무제한이 된다."""
    assert whale_follow.MAX_EVALUATIONS_PER_RUN <= 5
    assert whale_follow.MAX_EXIT_EVALUATIONS_PER_RUN <= 10

    class _Repo:
        def __init__(self) -> None:
            self.calls = 0

        def list_whale_events(self, wallet_address=None, limit=200):
            return [_Event("0xa", f"SYM{index}USDT", "long", "increase", NOW, size_usd=1000.0 * index) for index in range(1, 9)]

        def list_whale_follow_trades(self, status=None, symbol=None, limit=500):
            return []

    repo = _Repo()
    loaded: list[str] = []

    def loader(symbol: str, timeframe: str) -> dict:
        loaded.append(symbol)
        return {}

    result = whale_follow.run_entries(
        repo,
        object(),
        eligible={"0xa": "observation"},
        analysis_loader=loader,
        simulation_loader=lambda *args: {},
        now=NOW,
    )
    assert len(loaded) <= whale_follow.MAX_EVALUATIONS_PER_RUN
    assert result["evaluated"] <= whale_follow.MAX_EVALUATIONS_PER_RUN
    # C10 — 잘린 신호가 조용히 사라지지 않는다.
    assert any("상한" in str(item.get("reason")) for item in result["rejected"])


def test_track_is_registered_with_a_separate_ledger() -> None:
    """C3 — 크립토 트랙 원장에 기입하지 않는다."""
    from app.validation.sample_viability import TRACK_SAMPLE_SPECS

    spec = TRACK_SAMPLE_SPECS["whale_follow"]
    assert "whale_follow_trades" in spec.entry_sql
    assert "paper_trades" not in spec.entry_sql
    assert "paper_trades" not in spec.scored_sql
    crypto = TRACK_SAMPLE_SPECS["crypto"]
    assert "whale_follow" not in crypto.entry_sql, "크립토 트랙이 고래 거래를 세고 있다"


def test_follow_engine_never_writes_to_the_crypto_ledger() -> None:
    """C3 — grep 증명. 추종 엔진에 `upsert_paper_trade` 호출이 없다."""
    source = (REPO_ROOT / "backend/app/paper/whale_follow.py").read_text(encoding="utf-8")
    assert "upsert_paper_trade" not in source
    assert "upsert_whale_follow_trade" in source


# ── 6-3 알림 ───────────────────────────────────────────────────────────


def _trade(*, address: str = "0xa", status: str = "open", qualification: str = "observation", entry_at: datetime | None = None) -> PaperTrade:
    trade = whale_follow.open_follow_trade(_candidate(signal={**_candidate()["signal"], "address": address, "qualification": qualification}), now=NOW)
    update: dict = {"id": uuid4(), "status": status}
    if entry_at is not None:
        update["entry_at"] = entry_at
    return trade.model_copy(update=update)


def test_rule_is_registered_and_distinct_from_whale_entry() -> None:
    assert whale_follow_alerts.WHALE_FOLLOW_RULE_ID == "whale_follow_entry"
    assert whale_follow_alerts.WHALE_FOLLOW_RULE_ID != "whale_entry"
    assert delivery_gate.evaluate_rule(whale_follow_alerts.WHALE_FOLLOW_RULE_ID).allowed is True
    # 강등된 rule 은 강등 상태 그대로다.
    assert delivery_gate.evaluate_rule("whale_entry").allowed is False


def test_identity_does_not_depend_on_batch_content() -> None:
    """C7 — 이것이 스팸 사고의 기전이었다. 배치가 바뀌어도 키가 같아야 한다."""
    first = whale_follow_alerts.alert_identity(address="0xA", symbol="BTCUSDT", direction="long", phase="opened")
    second = whale_follow_alerts.alert_identity(address="0xa", symbol="btcusdt", direction="long", phase="opened")
    assert first == second
    for token in ("fill", "count", "size", "2026"):
        assert token not in first


def test_state_key_is_stable_across_repeated_entries() -> None:
    from app.notify.rules import AlertCandidate

    def key(trade: PaperTrade) -> str:
        candidates = whale_follow_alerts.build_candidates([trade], now=NOW)["candidates"]
        candidate: AlertCandidate = candidates[0]
        return candidate.state_key

    assert key(_trade()) == key(_trade())


def test_per_run_cap_blocks_the_excess() -> None:
    trades = [_trade(address=f"0x{index}") for index in range(9)]
    result = whale_follow_alerts.build_candidates(trades, now=NOW, per_run_limit=3)
    assert len(result["candidates"]) == 3
    assert any(item.get("cap") == "per_run" for item in result["blocked"])


def test_per_wallet_hourly_cap_blocks_the_excess() -> None:
    recent = [_trade(address="0xa", entry_at=NOW - timedelta(minutes=10)) for _ in range(3)]
    result = whale_follow_alerts.build_candidates([_trade(address="0xa")], now=NOW, recent_trades=recent, per_wallet_hourly_limit=3)
    assert result["candidates"] == []
    assert any(item.get("cap") == "per_wallet_hourly" for item in result["blocked"])


def test_hourly_cap_ignores_trades_outside_the_window() -> None:
    recent = [_trade(address="0xa", entry_at=NOW - timedelta(hours=5)) for _ in range(9)]
    result = whale_follow_alerts.build_candidates([_trade(address="0xa")], now=NOW, recent_trades=recent, per_wallet_hourly_limit=3)
    assert len(result["candidates"]) == 1


def test_only_opened_and_closed_are_sendable() -> None:
    assert whale_follow_alerts.SENDABLE_PHASES == ("opened", "closed")
    result = whale_follow_alerts.build_candidates([_trade(status="partial")], now=NOW)
    assert result["candidates"] == []
    assert "발송 대상 단계가 아니다" in result["blocked"][0]["reason"]


def test_message_carries_every_required_field() -> None:
    """C8 — 하나라도 빠지면 미검증 신호가 검증된 것처럼 읽힌다."""
    body = whale_follow_alerts.format_message(_trade(), phase="opened")
    assert "미검증 관찰 자격 진입" in body
    assert "N=38" in body and "CI 하한" in body
    assert "unclassified 추정" in body
    assert "무효화" in body and "진입" in body
    assert "체결→진입" in body
    assert "실주문이 아닌" in body


def test_promotion_entry_is_labelled_differently() -> None:
    body = whale_follow_alerts.format_message(_trade(qualification="promotion"), phase="opened")
    assert "승격 고래 추종 진입" in body
    assert "미검증" not in body


def test_alerts_do_not_bypass_the_gate() -> None:
    """6-3 항목 1 — grep 증명. 알림 모듈에 발송기가 없다."""
    source = (REPO_ROOT / "backend/app/notify/whale_follow_alerts.py").read_text(encoding="utf-8")
    for bypass in ("TelegramSender", "send_telegram", "httpx", "requests"):
        assert bypass not in source, f"관문 우회 경로가 있다: {bypass}"


# ── 6-4 자격별 분리 집계 ───────────────────────────────────────────────


def test_performance_is_split_by_qualification() -> None:
    """C11 — 문턱이 다르므로 섞으면 둘 다 해석 불가가 된다."""
    trades = [_trade(qualification="observation"), _trade(qualification="promotion")]
    buckets = whale_follow.performance_by_qualification(trades)["buckets"]
    assert set(buckets) == {"observation", "promotion"}
    for bucket in buckets.values():
        assert "승격" in bucket["not_promotion_evidence"]


def test_latency_reports_no_samples_honestly() -> None:
    buckets = whale_follow.performance_by_qualification([])["buckets"]
    assert buckets == {}


# ── 제약 증명 ──────────────────────────────────────────────────────────


def test_policy_and_direction_layers_are_untouched() -> None:
    """C5·C6 — 사이징·잠금·출구·방향 판정 diff 0줄."""
    diff = subprocess.run(["git", "diff", "origin/main", "--stat", "--", *UNTOUCHABLE], cwd=REPO_ROOT, capture_output=True, text=True, check=False)
    if diff.returncode != 0:
        pytest.skip("origin/main 을 참조할 수 없는 환경")
    assert diff.stdout.strip() == "", f"C5·C6 위반:\n{diff.stdout}"


def test_promotion_thresholds_are_unchanged() -> None:
    """C1 — 관찰 자격을 신설했을 뿐이다. 28일·N>=30·CI 하한 55% 그대로."""
    from app.backtest import candidate_scoring

    assert candidate_scoring.WHALE_VALIDATION_DAYS == 28
    source = (REPO_ROOT / "backend/app/onchain/service.py").read_text(encoding="utf-8")
    assert "sample_size < 30 or ci_low is None or ci_low < 55.0" in source


def test_no_real_order_path_was_added() -> None:
    """C2 — 봉인. 추종 트랙은 페이퍼다."""
    source = (REPO_ROOT / "backend/app/paper/whale_follow.py").read_text(encoding="utf-8")
    for forbidden in ("place_order", "create_order", "submit_order", "bitget"):
        assert forbidden not in source.lower()


def test_hourly_cap_does_not_count_the_trade_being_evaluated() -> None:
    """호출부가 원장 전체를 넘긴다 — 그러면 이 거래가 자기 자신을 세어 상한이 1 작아진다."""
    trade = _trade(address="0xa", entry_at=NOW - timedelta(minutes=1))
    prior = [_trade(address="0xa", entry_at=NOW - timedelta(minutes=10)) for _ in range(2)]
    # 원장 전체 = 이전 2건 + 지금 것. 상한 3 이면 이번 건은 통과해야 한다.
    result = whale_follow_alerts.build_candidates([trade], now=NOW, recent_trades=[*prior, trade], per_wallet_hourly_limit=3)
    assert len(result["candidates"]) == 1, "평가 중인 거래를 기준선에 넣어 상한이 조기 발동했다"


def test_status_carries_participant_confidence() -> None:
    """6-3 항목 4 — 본문에 '유형 추정과 신뢰도'가 필요하다. 자격 판정이 실어 날라야 한다."""
    status = _status(estimate={"participant_type": participant_type.TYPE_DIRECTIONAL, "confidence": 0.87})
    assert status.participant_confidence == 0.87
    assert status.as_payload()["participant_confidence"] == 0.87


def test_message_prints_confidence_when_known() -> None:
    trade = whale_follow.open_follow_trade(
        _candidate(signal={**_candidate()["signal"], "participant_confidence": 0.64}),
        now=NOW,
    )
    assert "신뢰 0.64" in whale_follow_alerts.format_message(trade, phase="opened")


def test_follow_engine_runs_on_the_isolated_executor() -> None:
    """C9 — 조회 상한만으로는 슬롯 점유를 막지 못한다. 격리도 함께 건다."""
    from app.worker.manager import _HEAVY_JOBS

    assert "whale_follow_engine" in _HEAVY_JOBS
