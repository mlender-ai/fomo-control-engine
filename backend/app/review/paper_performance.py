"""페이퍼 트랙 성과 집계 — 원장 단일 경로 (WO-FCE-PERFORMANCE-REPORT-01).

역할 분리: **생존 라인은 "살아있음"을, 이 리포트는 "무엇을 하고 있음"을 보고한다.**
`worker/liveness.py::daily_liveness_lines` 가 전자, 이 모듈이 후자다.

원칙:
- **원장 단일 경로**(작업 3): 크립토는 `paper_trades`, 주식은 `stock_paper_*`, 폴리는
  `poly_positions`/`poly_resolutions` 를 직접 읽는다. 별도 집계 테이블을 만들지 않는다.
- **표본 정직성**(C3): 표본 규칙을 **계산부에 내장**한다. 표본 0이면 승률·평균에 `None`
  을 넣어 표시부가 실수로 숫자를 만들 수 없게 한다.
- **트랙 분리**(C4): 크립토·주식 KR·주식 US·폴리를 단일 숫자로 합치지 않는다. 실계좌
  성과(`app/performance/metrics.py`)와도 섞지 않는다.
"""

from __future__ import annotations

from datetime import datetime, timezone
from statistics import mean
from typing import Any

# C3: 4주 검증의 판정 최소 표본. 이 아래는 승률을 계산해도 "판정 유보"로 표기한다.
SAMPLE_MIN_N = 30

SAMPLE_NONE = "none"
SAMPLE_INSUFFICIENT = "insufficient"
SAMPLE_SUFFICIENT = "sufficient"

# 진입이 실제로 성립하지 않은 원장 행. 승률 분모에 넣으면 없는 패배를 만든다.
NON_TRADE_EXIT_REASONS = frozenset({"duplicate_bootstrap_suppressed"})


def sample_state(n: int) -> str:
    """표본 크기를 세 상태로 분류한다 — 표시부는 이 값만 보고 문장을 고른다."""
    if n <= 0:
        return SAMPLE_NONE
    if n < SAMPLE_MIN_N:
        return SAMPLE_INSUFFICIENT
    return SAMPLE_SUFFICIENT


def closed_metrics(r_values: list[float]) -> dict[str, Any]:
    """청산 완료 표본의 승률·평균 R.

    정의(작업 3):
      - 승률 = 청산 완료 중 R>0 비율 (미청산 제외)
      - 평균 R = 청산 완료의 R 평균

    **표본 0이면 승률·평균에 None 을 넣는다**(C3). 표시부에서 0% 같은 숫자를 만들 수
    없게 하기 위해 계산부에서 못을 박는다.
    """
    n = len(r_values)
    if n == 0:
        return {"n": 0, "state": SAMPLE_NONE, "wins": 0, "win_rate_pct": None, "avg_r": None}
    wins = sum(1 for value in r_values if value > 0)
    return {
        "n": n,
        "state": sample_state(n),
        "wins": wins,
        "win_rate_pct": round(wins / n * 100.0, 1),
        "avg_r": round(mean(r_values), 3),
    }


def realized_r(net_pnl: float, quantity: float, entry_price: float, invalidation_price: float) -> float | None:
    """실현 R = 실현 손익 / 최초 무효화 거리 기준 리스크.

    **종가만으로 R 을 계산하면 안 된다**: 부분청산이 있으면 최종 청산가가 진입가보다
    불리해도 실현 손익은 양수일 수 있다(2026-07-30 실측: HYPEUSDT 롱 진입 58.623 →
    최종 청산 57.94 인데 실현 +1.19 — 부분청산 이익). 원장의 실현 손익이 진실이다.

    분모는 `stop_price` 가 아니라 `invalidation_price` 다 — stop 은 본전으로 끌어올려져
    진입가와 같아질 수 있어(breakeven_stop) 분모가 0이 된다. 최초 리스크는 무효화가다.
    """
    risk = abs(entry_price - invalidation_price) * abs(quantity)
    if risk <= 0:
        return None
    return net_pnl / risk


def _clock(source: dict[str, Any]) -> dict[str, Any]:
    """검증 경과일 — 유실일 제외 기준(선행 WO). 달력일·유실일을 함께 싣는다."""
    return {
        "elapsed_days": int(source.get("elapsed_days") or 0),
        "calendar_days": int(source.get("calendar_days") or 0),
        "lost_days": int(source.get("lost_days") or 0),
    }


