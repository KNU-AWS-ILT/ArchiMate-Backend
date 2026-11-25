"""빠진 필수 사용량 기본값 채우기 (#5). 실제 Gemini에서 RDS 사용량을 빠뜨려 균형안이 실패했다."""

import json

from archimate.llm import build_requirements, generate_designs
from archimate.llm.providers.mock import MockProvider, load_fixture
from archimate.llm.settings import LLMSettings
from archimate.llm.validation import check_design, fill_usage_defaults
from archimate.schemas import AwsService, DesignDraft, Tier


def balanced_without(*fields: str, drop_usage: bool = False) -> dict:
    """균형안 고정 응답에서 RDS 노드의 사용량 일부(또는 전부)를 뺀다."""
    data = json.loads(load_fixture("design_balanced.json"))
    rds = next(n for n in data["architecture"]["nodes"] if n["serviceType"] == "rds")
    if drop_usage:
        rds["usage"] = None
    for f in fields:
        rds["usage"].pop(f, None)
    return data


def rds_usage(d: DesignDraft):
    return next(n for n in d.architecture.nodes if n.service_type == AwsService.RDS).usage


def test_fills_fixed_value_fields():
    d = DesignDraft.model_validate(
        balanced_without("hoursPerMonth", "instanceCount", "multiAz", "storageGb")
    )
    filled = fill_usage_defaults(d)
    u = rds_usage(d)
    assert (u.hours_per_month, u.instance_count, u.multi_az, u.storage_gb) == (730, 1, False, 20)
    assert len(filled) == 4 and all(
        f.endswith(("hoursPerMonth", "instanceCount", "multiAz", "storageGb")) for f in filled
    )
    assert check_design(d) == []


def test_keeps_values_the_model_chose():
    d = DesignDraft.model_validate(balanced_without())
    before = rds_usage(d).model_copy()
    assert fill_usage_defaults(d) == []
    assert rds_usage(d) == before


def test_instance_type_is_not_guessed():
    d = DesignDraft.model_validate(balanced_without(drop_usage=True))
    fill_usage_defaults(d)
    assert rds_usage(d).instance_type is None
    assert any("instanceType" in e for e in check_design(d))


async def test_pipeline_fills_defaults_without_retry():
    data = json.dumps(balanced_without("hoursPerMonth", "storageGb"))
    provider = MockProvider(responses=[data, load_fixture("operations_guide.json")])
    req = build_requirements(json.loads(load_fixture("demo_answers.json")))
    result = await generate_designs(
        req,
        tiers=[Tier.BALANCED],
        provider=provider,
        settings=LLMSettings(_env_file=None, provider="mock"),
    )
    assert len(provider.calls) == 2  # 설계 1번(재시도 없음) + 운영 절차 1번
    rds = next(n for n in result.designs[0].architecture.nodes if n.service_type == AwsService.RDS)
    assert rds.usage.hours_per_month == 730 and rds.usage.storage_gb == 20
