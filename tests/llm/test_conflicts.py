"""자유 서술과 선택 항목 충돌 찾기 (#5). 선택 항목이 우선이고, 충돌은 cons 첫 항목에 정해진 문장으로 알린다."""

import json

import pytest

from archimate.llm import build_requirements, generate_design, prompts
from archimate.llm.conflicts import check_conflict_notes, find_conflicts
from archimate.llm.providers.mock import MockProvider, load_fixture
from archimate.llm.settings import LLMSettings
from archimate.schemas import Tier


def req(free_text: str, **answers):
    data = {**json.loads(load_fixture("demo_answers.json")), **answers, "freeText": free_text}
    return build_requirements(data)


@pytest.mark.parametrize(
    ("text", "answers", "described"),
    [
        ("모집 첫날에는 동시 접속이 10만 명까지 몰려요", {}, ["10만 명"]),
        ("동시에 1,500명이 들어와요", {"concurrentUsers": "under_1k"}, ["1,500명"]),
        ("동시 접속 2.5만 명", {"concurrentUsers": "under_10k"}, ["2.5만 명"]),
        ("예산은 월 10만 원까지 돼요", {}, ["10만 원"]),
        ("예산 0원은 아니고 월 2만 원", {"monthlyBudget": 0}, ["2만 원"]),
        # 충돌 아님
        ("3명이 만드는 MVP예요. 사용자 1만 명이 목표예요", {"concurrentUsers": "under_1k"}, []),
        ("동시 접속 50명 정도예요", {}, []),
        ("예산은 월 5만원이에요", {}, []),
        ("예산 0원이에요", {"monthlyBudget": 0}, []),
        ("지원서는 3만 건 정도 받아요", {}, []),
    ],
)
def test_find_conflicts(text, answers, described):
    assert [c.described for c in find_conflicts(req(text, **answers))] == described


def test_note_copies_user_values_and_particle():
    (c,) = find_conflicts(req("동시 접속이 10만 명"))
    assert c.note.startswith("보충 설명의 '10만 명'과 선택한 '동시 사용자 100명 이하'가 달라")
    (c,) = find_conflicts(req("예산은 월 10만 원"))
    assert "'월 예산 50,000원'이 달라" in c.note


def test_check_conflict_notes():
    conflicts = find_conflicts(req("동시 접속이 10만 명"))
    assert check_conflict_notes(["장점이 많아요"], conflicts)[0].startswith("cons 첫 항목에")
    assert check_conflict_notes([conflicts[0].note], conflicts) == []
    assert check_conflict_notes(["아무 내용"], []) == []
    # 충돌이 없는데 안내 문장을 지어내면 빼게 한다 (도그푸딩 평가에서 실제로 나옴)
    invented = "보충 설명의 'AI 서비스'와 선택한 '동시 사용자 100명 이하'가 달라 선택 항목 기준으로 설계했어요."
    assert "충돌하지 않습니다" in check_conflict_notes([invented, "장점"], [])[0]


def test_prompt_has_conflicts_block_only_when_needed():
    assert "<conflicts>" in prompts.design_user_prompt(req("동시 접속이 10만 명"), Tier.BALANCED)
    assert "<conflicts>" not in prompts.design_user_prompt(req("동아리 사이트"), Tier.BALANCED)


async def test_design_without_conflict_note_is_retried():
    r = req("동시 접속이 10만 명")
    (c,) = find_conflicts(r)
    fixed = json.loads(load_fixture("design_minimal_cost.json"))
    fixed["cons"] = [c.note, *fixed["cons"]]
    provider = MockProvider(responses=[load_fixture("design_minimal_cost.json"), json.dumps(fixed)])
    design = await generate_design(
        r,
        Tier.MINIMAL_COST,
        provider=provider,
        settings=LLMSettings(_env_file=None, provider="mock"),
    )
    assert design.cons[0] == c.note
    assert "cons 첫 항목에 다음 문장을 넣으세요" in provider.calls[1][2].content
