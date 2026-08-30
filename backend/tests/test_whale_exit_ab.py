"""WO-FCE-WHALE-EXIT-REPLAY-01 Phase 2 — 출구 A/B · 잠금 누수 · basis_carry.

이 파일이 고정하는 명제:

1. **잠금이 자기 트랙 원장을 읽는다** (2-6) — 크립토 청산이 추종 진입을 막지 않는다
2. **잠금 규칙은 그대로다** — 바뀐 것은 읽는 원장 하나
3. **`basis_carry` 는 배제된다** (2-7) — 델타 중립은 방향 베팅이 아니다
4. **A/B 대조가 매칭률·부분전량·보유시간을 낸다** (2-1·2-2)
5. **22%p 갭 판정이 인과를 단정하지 않는다** (C6)
6. **출구 B 는 공식 표본이 아니다** (C2)
"""

from __future__ import annotations

import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from app.onchain import follow_eligibility as fe
from app.onchain import participant_type
from app.paper import policy as paper_policy
from app.paper import service as paper_service
from app.paper import whale_exit_replay as wer

REPO_ROOT = Path(__file__).resolve().parents[2]
NOW = datetime(2026, 8, 30, 12, 0, tzinfo=timezone.utc)


class _Row:
    """잠금 판정이 보는 표면만 흉내낸다."""

    def __init__(self, *, symbol: str, exit_at: datetime, direction) -> None:
        self.symbol = symbol
        self.timeframe = "4h"
        self.exit_bar_at = exit_at
        self.exit_at = exit_at
        self.direction = direction


class _TwoLedgerRepo:
    """크립토와 추종이 **다른 청산 이력**을 가진 저장소."""

    def __init__(self, *, crypto: list, follow: list) -> None:
        self._crypto = crypto
        self._follow = follow

    def list_paper_trades(self, status=None, symbol=None, limit=50):
        return [row for row in self._crypto if symbol is None or row.symbol == symbol]

    def list_whale_follow_trades(self, status=None, symbol=None, limit=50):
        return [row for row in self._follow if symbol is None or row.symbol == symbol]


# ── 2-6 재진입 잠금 원장 누수 ───────────────────────────────────────────


def _policy() -> paper_policy.PaperPolicy:
    return paper_policy.PaperPolicy(margin_usdt=100.0, leverage=5.0, reentry_lock_mode="bars", reentry_lock_bars=3)


def _bar():
    from app.db.models import MarketCandle

    return MarketCandle(timestamp=NOW, open=100.0, high=101.0, low=99.0, close=100.0, volume=1.0)


def _block(repo, ledger: str):
    from app.db.models import Direction

    return paper_service._reentry_block_reason(repo, symbol="BTCUSDT", timeframe="4h", bar=_bar(), direction=Direction.long, policy=_policy(), ledger=ledger)


def test_crypto_exit_no_longer_locks_the_follow_track() -> None:
    """**누수의 실체.** 원장은 갈라놨는데 잠금만 공유했다.

    크립토가 BTCUSDT 를 방금 청산했고 추종 트랙에는 청산 이력이 없다. 이전에는 추종 진입이
    막혔다 — 추종 표본이 크립토 활동에 종속됐다는 뜻이다.
    """
    from app.db.models import Direction

    repo = _TwoLedgerRepo(crypto=[_Row(symbol="BTCUSDT", exit_at=NOW - timedelta(hours=1), direction=Direction.long)], follow=[])

    assert _block(repo, paper_service.LEDGER_CRYPTO) is not None, "크립토 잠금이 걸려야 이 회귀가 의미가 있다"
    assert _block(repo, paper_service.LEDGER_WHALE_FOLLOW) is None, "크립토 청산이 추종 진입을 막고 있다"


def test_follow_exit_still_locks_the_follow_track() -> None:
    """누수를 막는 것이지 잠금을 끄는 것이 아니다."""
    from app.db.models import Direction

    repo = _TwoLedgerRepo(crypto=[], follow=[_Row(symbol="BTCUSDT", exit_at=NOW - timedelta(hours=1), direction=Direction.long)])
    assert _block(repo, paper_service.LEDGER_WHALE_FOLLOW) is not None


