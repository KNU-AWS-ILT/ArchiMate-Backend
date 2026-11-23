"""자유 서술과 선택 항목의 충돌 찾기 (#5).

선택 항목이 우선이고, 충돌하면 설계안 cons 첫 항목에 정해진 문장으로 알린다.
LLM에게 충돌을 스스로 찾게 하면 빠뜨리거나(9회 중 7회) "설명 값을 반영해 설계했다"고 잘못 쓰는 일이
있어서, 확실한 경우만 코드가 찾아 문장까지 만들어 주고 검증으로 확인한다.

찾는 것 (오탐을 피하려고 좁게 잡는다)
- 동시 사용자: '동시'·'접속' 바로 뒤의 'N명'이 선택한 범위의 최대치를 넘을 때
  ("3명이 만드는"(팀 인원), "사용자 1만 명 목표"(전체 사용자)는 동시 접속이 아니라 보지 않는다)
- 월 예산: '예산'·'월' 바로 뒤의 'N원'이 선택한 예산과 2배 이상 다를 때
"""

import re
from dataclasses import dataclass

from archimate.llm.interview import option_label
from archimate.schemas import Requirements
from archimate.schemas.requirements import UserScale

_UNIT = {"": 1, "천": 1_000, "만": 10_000, "억": 100_000_000}
_USERS = re.compile(r"(?:동시|접속)[^\d.,]{0,12}?(\d[\d,]*(?:\.\d+)?)\s*(천|만|억)?\s*명")
_BUDGET = re.compile(r"(?:예산|월)[^\d.,]{0,6}?(\d[\d,]*(?:\.\d+)?)\s*(천|만|억)?\s*원")

MAX_USERS: dict[UserScale, float] = {
    UserScale.UNDER_100: 100,
    UserScale.UNDER_1K: 1_000,
    UserScale.UNDER_10K: 10_000,
    UserScale.OVER_10K: float("inf"),
}
BUDGET_MISMATCH_RATIO = 2


@dataclass(frozen=True)
class Conflict:
    described: str  # 사용자가 쓴 표현 그대로, 예: '10만 명'
    chosen: str  # 선택 항목, 예: '동시 사용자 100명 이하'

    @property
    def note(self) -> str:
        """cons 첫 항목에 그대로 넣을 문장."""
        return (
            f"보충 설명의 '{self.described}'과 선택한 '{self.chosen}'{_i_ga(self.chosen)} 달라 "
            "선택 항목 기준으로 "
            f"설계했어요. {self.described}이 맞다면 인터뷰에서 그 값을 골라 다시 만들어 주세요."
        )


def _i_ga(word: str) -> str:
    """받침이 있으면 '이', 없으면 '가'."""
    last = word[-1]
    if "가" <= last <= "힣":
        return "이" if (ord(last) - ord("가")) % 28 else "가"
    return "이"


def _value(number: str, unit: str | None) -> float:
    return float(number.replace(",", "")) * _UNIT[unit or ""]


def _described(m: re.Match[str], suffix: str) -> str:
    return f"{m.group(1)}{m.group(2) or ''} {suffix}" if m.group(2) else f"{m.group(1)}{suffix}"


def find_conflicts(req: Requirements) -> list[Conflict]:
    text = req.free_text or ""
    conflicts: list[Conflict] = []

    users = [
        m for m in _USERS.finditer(text) if _value(m[1], m[2]) > MAX_USERS[req.concurrent_users]
    ]
    if users:
        m = max(users, key=lambda m: _value(m[1], m[2]))
        label = option_label("concurrentUsers", req.concurrent_users)
        conflicts.append(Conflict(described=_described(m, "명"), chosen=f"동시 사용자 {label}"))

    for m in _BUDGET.finditer(text):
        value, budget = _value(m[1], m[2]), req.monthly_budget
        if value == budget:
            continue
        if value >= budget * BUDGET_MISMATCH_RATIO or value * BUDGET_MISMATCH_RATIO <= budget:
            conflicts.append(
                Conflict(described=_described(m, "원"), chosen=f"월 예산 {budget:,}원")
            )
            break
    return conflicts


_NOTE_MARK = "선택 항목 기준으로 설계했어요"


def check_conflict_notes(cons: list[str], conflicts: list[Conflict]) -> list[str]:
    """찾은 충돌마다 cons에 그 값이 언급됐는지 확인한다 (문장이 조금 달라도 값이 있으면 통과).

    충돌이 없는데 안내 문장을 지어내면(예: 서비스 설명 전체를 '설명에 쓴 값'으로 넣음) 빼게 한다.
    """
    if not conflicts:
        return [
            f"보충 설명과 선택 항목은 충돌하지 않습니다. cons에서 '{item[:30]}…' 항목을 빼세요."
            for item in cons
            if _NOTE_MARK in item
        ]
    errors = []
    for c in conflicts:
        if not any(c.described in item and "선택" in item for item in cons):
            errors.append(f'cons 첫 항목에 다음 문장을 넣으세요: "{c.note}"')
    return errors