def crypto_track(trades: list[Any]) -> dict[str, Any]:
    """크립토 트랙 — `paper_trades` 원장.

    크립토에는 주식·폴리와 달리 **검증 시계가 없다**(유실일 제외 경과일의 원천이
    존재하지 않는다). 없는 값을 만들지 않고 ``clock: None`` 으로 둔다.
    """
    closed = [trade for trade in trades if str(getattr(trade, "status", "")) == "closed"]
    traded = [trade for trade in closed if str(getattr(trade, "exit_reason", "") or "") not in NON_TRADE_EXIT_REASONS]
    r_values: list[float] = []
    r_undefined = 0
    for trade in traded:
        value = realized_r(
            float(trade.net_pnl_usdt),
            float(trade.quantity),
            float(trade.entry_price),
            float(trade.invalidation_price),
        )
        if value is None:
            r_undefined += 1
            continue
        r_values.append(value)
    return {
        "key": "crypto",
        "label": "크립토",
        "currency": "USDT",
        "clock": None,
        "holding": {"count": sum(1 for trade in trades if str(getattr(trade, "status", "")) == "open")},
        "closed": closed_metrics(r_values),
        "excluded": {"non_trade": len(closed) - len(traded), "r_undefined": r_undefined},
        "today": {},
    }


def stock_tracks(dashboard: dict[str, Any]) -> list[dict[str, Any]]:
    """주식 트랙 — **KR·US 분리**(작업 3). 시장별 세션·유니버스·통화가 다르다.

    청산 완료 표본은 매도 체결(`side='sell'`)이다. 매수만 있으면 청산 표본은 0이며
    승률은 `None` 이 된다 — 미실현 손익을 승률로 환산하지 않는다(C3).
    """
    tracks = [row for row in (dashboard.get("tracks") or []) if isinstance(row, dict)]
    positions = [row for row in (dashboard.get("positions") or []) if isinstance(row, dict)]
    rejections = dashboard.get("entry_rejection_distribution") or {}
    gates = [row for row in (rejections.get("gates") or []) if isinstance(row, dict)]
    results: list[dict[str, Any]] = []
    for track in sorted(tracks, key=lambda row: str(row.get("market") or "")):
        market = str(track.get("market") or "")
        held = [row for row in positions if str(row.get("market") or "") == market]
        market_gates = [row for row in gates if str(row.get("market") or "") == market]
        top_gate = max(market_gates, key=lambda row: int(row.get("count") or 0)) if market_gates else None
        results.append(
            {
                "key": f"stock_{market.lower()}",
                "label": f"주식 {market}",
                "currency": str(track.get("currency") or ""),
                "clock": _clock(track),
                "holding": {
                    "count": len(held),
                    "unrealized_pct": _unrealized_pct(held),
                },
                # 매도 체결이 없으면 청산 표본 0 — closed_metrics 가 None 을 강제한다.
                "closed": closed_metrics(_stock_realized_r(dashboard, market)),
                "excluded": {},
                "today": {
                    "top_reject_gate": str(top_gate.get("gate")) if top_gate else None,
                    "reject_total": sum(int(row.get("count") or 0) for row in market_gates),
                    "reject_period_days": int(rejections.get("period_days") or 0),
                },
                "benchmark": {
                    "start": track.get("benchmark_start"),
                    "current": track.get("benchmark_current"),
                    "symbol": track.get("benchmark_proxy_symbol") or None,
                },
            }
        )
    return results


def _stock_realized_r(dashboard: dict[str, Any], market: str) -> list[float]:
    """주식 청산 완료의 R 목록. 매도 체결 원장에 실현 R 이 실릴 때까지는 빈 목록이다.

    비어 있음은 "승률 0%" 가 아니라 "표본 없음" 이다(C3) — 호출부의 closed_metrics 가
    None 을 넣는다.
    """
    realized: list[float] = []
    for fill in dashboard.get("recent_fills") or []:
        if not isinstance(fill, dict) or str(fill.get("market") or "") != market:
            continue
        if str(fill.get("side") or "") != "sell":
            continue
        value = fill.get("realized_r")
        if value is None:
            continue
        try:
            realized.append(float(value))
        except (TypeError, ValueError):
            continue
    return realized


def _unrealized_pct(positions: list[dict[str, Any]]) -> float | None:
    """보유 포지션의 미실현 수익률(원가 가중). 마크가 없으면 만들지 않는다."""
    cost = 0.0
    value = 0.0
    for row in positions:
        try:
            quantity = float(row.get("quantity") or 0)
            average = float(row.get("average_price") or 0)
            current = row.get("current_price")
            if current is None or quantity <= 0 or average <= 0:
                continue
            cost += average * quantity
            value += float(current) * quantity
        except (TypeError, ValueError):
            continue
    if cost <= 0:
        return None
    return round((value - cost) / cost * 100.0, 2)