def test_crypto_lock_behaviour_is_unchanged() -> None:
    """2-6 수용 기준 — 크립토 트랙 잠금 동작 무변경. 기본값이 크립토다."""
    from app.db.models import Direction

    repo = _TwoLedgerRepo(crypto=[_Row(symbol="BTCUSDT", exit_at=NOW - timedelta(hours=1), direction=Direction.long)], follow=[])
    explicit = _block(repo, paper_service.LEDGER_CRYPTO)
    default = paper_service._reentry_block_reason(repo, symbol="BTCUSDT", timeframe="4h", bar=_bar(), direction=Direction.long, policy=_policy())
    assert default == explicit, "기본값이 크립토가 아니다 — 기존 호출부의 동작이 바뀐다"


def test_follow_track_asks_for_its_own_ledger() -> None:
    source = (REPO_ROOT / "backend/app/paper/whale_follow.py").read_text(encoding="utf-8")
    block = source.split("def evaluate_signal")[1].split("\ndef ")[0]
    assert "ledger=paper_service.LEDGER_WHALE_FOLLOW" in block


def test_unknown_ledger_does_not_silently_fall_back_to_crypto() -> None:
    """없는 원장을 크립토로 대신 읽으면 누수가 조용히 되살아난다."""

    class _OnlyCrypto:
        def list_paper_trades(self, status=None, symbol=None, limit=50):
            raise AssertionError("추종 원장을 못 찾자 크립토를 읽었다")

    assert paper_service._closed_trades(_OnlyCrypto(), ledger=paper_service.LEDGER_WHALE_FOLLOW, symbol="BTCUSDT") == []


def test_lock_survives_rows_without_an_exit_stamp() -> None:
    """저장소가 `status` 를 무시하고 열린 거래를 돌려줘도 잠금 판정이 죽으면 안 된다.

    죽으면 진입이 `error` 로 거부되고, 그 사유는 "잠금에 걸렸다"와 구분되지 않는다.
    """

    class _Open:
        symbol = "BTCUSDT"
        timeframe = "4h"

    class _Repo:
        def list_whale_follow_trades(self, status=None, symbol=None, limit=50):
            return [_Open()]

    assert _block(_Repo(), paper_service.LEDGER_WHALE_FOLLOW) is None


# ── 2-7 basis_carry 배제 ────────────────────────────────────────────────


def _status(kind: str):
    return fe.follow_status(address="0xa", sample_size=99, wins=99, estimate={"participant_type": kind})


def test_basis_carry_is_excluded() -> None:
    """델타 중립이다 — 퍼프 다리만 따라가면 헤지를 방향 베팅으로 오독한다."""
    assert _status(participant_type.TYPE_BASIS_CARRY).eligible is False
    assert "델타 중립" in _status(participant_type.TYPE_BASIS_CARRY).reason


def test_directional_and_unclassified_still_pass() -> None:
    """배제가 넓어졌지 자격이 좁아진 것이 아니다 — 모르는 것을 배제하면 영원히 모른다."""
    assert _status(participant_type.TYPE_DIRECTIONAL).eligible is True
    assert _status(participant_type.TYPE_UNCLASSIFIED).eligible is True


def test_exclusion_reason_is_queryable_per_type() -> None:
    """2-7 항목 4 — 하나로 뭉뚱그리면 "왜 이 지갑이 빠졌나"를 화면에서 답할 수 없다."""
    assert set(fe.EXCLUSION_REASONS) == fe.FOLLOW_EXCLUDED_TYPES
    funnel = fe.funnel({kind: _status(kind) for kind in (participant_type.TYPE_MARKET_MAKER, participant_type.TYPE_BASIS_CARRY)})
    assert funnel["excluded_by_type"] == {"basis_carry": 1, "market_maker": 1}


def test_thresholds_did_not_move() -> None:
    """C3 — 자격 임계 diff 0줄. 유형 필터만 넓혔다."""
    assert fe.FOLLOW_MIN_SAMPLE == 30
    assert fe.FOLLOW_MIN_WIN_PCT == 55.0


# ── 2-1 · 2-2 대조 ──────────────────────────────────────────────────────


def _trade(*, net: float, exit_at: datetime | None, entry_at: datetime, entry_type: str = "solo") -> dict:
    return {
        "id": "t1",
        "symbol": "BTCUSDT",
        "direction": "long",
        "whale_address": "0xa",
        "net_pnl_usdt": net,
        "entry_at": entry_at,
        "exit_at": exit_at,
        "exit_reason": "take_profit",
        "entry_price": 100.0,
        "quantity": 1.0,
        "entry_type": entry_type,
    }


