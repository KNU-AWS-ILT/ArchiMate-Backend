"""LLM 생성 파이프라인 — 이 모듈의 공개 함수가 백엔드 API 계층과의 경계다.

    generate_designs(req)               → DesignSet   (3안 병렬 생성)
    generate_operations_guide(req, d)   → OperationsGuide

흐름: 프롬프트 조립 → 제공자 호출(JSON) → Pydantic 검증 → 의미 검증
      → 실패 시 에러 목록을 피드백으로 붙여 재시도 (최대 LLM_MAX_VALIDATION_RETRIES회)
"""

import asyncio
import logging
import time
from collections.abc import Callable, Sequence
from typing import TypeVar

from pydantic import BaseModel, ValidationError

from archimate.llm import prompts
from archimate.llm.errors import (
    AllTiersFailedError,
    LLMConfigError,
    LLMError,
    LLMOutputError,
    LLMTimeoutError,
)
from archimate.llm.providers import ChatMessage, LLMProvider, build_provider
from archimate.llm.settings import LLMSettings, get_settings
from archimate.llm.validation import check_design, check_operations_guide
from archimate.schemas import (
    Design,
    DesignDraft,
    DesignSet,
    OperationsGuide,
    Requirements,
    Tier,
    TierFailure,
)

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)


def _format_validation_error(e: ValidationError) -> list[str]:
    return [
        f"{'.'.join(str(p) for p in err['loc']) or '(root)'}: {err['msg']}" for err in e.errors()
    ]


async def _generate_validated(
    provider: LLMProvider,
    settings: LLMSettings,
    *,
    system: str,
    user: str,
    output_model: type[T],
    check: Callable[[T], list[str]],
    label: str,
) -> T:
    messages = [ChatMessage("user", user)]
    errors: list[str] = []

    for attempt in range(settings.max_validation_retries + 1):
        started = time.perf_counter()
        try:
            async with asyncio.timeout(settings.request_timeout_s):
                raw = await provider.generate_json(
                    system=system, messages=messages, output_model=output_model
                )
        except TimeoutError as e:
            raise LLMTimeoutError(f"{label}: {settings.request_timeout_s}초 안에 응답 없음") from e
        elapsed = time.perf_counter() - started

        try:
            result = output_model.model_validate_json(raw)
            errors = check(result)
        except ValidationError as e:
            errors = _format_validation_error(e)

        logger.info(
            "llm.generate label=%s provider=%s model=%s attempt=%d elapsed=%.1fs errors=%d",
            label,
            provider.name,
            provider.model,
            attempt + 1,
            elapsed,
            len(errors),
        )
        if not errors:
            return result

        logger.warning("llm.invalid_output label=%s errors=%s", label, errors[:5])
        messages += [
            ChatMessage("assistant", raw),
            ChatMessage("user", prompts.retry_feedback(errors)),
        ]

    raise LLMOutputError(f"{label}: 재시도 후에도 검증 실패 — " + "; ".join(errors[:5]))


def _resolve(
    provider: LLMProvider | None, settings: LLMSettings | None
) -> tuple[LLMProvider, LLMSettings]:
    if settings is None:
        return provider or get_default_provider(), get_settings()
    return provider or build_provider(settings), settings


_default_provider: LLMProvider | None = None


def get_default_provider() -> LLMProvider:
    """설정 기반 제공자 싱글턴 (Lambda 웜 스타트 때 클라이언트 재사용)."""
    global _default_provider
    if _default_provider is None:
        _default_provider = build_provider(get_settings())
    return _default_provider


async def generate_design(
    req: Requirements,
    tier: Tier,
    *,
    provider: LLMProvider | None = None,
    settings: LLMSettings | None = None,
) -> Design:
    provider, settings = _resolve(provider, settings)
    draft = await _generate_validated(
        provider,
        settings,
        system=prompts.design_system_prompt(),
        user=prompts.design_user_prompt(req, tier),
        output_model=DesignDraft,
        check=check_design,
        label=f"design:{tier.value}",
    )
    return Design(tier=tier, **draft.model_dump())


async def generate_designs(
    req: Requirements,
    *,
    tiers: Sequence[Tier] = tuple(Tier),
    provider: LLMProvider | None = None,
    settings: LLMSettings | None = None,
) -> DesignSet:
    """3안을 병렬로 생성. 일부 실패는 failures에 담고, 전부 실패하면 예외."""
    provider, settings = _resolve(provider, settings)
    results = await asyncio.gather(
        *(generate_design(req, t, provider=provider, settings=settings) for t in tiers),
        return_exceptions=True,
    )

    designs: list[Design] = []
    failures: list[TierFailure] = []
    for tier, result in zip(tiers, results, strict=True):
        if isinstance(result, LLMConfigError):
            raise result  # 설정 문제는 안별 실패가 아니라 전체 실패
        if isinstance(result, LLMError):
            logger.error("llm.tier_failed tier=%s code=%s err=%s", tier.value, result.code, result)
            failures.append(TierFailure(tier=tier, reason=result.code))
        elif isinstance(result, BaseException):
            raise result
        else:
            designs.append(result)

    if not designs:
        raise AllTiersFailedError("모든 안 생성 실패: " + ", ".join(f.reason for f in failures))
    return DesignSet(designs=designs, failures=failures)


async def generate_operations_guide(
    req: Requirements,
    design: Design,
    *,
    provider: LLMProvider | None = None,
    settings: LLMSettings | None = None,
) -> OperationsGuide:
    provider, settings = _resolve(provider, settings)
    return await _generate_validated(
        provider,
        settings,
        system=prompts.operations_system_prompt(),
        user=prompts.operations_user_prompt(req, design),
        output_model=OperationsGuide,
        check=check_operations_guide,
        label=f"operations:{design.tier.value}",
    )
