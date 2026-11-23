"""3안 생성 실패 처리 (#21): 안별 실패 이유, 콜백 오류 격리, 1안 모드, 운영 절차 출력 보완."""

import asyncio
import json
import re

import pytest

from archimate.llm import (
    AllTiersFailedError,
    LLMConfigError,
    LLMOutputError,
    LLMRateLimitError,
    LLMTimeoutError,
    build_requirements,
    generate_designs,
)
from archimate.llm.providers.mock import MockProvider, load_fixture
from archimate.llm.settings import LLMSettings
from archimate.llm.validation import check_operations_guide
from archimate.schemas import Design, DesignDraft, OperationsGuide, Tier
from archimate.schemas.operations import GuideStep

_TIER_TAG = re.compile(r"<tier>(\w+)</tier>")


class TierFailProvider(MockProvider):
    """고정 응답을 주되, 지정한 안의 설계 요청에서는 예외를 던진다."""

    def __init__(self, fail: dict[Tier, BaseException]) -> None:
        super().__init__()
        self._fail = {t.value: e for t, e in fail.items()}

    async def generate_json(self, *, system, messages, output_model):
        if issubclass(output_model, DesignDraft):
            tier = _TIER_TAG.search(messages[0].content).group(1)
            if tier in self._fail:
                raise self._fail[tier]
        return await super().generate_json(
            system=system, messages=messages, output_model=output_model
        )


@pytest.fixture
def req():
    return build_requirements(json.loads(load_fixture("demo_answers.json")))


@pytest.fixture
def settings():
    # 일시 오류 재시도(#23)는 test_retries.py에서 본다. 여기서는 안별 실패 처리만 확인
    return LLMSettings(_env_file=None, provider="mock", max_transient_retries=0)


async def run(req, settings, fail=None, **kwargs):
    return await generate_designs(
        req, provider=TierFailProvider(fail or {}), settings=settings, **kwargs
    )


# ── 일부 실패 ──


async def test_partial_failure_keeps_other_tiers(req, settings):
    result = await run(req, settings, {Tier.SCALABLE: LLMRateLimitError("한도")})
    assert [d.tier for d in result.designs] == [Tier.MINIMAL_COST, Tier.BALANCED]
    assert [(f.tier, f.reason) for f in result.failures] == [(Tier.SCALABLE, "LLM_RATE_LIMITED")]


async def test_unexpected_error_fails_only_that_tier(req, settings):
    result = await run(req, settings, {Tier.BALANCED: RuntimeError("SDK 버그")})
    assert len(result.designs) == 2
    assert [(f.tier, f.reason) for f in result.failures] == [(Tier.BALANCED, "TIER_INTERNAL_ERROR")]


async def test_callback_error_fails_only_that_tier(req, settings):
    saved: list[Tier] = []

    async def on_tier_done(design: Design) -> None:
        if design.tier == Tier.BALANCED:
            raise KeyError("단가 없음")
        saved.append(design.tier)

    result = await run(req, settings, on_tier_done=on_tier_done)
    assert sorted(saved) == sorted([Tier.MINIMAL_COST, Tier.SCALABLE])
    assert [d.tier for d in result.designs] == [Tier.MINIMAL_COST, Tier.SCALABLE]
    assert [(f.tier, f.reason) for f in result.failures] == [
        (Tier.BALANCED, "TIER_CALLBACK_FAILED")
    ]


# ── 전부 실패 ──


async def test_all_failed_with_same_cause_reports_it(req, settings):
    fail = {t: LLMRateLimitError("한도") for t in Tier}
    with pytest.raises(AllTiersFailedError) as e:
        await run(req, settings, fail)
    assert [f.reason for f in e.value.failures] == ["LLM_RATE_LIMITED"] * 3
    assert isinstance(e.value.common_cause, LLMRateLimitError)


async def test_all_failed_with_mixed_causes_has_no_common_cause(req, settings):
    fail = {
        Tier.MINIMAL_COST: LLMRateLimitError("한도"),
        Tier.BALANCED: LLMTimeoutError("시간 초과"),
        Tier.SCALABLE: LLMOutputError("검증 실패"),
    }
    with pytest.raises(AllTiersFailedError) as e:
        await run(req, settings, fail)
    assert [(f.tier, f.reason) for f in e.value.failures] == [
        (Tier.MINIMAL_COST, "LLM_RATE_LIMITED"),
        (Tier.BALANCED, "LLM_TIMEOUT"),
        (Tier.SCALABLE, "LLM_INVALID_OUTPUT"),
    ]
    assert e.value.common_cause is None


async def test_config_error_is_raised_as_is(req, settings):
    with pytest.raises(LLMConfigError):
        await run(req, settings, {Tier.BALANCED: LLMConfigError("키 없음")})


async def test_cancellation_is_not_swallowed(req, settings):
    with pytest.raises(asyncio.CancelledError):
        await run(req, settings, {Tier.BALANCED: asyncio.CancelledError()})


def test_all_tiers_failed_error_keeps_simple_constructor():
    e = AllTiersFailedError("boom")
    assert str(e) == "boom"
    assert e.failures == [] and e.common_cause is None


# ── 1안 모드 (#14) ──


async def test_single_tier_mode_reports_only_attempted_tier(req, settings):
    result = await run(req, settings, tiers=[Tier.MINIMAL_COST])
    assert [d.tier for d in result.designs] == [Tier.MINIMAL_COST]
    assert result.failures == []

    with pytest.raises(AllTiersFailedError) as e:
        await run(
            req,
            settings,
            {Tier.MINIMAL_COST: LLMTimeoutError("시간 초과")},
            tiers=[Tier.MINIMAL_COST],
        )
    assert [(f.tier, f.reason) for f in e.value.failures] == [(Tier.MINIMAL_COST, "LLM_TIMEOUT")]


# ── 운영 절차 출력 보완 (#13) ──


def test_blank_console_path_and_warn_become_null():
    step = GuideStep.model_validate(
        {
            "id": "a",
            "title": "t",
            "desc": "d",
            "nodeIds": [],
            "todo": ["x"],
            "consolePath": "",
            "warn": "  ",
        }
    )
    assert step.console_path is None and step.warn is None
    assert step.model_dump(mode="json")["consolePath"] is None


def test_duplicate_phase_key_is_rejected():
    guide = OperationsGuide.model_validate_json(load_fixture("operations_guide.json"))
    guide.phases[1].key = guide.phases[0].key
    errors = check_operations_guide(guide)
    assert any("단계 key" in e and "중복" in e for e in errors)