def _exit(*, at: datetime, price: float, kind: str = "close") -> wer.WhaleExit:
    return wer.WhaleExit(at=at, price=price, kind=kind, size_usd=1000.0)


def test_partial_and_full_exits_are_distinguished() -> None:
    """2-1 항목 3 — `reduce` 를 전량으로 읽으면 "고래가 나갔다"가 거짓이 된다."""
    entry = NOW - timedelta(hours=5)
    full = wer.compare_trade(_trade(net=1.0, exit_at=NOW, entry_at=entry), _exit(at=NOW - timedelta(hours=1), price=105.0), cost_rate=0.0006)
    partial = wer.compare_trade(_trade(net=1.0, exit_at=NOW, entry_at=entry), _exit(at=NOW - timedelta(hours=1), price=105.0, kind="reduce"), cost_rate=0.0006)
    assert full["exit_b_full"] is True
    assert partial["exit_b_full"] is False


def test_hold_time_is_compared_not_just_money() -> None:
    """2-2 항목 2 — "덜 잃었다"와 "빨리 나왔다"는 다른 처방이다."""
    entry = NOW - timedelta(hours=8)
    row = wer.compare_trade(_trade(net=1.0, exit_at=NOW, entry_at=entry), _exit(at=NOW - timedelta(hours=6), price=105.0), cost_rate=0.0006)
    assert row["hold_a_hours"] == pytest.approx(8.0)
    assert row["hold_b_hours"] == pytest.approx(2.0)


def test_match_rate_and_exit_kinds_are_reported() -> None:
    """2-1 항목 2 — 매칭률이 낮으면 대조 자체가 표본 부족이고 그 사실이 먼저 보여야 한다."""
    entry = NOW - timedelta(hours=5)
    matched = wer.compare_trade(_trade(net=1.0, exit_at=NOW, entry_at=entry), _exit(at=NOW - timedelta(hours=1), price=105.0), cost_rate=0.0006)
    unmatched = wer.compare_trade(_trade(net=-1.0, exit_at=NOW, entry_at=entry), None, cost_rate=0.0006)

    summary = wer.summarize([matched, unmatched])
    assert summary["matched"] == 1
    assert summary["match_rate_pct"] == 50.0
    assert summary["whale_exit_kind"] == {"close": 1, "reduce": 0}
    assert summary["hold_hours"]["a_median"] is not None


def test_entry_type_crosses_with_ab() -> None:
    """2-8 항목 2 — 유형별과 A/B 를 따로 보면 "어느 유형에서 출구가 문제인가"를 못 본다."""
    entry = NOW - timedelta(hours=5)
    herd = wer.compare_trade(_trade(net=-2.0, exit_at=NOW, entry_at=entry, entry_type="herd"), _exit(at=NOW - timedelta(hours=1), price=110.0), cost_rate=0.0)
    solo = wer.compare_trade(_trade(net=1.0, exit_at=NOW, entry_at=entry, entry_type="solo"), _exit(at=NOW - timedelta(hours=1), price=99.0), cost_rate=0.0)
    by_type = wer.summarize([herd, solo])["by_entry_type"]

    assert set(by_type) == {"herd", "solo"}
    # 무리 진입에서는 고래 청산이 나았고, 단독에서는 우리 출구가 나았다.
    assert by_type["herd"]["delta_net"] > 0
    assert by_type["solo"]["delta_net"] < 0


def test_missing_entry_type_is_unclassified_not_invented() -> None:
    entry = NOW - timedelta(hours=5)
    row = wer.compare_trade({**_trade(net=1.0, exit_at=NOW, entry_at=entry), "entry_type": None}, _exit(at=NOW, price=105.0), cost_rate=0.0)
    assert set(wer.summarize([row])["by_entry_type"]) == {"unclassified"}


# ── 2-2 항목 4 · 22%p 갭 판정 ───────────────────────────────────────────