def poly_track(dashboard: dict[str, Any]) -> dict[str, Any]:
    """폴리 트랙 — 대표 지표는 **브라이어 평균**(WO-POLY-PAPER-01 원칙).

    단 브라이어는 두 모집단을 구분해 싣는다(C3):
      - ``settled_positions``: 실제 정산된 **포지션** 기준. 대표 지표.
      - ``estimates``: 추정 해소 전체 기준. 2026-07-30 실측에서 1,789건 중 1,774건
        (99.2%)이 이미 0%/100% 로 확정된 자명한 시장이라 평균 0.0005 가 나왔다.
        이 숫자를 대표로 쓰면 예측력이 완벽하다는 거짓 인상을 준다 — 자명 시장 건수를
        함께 싣고 대표 자리에 두지 않는다.
    """
    track = dashboard.get("track") or {}
    positions = [row for row in (dashboard.get("positions") or []) if isinstance(row, dict)]
    settled = [row for row in positions if str(row.get("status") or "") == "resolved"]
    calibration = dashboard.get("calibration") or {}
    brier_values = [float(row["brier_score"]) for row in settled if row.get("brier_score") is not None]
    return {
        "key": "poly",
        "label": "폴리",
        "currency": "USDC",
        "clock": _clock(track),
        "holding": {"count": sum(1 for row in positions if str(row.get("status") or "") == "open")},
        "closed": closed_metrics([float(row["pnl"]) for row in settled if row.get("pnl") is not None]),
        "excluded": {},
        "today": {},
        "brier": {
            "settled_positions": {
                "n": len(brier_values),
                "state": sample_state(len(brier_values)),
                "avg": round(mean(brier_values), 4) if brier_values else None,
            },
            "estimates": _estimate_brier(calibration, int(dashboard.get("resolution_count") or 0)),
        },
    }


def _estimate_brier(calibration: dict[str, Any], resolution_count: int) -> dict[str, Any]:
    """추정 해소 기준 브라이어 — 자명 시장(0~10%, 90~100% 버킷) 비중을 함께 밝힌다."""
    curve = [row for row in (calibration.get("curve") or []) if isinstance(row, dict)]
    trivial = 0
    for row in curve:
        label = str(row.get("bucket") or "")
        if label.startswith("0–10") or label.startswith("90–100"):
            trivial += int(row.get("n") or 0)
    mean_brier = calibration.get("mean_brier_score")
    return {
        "n": resolution_count,
        "state": sample_state(resolution_count),
        "avg": round(float(mean_brier), 4) if mean_brier is not None else None,
        "trivial_n": trivial,
        "trivial_share_pct": round(trivial / resolution_count * 100.0, 1) if resolution_count else None,
    }


def paper_performance_report(
    *,
    crypto_trades: list[Any],
    stock_dashboard: dict[str, Any],
    poly_dashboard: dict[str, Any],
    now: datetime | None = None,
) -> dict[str, Any]:
    """4트랙(크립토·주식 KR·주식 US·폴리) 성과 리포트.

    **진입이 0인 트랙도 반드시 포함된다**(작업 4) — 이벤트가 없어서 침묵하는 상태를
    구조적으로 제거하는 것이 이 리포트의 목적이다.
    """
    moment = now or datetime.now(timezone.utc)
    tracks = [crypto_track(crypto_trades), *stock_tracks(stock_dashboard), poly_track(poly_dashboard)]
    for track in tracks:
        clock = track.get("clock") or {}
        track["projection"] = _projected_sample(int(track["closed"]["n"]), int(clock.get("elapsed_days") or 0))
    return {"as_of": moment.isoformat(), "sample_min_n": SAMPLE_MIN_N, "tracks": tracks}


def _projected_sample(closed_n: int, elapsed_days: int, horizon_days: int = 28) -> dict[str, Any]:
    """4주 시점 예상 표본과 N>=30 도달 가능 여부(주간 리포트 §2-2).

    청산 속도를 선형 외삽한다. 경과일이 0이면 속도를 알 수 없으므로 판정하지 않는다 —
    표본이 없을 때 도달 가능성을 단정하면 그 자체가 없는 숫자를 만드는 것이다.
    """
    if elapsed_days <= 0:
        return {"per_day": None, "projected_n": None, "reaches_min": None, "horizon_days": horizon_days}
    per_day = closed_n / elapsed_days
    projected = int(per_day * horizon_days)
    return {
        "per_day": round(per_day, 2),
        "projected_n": projected,
        "reaches_min": projected >= SAMPLE_MIN_N,
        "horizon_days": horizon_days,
    }
