"""[백엔드(API·비용)] ⑦ 비용 계산 로직 — Price List API/가격 스냅샷, 월 비용, Free Tier·크레딧 소진 판별.

입력: `archimate.schemas.Design` 의 노드별 `usage` (LLM이 단가 없이 사용량만 출력).
"""

from archimate.schemas import Design, MonthlyCost


def estimate_monthly_cost(design: Design) -> MonthlyCost | None:
    """1안의 월 비용. 통화·응답 형식(Issue #10)이 확정되기 전까지는 None(= monthlyCost: null)."""
    return None


__all__ = ["estimate_monthly_cost"]
