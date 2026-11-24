"""예산 0원 안내 (#5). 요금이 생기는 서비스를 쓰면서 budgetNote가 0원을 단정하지 않게 한다."""

import json

import pytest

from archimate.llm import build_requirements, generate_design
from archimate.llm.providers.mock import MockProvider, load_fixture
from archimate.llm.settings import LLMSettings
from archimate.llm.validation import ZERO_BUDGET_NOTE, check_budget_note, zero_budget_sentence
from archimate.schemas import AwsService, DesignDraft, Tier

HONEST = f"Amazon S3·Amazon API Gateway는 항상 무료가 아니라 {ZERO_BUDGET_NOTE}."


def minimal(budget_note: str, drop: tuple[str, ...] = ()) -> DesignDraft:
    data = json.loads(load_fixture("design_minimal_cost.json"))
    data["budgetNote"] = budget_note
    arch = data["architecture"]
    arch["nodes"] = [n for n in arch["nodes"] if n["serviceType"] not in drop]
    ids = {n["id"] for n in arch["nodes"]}
    arch["edges"] = [e for e in arch["edges"] if e["from"] in ids and e["to"] in ids]
    return DesignDraft.model_validate(data)


def test_zero_budget_with_paid_services_needs_note():
    errors = check_budget_note(minimal("항상 무료 한도 안에서 운영돼요."), 0)
    assert errors == [f"예산이 0원이라 budgetNote에 다음 문장을 넣으세요: '{HONEST}'"]


def test_fixed_cost_services_say_budget_is_exceeded():
    sentence = zero_budget_sentence({AwsService.RDS, AwsService.EC2, AwsService.S3})
    assert sentence == "Amazon RDS·Amazon EC2는 켜 두는 동안 고정 요금이 나와 예산 0원을 넘어요."
    assert zero_budget_sentence({AwsService.LAMBDA, AwsService.DYNAMODB}) is None


@pytest.mark.parametrize(
    "claim",
    [
        "월 예산 0원 조건에 완벽히 부합해요.",
        "추가 비용이 발생하지 않습니다.",
        "프리티어 혜택 내에서 모두 충당 가능합니다.",
        "월 예산 0원 범위 내에서 운영돼요.",
    ],
)
def test_zero_cost_claims_are_rejected(claim):
    errors = check_budget_note(minimal(f"{HONEST} {claim}"), 0)
    assert errors == [errors[0]] and "0원을 단정하는 표현을 빼세요" in errors[0]


def test_honest_note_passes_and_true_phrases_are_allowed():
    note = (
        f"{HONEST} 트래픽이 적을 때 고정 비용은 전혀 발생하지 않고, 평소엔 비용이 거의 들지 않아요."
    )
    assert check_budget_note(minimal(note), 0) == []


def test_only_for_zero_budget_and_paid_services():
    assert check_budget_note(minimal("추가 비용이 발생하지 않습니다."), 50_000) == []
    # 항상 무료 서비스만 쓰면 0원 운영이 가능하므로 안내가 필요 없다
    free_only = minimal("항상 무료 한도 안에서 운영돼요.", drop=("s3", "api_gateway"))
    assert check_budget_note(free_only, 0) == []


async def test_pipeline_retries_until_note_is_added():
    answers = {**json.loads(load_fixture("demo_answers.json")), "monthlyBudget": 0}
    req = build_requirements(answers)
    bad = minimal("월 예산 0원 조건에 완벽히 부합해요.").model_dump_json(by_alias=True)
    good = minimal(HONEST).model_dump_json(by_alias=True)
    provider = MockProvider(responses=[bad, good])
    design = await generate_design(
        req,
        Tier.MINIMAL_COST,
        provider=provider,
        settings=LLMSettings(_env_file=None, provider="mock"),
    )
    assert ZERO_BUDGET_NOTE in design.budget_note
    assert "0원을 단정하는 표현을 빼세요" in provider.calls[1][2].content


# ── 운영 절차의 예산 알람: AWS Budgets는 달러로만 입력 ──


def _guide(mutate=None):
    from archimate.schemas import OperationsGuide

    data = json.loads(load_fixture("operations_guide.json"))
    if mutate:
        mutate(data["phases"][0]["steps"][1])
    return OperationsGuide.model_validate(data)


def test_budget_alarm_prompt_uses_usd_or_zero_spend():
    from archimate.llm import prompts

    answers = json.loads(load_fixture("demo_answers.json"))
    assert "'36'(USD)" in prompts.budget_alarm(build_requirements(answers))
    answers["monthlyBudget"] = 0
    assert "제로 지출 예산" in prompts.budget_alarm(build_requirements(answers))


def test_check_budget_alarm():
    from archimate.llm.validation import budget_alarm_usd, check_budget_alarm

    assert budget_alarm_usd(50_000) == 36 and budget_alarm_usd(300_000) == 217
    assert check_budget_alarm(_guide(), 50_000) == []
    assert "'217'(USD)" in check_budget_alarm(_guide(), 300_000)[0]  # 원화만 쓰면 다시 요청
    assert "제로 지출 예산" in check_budget_alarm(_guide(), 0)[0]

    def zero_spend(step):
        step["todo"][0] = "AWS Budgets에서 '제로 지출 예산' 템플릿을 골라요."

    assert check_budget_alarm(_guide(zero_spend), 0) == []