def _summary(count: int, delta: float) -> dict:
    entry = NOW - timedelta(hours=5)
    rows = []
    for index in range(count):
        rows.append(
            wer.compare_trade(
                {**_trade(net=0.0, exit_at=NOW, entry_at=entry), "id": f"t{index}"},
                _exit(at=NOW - timedelta(hours=1), price=100.0 + delta),
                cost_rate=0.0,
            )
        )
    return wer.summarize(rows)


def test_gap_verdict_names_three_hypotheses_and_asserts_none() -> None:
    """C6 — 인과를 단정하지 않는다. 셋이 동시에 참일 수 있다."""
    gap = wer.gap_verdict(summary=_summary(35, 1.0), whale_win_pct=56.6, follow_win_pct=34.3)

    assert gap["gap_pp"] == 22.3
    assert [row["id"] for row in gap["hypotheses"]] == ["exit", "friction", "scoring"]
    assert "인과를 단정하지 않는다" in gap["not_causal"]


def test_gap_verdict_refuses_to_judge_on_a_small_sample() -> None:
    """C8 — 부족한 표본으로 방향을 정하면 다음 WO 전체가 그 위에 얹힌다."""
    gap = wer.gap_verdict(summary=_summary(3, 1.0), whale_win_pct=56.6, follow_win_pct=34.3)

    assert gap["actionable"] is False
    exit_row = next(row for row in gap["hypotheses"] if row["id"] == "exit")
    assert exit_row["consistent"] is None


def test_scoring_hypothesis_is_always_partly_true() -> None:
    """**두 승률은 애초에 같은 것을 재지 않는다** — 갭의 일부는 정의상 존재한다."""
    gap = wer.gap_verdict(summary=_summary(35, 1.0), whale_win_pct=56.6, follow_win_pct=34.3)
    scoring = next(row for row in gap["hypotheses"] if row["id"] == "scoring")

    assert scoring["consistent"] is True
    assert "같은 것을 재지 않으므로" in scoring["note"]


def test_friction_is_undetermined_without_host_measurements() -> None:
    """지연·이탈 분포는 호스트 관측에서만 나온다. 없으면 없다고 쓴다(C8)."""
    gap = wer.gap_verdict(summary=_summary(35, 1.0), whale_win_pct=56.6, follow_win_pct=34.3, latency=None, drift=None)
    friction = next(row for row in gap["hypotheses"] if row["id"] == "friction")

    assert friction["consistent"] is None
    assert "호스트 관측" in friction["note"]


def test_friction_is_flagged_when_entries_crowd_the_caps() -> None:
    gap = wer.gap_verdict(summary=_summary(35, 1.0), whale_win_pct=56.6, follow_win_pct=34.3, latency={"median": 25.0}, drift={"median": 22.0})
    friction = next(row for row in gap["hypotheses"] if row["id"] == "friction")
    assert friction["consistent"] is True


# ── 제약 증명 ──────────────────────────────────────────────────────────


def test_exit_b_is_never_counted_as_official_sample() -> None:
    """C1·C2 — 반사실이다. 포지션을 두 배로 열지 않고 표본에 합산하지 않는다."""
    summary = _summary(5, 1.0)
    assert summary["official_sample"] == "exit_a"
    assert "합산하지 않는다" in summary["not_official"]

    source = (REPO_ROOT / "backend/app/paper/whale_exit_replay.py").read_text(encoding="utf-8")
    for forbidden in ("upsert_whale_follow_trade", "open_trade(", "upsert_paper_trade"):
        assert forbidden not in source, f"반사실 모듈이 원장을 쓰거나 포지션을 연다: {forbidden}"


def test_exit_a_logic_is_untouched() -> None:
    """C4 — 비교 대상이 바뀌면 비교가 무의미하다."""
    diff = subprocess.run(
        ["git", "diff", "origin/main", "--stat", "--", "backend/app/paper/policy.py", "backend/app/analyst", "backend/app/structure"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    if diff.returncode != 0:
        pytest.skip("origin/main 을 참조할 수 없는 환경")
    assert diff.stdout.strip() == "", f"C4·C5 위반:\n{diff.stdout}"


def test_run_exits_still_ignores_eligibility() -> None:
    """자격 상실은 신규 진입만 막는다 — 출구는 가격·시간이 정한다."""
    source = (REPO_ROOT / "backend/app/paper/whale_follow.py").read_text(encoding="utf-8")
    exits = source.split("def run_exits")[1].split("\ndef ")[0]
    assert "eligible" not in exits
