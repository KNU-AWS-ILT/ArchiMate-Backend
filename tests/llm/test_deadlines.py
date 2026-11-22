"""LLM 전체 기한과 생성할 안 설정 (#22)."""

import asyncio
import json
import time

import pytest
from pydantic import ValidationError

from archimate.llm import (
    AllTiersFailedError,
    LLMTimeoutError,
    build_requirements,
    explain_node,
    generate_designs,
    pipeline,
)
from archimate.llm.pipeline import Deadline
from archimate.llm.providers.mock import MockProvider, load_fixture
from archimate.llm.settings import LLMSettings
from archimate.schemas import Design, DesignDraft, ExplanationDraft, Tier


class SlowProvider(MockProvider):
    """고정 응답을 주되, 지정한 안의 설계 요청과 노드 설명 요청은 delay초 뒤에 응답한다."""

    def __init__(self, delay: float, tiers: set[Tier] | None = None, **kwargs) -> None:
        super().__init__(**kwargs)
        self._delay = delay
        self._tiers = {t.value for t in (tiers or set(Tier))}

    async def generate_json(self, *, system, messages, output_model):
        if issubclass(output_model, DesignDraft):
            slow = any(f"<tier>{t}</tier>" in messages[0].content for t in self._tiers)
        else:
            slow = issubclass(output_model, ExplanationDraft)
        if slow:
            await asyncio.sleep(self._delay)
        return await super().generate_json(
            system=system, messages=messages, output_model=output_model
        )


@pytest.fixture(autouse=True)
def short_min_attempt(monkeypatch):
    # 실제 값(5초) 대신 테스트 규모에 맞춘다
    monkeypatch.setattr(pipeline, "MIN_ATTEMPT_S", 0.05)


@pytest.fixture
def req():
    return build_requirements(json.loads(load_fixture("demo_answers.json")))


def settings(**kwargs) -> LLMSettings:
    return LLMSettings(_env_file=None, provider="mock", **kwargs)


def test_deadline_remaining_counts_down():
    d = Deadline(10)
    first = d.remaining()
    time.sleep(0.01)
    assert 9 < d.remaining() < first <= 10


async def test_call_waits_only_for_remaining_time(req):
    # 1회 제한 50초여도 전체 기한이 0.2초면 0.2초 안에 끝난다
    s = settings(request_timeout_s=50, generation_deadline_s=0.2)
    started = time.monotonic()
    with pytest.raises(AllTiersFailedError) as e:
        await generate_designs(req, provider=SlowProvider(delay=5), settings=s)
    assert time.monotonic() - started < 1
    assert [f.reason for f in e.value.failures] == ["LLM_TIMEOUT"] * 3
    assert isinstance(e.value.common_cause, LLMTimeoutError)


async def test_no_retry_when_time_is_short(req):
    # 검증 실패가 계속 나도, 기한이 지나면 재시도 횟수가 남아 있어도 멈춘다
    class SlowInvalid(MockProvider):
        async def generate_json(self, **kwargs):
            await asyncio.sleep(0.1)
            return await super().generate_json(**kwargs)

    provider = SlowInvalid(responses=["{}"] * 10)
    s = settings(max_validation_retries=5, generation_deadline_s=0.25)
    with pytest.raises(AllTiersFailedError) as e:
        await generate_designs(req, tiers=[Tier.MINIMAL_COST], provider=provider, settings=s)
    assert [f.reason for f in e.value.failures] == ["LLM_TIMEOUT"]
    assert "전체 기한" in str(e.value.causes[0])
    assert len(provider.calls) < 6


async def test_slow_tier_times_out_and_others_are_kept(req):
    saved: list[Tier] = []

    async def on_tier_done(design: Design) -> None:
        saved.append(design.tier)

    s = settings(generation_deadline_s=0.3)
    result = await generate_designs(
        req,
        provider=SlowProvider(delay=5, tiers={Tier.SCALABLE}),
        settings=s,
        on_tier_done=on_tier_done,
    )
    assert sorted(saved) == sorted([Tier.MINIMAL_COST, Tier.BALANCED])
    assert [(f.tier, f.reason) for f in result.failures] == [(Tier.SCALABLE, "LLM_TIMEOUT")]


async def test_guide_out_of_time_keeps_design(req):
    # 설계는 끝났지만 운영 절차를 만들 시간이 없으면 운영 절차 없이 저장한다
    class SlowGuide(MockProvider):
        async def generate_json(self, *, system, messages, output_model):
            if not issubclass(output_model, DesignDraft):
                await asyncio.sleep(5)
            return await super().generate_json(
                system=system, messages=messages, output_model=output_model
            )

    s = settings(generation_deadline_s=0.2)
    result = await generate_designs(
        req, tiers=[Tier.MINIMAL_COST], provider=SlowGuide(), settings=s
    )
    assert result.designs[0].operations_guide is None
    assert result.failures == []


async def test_explain_uses_its_own_deadline(req):
    design_set = await generate_designs(req, tiers=[Tier.MINIMAL_COST], settings=settings())
    s = settings(generation_deadline_s=100, explain_deadline_s=0.2)
    started = time.monotonic()
    with pytest.raises(LLMTimeoutError):
        await explain_node(
            req, design_set.designs[0], "app_db", provider=SlowProvider(delay=5), settings=s
        )
    assert time.monotonic() - started < 1


# ── 생성할 안 설정 LLM_TIERS ──


@pytest.mark.parametrize(
    ("env", "expected"),
    [
        ("minimal_cost", [Tier.MINIMAL_COST]),
        ("scalable, minimal_cost,scalable", [Tier.MINIMAL_COST, Tier.SCALABLE]),
        ("", list(Tier)),
    ],
)
def test_tiers_from_env(monkeypatch, env, expected):
    monkeypatch.setenv("LLM_TIERS", env)
    assert LLMSettings(_env_file=None).tiers == expected


def test_unknown_tier_is_rejected(monkeypatch):
    monkeypatch.setenv("LLM_TIERS", "cheapest")
    with pytest.raises(ValidationError):
        LLMSettings(_env_file=None)


async def test_generate_designs_uses_tiers_setting(req):
    result = await generate_designs(req, settings=settings(tiers=[Tier.BALANCED]))
    assert [d.tier for d in result.designs] == [Tier.BALANCED]
