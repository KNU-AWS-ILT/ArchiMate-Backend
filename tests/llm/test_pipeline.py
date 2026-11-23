"""생성 파이프라인 — 검증 실패 피드백 재시도, 운영 절차 실패, on_tier_done, 노드 설명 (#24).

실패 처리(#21)는 test_tier_failures.py, 기한(#22)은 test_deadlines.py, 일시 오류(#23)는 test_retries.py.
"""

import asyncio
import json

import pytest

from archimate.llm import (
    LLMOutputError,
    build_requirements,
    explain_node,
    generate_design,
    generate_designs,
)
from archimate.llm.providers.mock import MockProvider, load_fixture
from archimate.llm.settings import LLMSettings
from archimate.schemas import SERVICE_INFO, AwsService, Design, DesignDraft, Tier


@pytest.fixture
def req():
    return build_requirements(json.loads(load_fixture("demo_answers.json")))


def settings(**kwargs) -> LLMSettings:
    return LLMSettings(_env_file=None, provider="mock", **kwargs)


def design_missing_memory() -> str:
    """lambda 노드의 memoryMb가 빠진 최소비용안 (기본값으로 채우지 않는 필드)."""
    data = json.loads(load_fixture("design_minimal_cost.json"))
    lam = next(n for n in data["architecture"]["nodes"] if n["serviceType"] == "lambda")
    lam["usage"]["memoryMb"] = None
    return json.dumps(data)


# ── 검증 실패 → 피드백 재시도 ──


async def test_validation_error_is_fed_back_and_retried(req):
    good = load_fixture("design_minimal_cost.json")
    provider = MockProvider(responses=[design_missing_memory(), good])
    design = await generate_design(req, Tier.MINIMAL_COST, provider=provider, settings=settings())
    assert design.tier == Tier.MINIMAL_COST

    retry = provider.calls[1]
    assert [m.role for m in retry] == ["user", "assistant", "user"]
    assert retry[1].content == design_missing_memory()  # 직전 응답을 그대로 보여 주고
    assert "<errors>" in retry[2].content  # 무엇이 틀렸는지 알려 준다
    assert "usage.memoryMb가 필요합니다" in retry[2].content


async def test_schema_error_is_also_fed_back(req):
    provider = MockProvider(responses=['{"title": 1}', load_fixture("design_minimal_cost.json")])
    await generate_design(req, Tier.MINIMAL_COST, provider=provider, settings=settings())
    assert "<errors>" in provider.calls[1][2].content


async def test_gives_up_after_max_validation_retries(req):
    provider = MockProvider(responses=[design_missing_memory()] * 3)
    with pytest.raises(LLMOutputError, match="재시도 후에도 검증 실패"):
        await generate_design(
            req,
            Tier.MINIMAL_COST,
            provider=provider,
            settings=settings(max_validation_retries=2),
        )
    assert len(provider.calls) == 3


# ── 운영 절차 실패 · on_tier_done ──


async def test_guide_failure_keeps_design(req):
    bad_guide = json.dumps({**json.loads(load_fixture("operations_guide.json")), "checklists": []})
    provider = MockProvider(responses=[load_fixture("design_minimal_cost.json"), *[bad_guide] * 3])
    saved: list[Design] = []

    async def on_tier_done(design: Design) -> None:
        saved.append(design)

    result = await generate_designs(
        req,
        tiers=[Tier.MINIMAL_COST],
        provider=provider,
        settings=settings(max_validation_retries=2),
        on_tier_done=on_tier_done,
    )
    assert result.failures == []
    assert result.designs[0].operations_guide is None
    assert saved == result.designs  # 운영 절차 없이도 저장한다


async def test_on_tier_done_is_called_as_each_tier_finishes(req):
    delays = {
        Tier.MINIMAL_COST: 0.4,
        Tier.BALANCED: 0.0,
        Tier.SCALABLE: 0.2,
    }  # 느린 CI에서도 순서가 바뀌지 않게 0.2초 간격

    class Staggered(MockProvider):
        async def generate_json(self, *, system, messages, output_model):
            if issubclass(output_model, DesignDraft):
                tier = next(t for t in Tier if f"<tier>{t.value}</tier>" in messages[0].content)
                await asyncio.sleep(delays[tier])
            return await super().generate_json(
                system=system, messages=messages, output_model=output_model
            )

    order: list[Tier] = []

    async def on_tier_done(design: Design) -> None:
        assert design.operations_guide is not None  # 운영 절차까지 붙은 뒤 호출
        order.append(design.tier)

    result = await generate_designs(
        req, provider=Staggered(), settings=settings(), on_tier_done=on_tier_done
    )
    assert order == [Tier.BALANCED, Tier.SCALABLE, Tier.MINIMAL_COST]  # 끝난 순서
    assert [d.tier for d in result.designs] == list(Tier)  # 결과는 안 순서


# ── 노드 설명 ──


@pytest.fixture
def minimal_design(req):
    draft = DesignDraft.model_validate_json(load_fixture("design_minimal_cost.json"))
    return Design(tier=Tier.MINIMAL_COST, **draft.model_dump(by_alias=False))


@pytest.mark.parametrize("node_id", ["ghost", "users"])
async def test_explain_rejects_unknown_and_users_node(req, minimal_design, node_id):
    with pytest.raises(ValueError, match="설명할 수 있는 노드가 아닙니다"):
        await explain_node(req, minimal_design, node_id, settings=settings())


async def test_explain_fills_service_info_from_design(req, minimal_design):
    provider = MockProvider()
    exp = await explain_node(req, minimal_design, "app_db", provider=provider, settings=settings())
    node = next(n for n in minimal_design.architecture.nodes if n.id == "app_db")
    assert (exp.node_id, exp.service_type, exp.role) == ("app_db", AwsService.DYNAMODB, node.role)
    assert (exp.name, exp.category) == SERVICE_INFO[AwsService.DYNAMODB]
    assert [a.name for a in exp.alternatives] == [SERVICE_INFO[AwsService.RDS][0]]
    assert exp.monthly_cost is None  # 금액은 API 쪽이 채운다
    # 설계 때 적은 선택 이유를 소형 모델에 넘긴다
    assert node.reason in provider.calls[0][0].content
