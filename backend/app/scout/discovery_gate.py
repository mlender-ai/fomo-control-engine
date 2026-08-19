"""WO-FCE-DISCOVERY-UNBLOCK-01 — 발견 게이트 술어.

이 모듈은 **의존이 없는 leaf** 다. `app/db/repository/` 가 술어를 그대로 쓰려면 그래야 한다 —
저장소 계층이 상위 모듈(`scout/universe.py` 는 `structure/`·`review/` 를 임포트한다)을
끌어오면 순환이 생긴다. `app/validation/window_anchor.py` 가 같은 이유로 분리돼 있고,
그 파일의 주석이 선례다.

술어를 SQL 과 파이썬 두 곳에 두지 않는 것도 같은 원칙이다 — 둘로 두면 하나는 반드시 어긋난다.
"""

from __future__ import annotations

from typing import Any


# WO-FCE-DISCOVERY-UNBLOCK-01 — 관측 등급 게이트.
#
# ## 발견 게이트는 두 가지 일을 겸하고 있었다
#
# `gate_passed` 는 원래 **알림 발송 자격**이다(`status="alerted"` · `daily_alert_limit` ·
# `symbol_cooldown` 이 같은 경로에 있다). 그런데 `paper_universe()` 가 같은 플래그로
# **페이퍼 엔진이 무엇을 평가할지**를 정한다. 두 목적이 한 술어에 얹혀 있다.
#
# 그 결과가 순환이다. `paper/policy.py` 는 이미 이 순환을 진입 조건에서 끊었다:
#
#     signature_gate_mode = "record_only"
#     # 검증 대상을 검증 조건으로 삼으면 표본이 영원히 생기지 않는다
#
# 진입은 시그니처 검증을 요구하지 않는데, **그 심볼을 보기까지가 시그니처 검증을 요구한다.**
# 진입 게이트에서 뺀 조건이 유니버스 급유 단계에 그대로 남아 있었다.
#
# ## 무엇을 미루고 무엇을 남기는가
#
# 아래 두 게이트만 **요구에서 기록으로** 옮긴다(C3). 값은 계속 산출·저장되며 판정에서만 빠진다.
# 임계값(`universe_backtest_min_sample` · `universe_backtest_min_ci_low_pct`)은 **건드리지 않는다**(C1).
#
# 나머지는 전부 그대로 요구한다(C2) — `liquidity_floor` · `confidence` ·
# `signature_lifecycle_state` · `live_backtest_divergence` · `earnings_window` ·
# `stage2_template`. 실측(2026-08-19 · 6시간 발견 534건)에서 이 비순환 게이트들이
# crypto 241건(유동성)·51건(신뢰도)을 계속 거른다. 주식·지수는 `stage2_template` 로
# 0건 통과다 — 그것은 실제 구조 조건이므로 손대지 않는다.
#
# ⚠️ **이것은 품질 검증이 아니다.** 관측 등급 통과는 "평가해 볼 자격"이며
# "성적이 좋다"가 아니다. 이 경로로 들어온 심볼의 거래는 별도 채점되어야 한다(C5).
OBSERVATION_DEFERRED_GATES = frozenset({"backtest_sample", "backtest_win_1r_ci_low"})

# 발송 자격 전용 게이트. 관측 등급 판정에서는 애초에 대상이 아니다.
_DISPATCH_ONLY_GATES = frozenset({"daily_alert_limit", "symbol_cooldown"})


def observation_gate_passed(reasons: list[dict[str, Any]] | None) -> bool:
    """페이퍼 엔진이 **평가해 볼** 자격이 있는가 (알림 자격과 다르다).

    표본 관련 두 게이트를 미루고, 나머지 비순환 게이트는 전부 요구한다.
    `gate_passed` 가 이미 True 면 당연히 True 다.

    **통과가 품질을 뜻하지 않는다** — 진입은 페이퍼 자신의 게이트 9종이 따로 판정하고,
    이 경로로 유입된 심볼의 성적은 분리 집계된다(C5·C10).
    """
    for reason in reasons or []:
        code = str(reason.get("code") or "")
        if code in OBSERVATION_DEFERRED_GATES or code in _DISPATCH_ONLY_GATES:
            continue
        if not reason.get("passed"):
            return False
    return True
