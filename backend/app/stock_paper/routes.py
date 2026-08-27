from typing import Any

from fastapi import APIRouter

from app.core.config import get_settings
from app.validation import provisional_defaults

from .models import Market
from .service import run_stock_paper_engine, stock_paper_dashboard, stock_paper_entry_chart, universe_payload


router = APIRouter(prefix="/api/stock-paper", tags=["stock-paper"])


@router.get("/dashboard")
def dashboard() -> dict:
    """대시보드 + 트랙 자본 (WO-FCE-TRACK-CAPITAL-01 1-3).

    자본은 계산 계층에서 붙인다 — 스토어가 이미 NAV 를 내지만 그것은 트랙별 형식이고,
    4트랙이 같은 필드로 읽히려면 공통 모듈이 산출해야 한다.
    """
    from app.validation.track_capital import capital_for_response

    settings = get_settings()
    return {
        **stock_paper_dashboard(settings),
        "capital": capital_for_response(settings, ("stock_kr", "stock_us")),
        # 4-3: 절전은 코드로 풀 수 없다. 화면이 상시 경고하고 명령을 복사 가능하게 낸다.
        "host_persistence": _host_persistence_warning(settings),
        # C5: 지금 적용 중인 임시값을 이 탭에서도 볼 수 있어야 한다.
        "provisional_defaults": provisional_defaults.summary(settings),
    }


def _host_persistence_warning(settings: Any) -> dict[str, Any]:
    """유실일 → 유효일 상한과 조치 명령 (WO-FCE-DEFAULTS-01 4-3).

    **유실일을 분모에서 빼지 않는다**(C3). 관측하지 않은 날을 관측했다고 하는 것이기 때문이다.
    대신 그 유실이 검증 가능성에 무엇을 하는지 정량으로 낸다.
    """
    from app.stock_paper.store import StockPaperStore
    from app.validation import pending_decisions

    try:
        tracks = StockPaperStore(str(settings.database_url)).dashboard().get("tracks") or []
    except Exception:
        tracks = []
    ceilings = {
        str(track.get("market")): pending_decisions.effective_day_ceiling(
            calendar_days=int(track.get("calendar_days") or 0),
            lost_days=int(track.get("lost_days") or 0),
        )
        for track in tracks
        if track.get("market")
    }
    unreachable = {market: row for market, row in ceilings.items() if not row["reachable"]}
    return {
        "blocking": bool(unreachable),
        "headline": "호스트 절전으로 28일 창 도달 불가" if unreachable else "관측 지속성 정상",
        "ceilings": ceilings,
        "command": "caffeinate -dimsu &",
        "verify_command": "bash scripts/local/check-sleep-guard.sh",
        "cannot_be_fixed_in_code": True,
        "note": (
            "유실일을 유효일 분모에서 빼지 않는다 — 관측하지 않은 날을 관측했다고 하는 것이다. 이 한 줄을 실행하지 않으면 주식 트랙 검증은 성립하지 않는다."
        ),
        "document": "docs/validation/HOST_PERSISTENCE.md",
    }


@router.get("/universe")
def universe() -> dict:
    return universe_payload()


@router.get("/entry-chart")
def entry_chart(market: Market, symbol: str) -> dict:
    return stock_paper_entry_chart(get_settings(), market, symbol)


@router.post("/run")
def run_once() -> dict:
    return run_stock_paper_engine(get_settings())
