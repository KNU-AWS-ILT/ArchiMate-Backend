"""LLM 생성 파이프라인 — 이 모듈의 공개 함수가 백엔드 API 계층과의 경계다.

    generate_designs(req, on_tier_done=...)  → DesignSet  (3안 병렬 생성, 안마다 운영 절차 포함)
    explain_node(req, design, node_id)       → NodeExplanation  (노드 설명, 소형 모델)
    generate_operations_guide(req, d)        → OperationsGuide  (운영 절차만 다시 만들 때)

흐름: 프롬프트 조립 → 제공자 호출(JSON) → Pydantic 검증 → 의미 검증
      → 실패 시 에러 목록을 피드백으로 붙여 재시도 (최대 LLM_MAX_VALIDATION_RETRIES회)

전체 기한(#22): 설계 생성은 LLM_GENERATION_DEADLINE_S(3안 공통), 노드 설명은 LLM_EXPLAIN_DEADLINE_S.
호출마다 남은 시간만 기다리고, 남은 시간이 부족하면 재시도하지 않고 LLMTimeoutError로 끝낸다.

일시 오류(#23): 429·5xx·네트워크 오류는 지수 백오프로 최대 LLM_MAX_TRANSIENT_RETRIES회 다시 부른다.
설계·운영 절차는 한도 초과·과부하가 계속되면 LLM_FALLBACK_MODEL로 1회 더 시도한다 (노드 설명은 대체 모델 없음).
"""

import asyncio
import logging
import random
import time
from collections.abc import Awaitable, Callable, Sequence
from typing import TypeVar

from pydantic import BaseModel, ValidationError

from archimate.llm import prompts
from archimate.llm.conflicts import check_conflict_notes, find_conflicts
from archimate.llm.errors import (
    AllTiersFailedError,
    LLMConfigError,
    LLMError,
    LLMOutputError,
    LLMProviderError,
    LLMRateLimitError,
    LLMTimeoutError,
    TierCallbackError,
    TierInternalError,
)
from archimate.llm.providers import ChatMessage, LLMProvider, build_provider
from archimate.llm.settings import LLMSettings, get_settings
from archimate.llm.validation import (
    check_budget_note,
    check_design,
    check_explanation,
    check_operations_guide,
    fill_usage_defaults,
)
from archimate.schemas import (
    SERVICE_INFO,
    Alternative,
    AwsService,
    Design,
    DesignDraft,
    DesignSet,
    ExplanationDraft,
    NodeExplanation,
    OperationsGuide,
    Requirements,
    Tier,
    TierFailure,
)

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)


# 남은 시간이 이보다 짧으면 새 호출을 시작하지 않는다 (응답을 받을 가능성이 낮고 비용만 든다)
MIN_ATTEMPT_S = 5.0


class Deadline:
    """전체 기한. 같은 객체를 넘기면 여러 호출(3안, 설계 → 운영 절차)이 같은 기한을 나눠 쓴다."""

    def __init__(self, seconds: float) -> None:
        self.seconds = seconds
        self._ends_at = time.monotonic() + seconds

    def remaining(self) -> float:
        return self._ends_at - time.monotonic()


class _ProviderCall:
    """제공자 호출 = 전체 기한 안에서 일시 오류 재시도(지수 백오프) + 한도 초과 시 대체 모델 1회 (#23).

    대체 모델로 바꾸면 같은 요청의 검증 재시도도 대체 모델로 이어 간다.
    """

    def __init__(
        self,
        provider: LLMProvider,
        settings: LLMSettings,
        *,
        label: str,
        deadline: Deadline,
        allow_fallback: bool,
    ) -> None:
        self.provider = provider
        self.used_fallback = False
        self._settings = settings
        self._label = label
        self._deadline = deadline
        self._fallback_model = settings.fallback_model if allow_fallback else None

    async def __call__(
        self,
        *,
        system: str,
        messages: list[ChatMessage],
        output_model: type[BaseModel],
        detail: str = "",
    ) -> str:
        retries = 0
        while True:
            timeout_s = min(self._settings.request_timeout_s, self._remaining_or_raise(detail))
            try:
                async with asyncio.timeout(timeout_s):
                    return await self.provider.generate_json(
                        system=system, messages=messages, output_model=output_model
                    )
            except TimeoutError as e:
                raise LLMTimeoutError(f"{self._label}: {timeout_s:.3g}초 안에 응답 없음") from e
            except LLMProviderError as e:
                # 시간 초과(LLMTimeoutError)는 일시 오류로 보지 않는다. 호출 하나가 최대 50초라
                # 다시 불러도 또 느릴 가능성이 높고, 전체 기한만 써 버린다 (#23)
                if not e.transient:
                    raise
                if retries < self._settings.max_transient_retries:
                    delay = self._backoff(retries, e.retry_after_s)
                    if self._deadline.remaining() - delay >= MIN_ATTEMPT_S:
                        retries += 1
                        logger.warning(
                            "llm.transient_retry label=%s code=%s retry=%d delay=%.1fs err=%s",
                            self._label,
                            e.code,
                            retries,
                            delay,
                            e,
                        )
                        await asyncio.sleep(delay)
                        continue
                if self._switch_to_fallback(e):
                    retries = self._settings.max_transient_retries  # 대체 모델은 1회만
                    continue
                raise

    def _remaining_or_raise(self, detail: str) -> float:
        remaining = self._deadline.remaining()
        if remaining < MIN_ATTEMPT_S:
            logger.warning("llm.deadline_exceeded label=%s remaining=%.1fs", self._label, remaining)
            raise LLMTimeoutError(
                f"{self._label}: 전체 기한 {self._deadline.seconds:g}초 안에 끝내지 못함{detail}"
            )
        return remaining

    def _backoff(self, retry: int, retry_after_s: float | None) -> float:
        base = self._settings.retry_base_delay_s
        delay = base * 2**retry + random.uniform(0, base)
        return max(delay, retry_after_s or 0.0)

    def _switch_to_fallback(self, e: LLMProviderError) -> bool:
        model = self._fallback_model
        # 한도 초과·과부하는 모델별이라 다른 모델이면 될 수 있다. 네트워크 끊김·500은 바꿔도 같다
        if (
            not (isinstance(e, LLMRateLimitError) or e.overloaded)
            or not model
            or self.used_fallback
            or model == self.provider.model
        ):
            return False
        logger.warning(
            "llm.fallback label=%s code=%s from=%s to=%s",
            self._label,
            e.code,
            self.provider.model,
            model,
        )
        self.provider = build_provider(self._settings, model=model)
        self.used_fallback = True
        return True


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
    deadline: Deadline,
    allow_fallback: bool = True,
    prepare: Callable[[T], list[str]] | None = None,
) -> T:
    """prepare: 검증 전에 결과를 고친다 (예: 빠진 사용량 기본값). 고친 항목 목록을 돌려준다."""
    messages = [ChatMessage("user", user)]
    errors: list[str] = []
    call = _ProviderCall(
        provider, settings, label=label, deadline=deadline, allow_fallback=allow_fallback
    )

    for attempt in range(settings.max_validation_retries + 1):
        detail = f" (직전 검증 오류: {'; '.join(errors[:3])})" if errors else ""
        started = time.perf_counter()
        raw = await call(system=system, messages=messages, output_model=output_model, detail=detail)
        elapsed = time.perf_counter() - started

        try:
            result = output_model.model_validate_json(raw)
            if prepare is not None and (fixed := prepare(result)):
                logger.info("llm.filled_defaults label=%s fields=%s", label, fixed)
            errors = check(result)
        except ValidationError as e:
            errors = _format_validation_error(e)

        logger.info(
            "llm.generate label=%s provider=%s model=%s fallback=%s attempt=%d elapsed=%.1fs "
            "errors=%d",
            label,
            call.provider.name,
            call.provider.model,
            call.used_fallback,
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
_explain_provider: LLMProvider | None = None


def get_default_provider() -> LLMProvider:
    """설계용 제공자 싱글턴 (Lambda 웜 스타트 때 클라이언트 재사용)."""
    global _default_provider
    if _default_provider is None:
        _default_provider = build_provider(get_settings())
    return _default_provider


def get_explain_provider() -> LLMProvider:
    """노드 설명용 소형 모델 제공자 싱글턴."""
    global _explain_provider
    if _explain_provider is None:
        settings = get_settings()
        _explain_provider = build_provider(settings, model=settings.resolved_explain_model)
    return _explain_provider


async def generate_design(
    req: Requirements,
    tier: Tier,
    *,
    provider: LLMProvider | None = None,
    settings: LLMSettings | None = None,
    deadline: Deadline | None = None,
) -> Design:
    provider, settings = _resolve(provider, settings)
    conflicts = find_conflicts(req)
    draft = await _generate_validated(
        provider,
        settings,
        system=prompts.design_system_prompt(),
        user=prompts.design_user_prompt(req, tier),
        output_model=DesignDraft,
        # 자유 서술 충돌을 코드가 찾았으면 cons에 알렸는지도 확인한다 (#5)
        check=lambda d: (
            check_design(d)
            + check_conflict_notes(d.cons, conflicts)
            + check_budget_note(d, req.monthly_budget)
        ),
        prepare=fill_usage_defaults,
        label=f"design:{tier.value}",
        deadline=deadline or Deadline(settings.generation_deadline_s),
    )
    return Design(tier=tier, **draft.model_dump(by_alias=False))


async def _generate_tier(
    req: Requirements,
    tier: Tier,
    provider: LLMProvider,
    settings: LLMSettings,
    on_tier_done: Callable[[Design], Awaitable[None]] | None,
    deadline: Deadline,
) -> Design:
    """1안 = 설계 → 운영 절차. 운영 절차가 실패해도 설계는 살린다(operationsGuide=null).

    운영 절차는 설계가 쓰고 남은 기한 안에서 만든다. 기한이 부족하면 운영 절차 없이 저장한다.
    """
    design = await generate_design(
        req, tier, provider=provider, settings=settings, deadline=deadline
    )
    try:
        design.operations_guide = await generate_operations_guide(
            req, design, provider=provider, settings=settings, deadline=deadline
        )
    except LLMConfigError:
        raise
    except LLMError as e:
        logger.error("llm.guide_failed tier=%s code=%s err=%s", tier.value, e.code, e)
    if on_tier_done is not None:
        try:
            await on_tier_done(design)
        except Exception as e:
            # 비용 계산·저장 실패는 이 안만 실패로 처리 (다른 안은 계속)
            raise TierCallbackError(f"{tier.value}: on_tier_done 실패 — {e!r}") from e
    return design


async def generate_designs(
    req: Requirements,
    *,
    tiers: Sequence[Tier] | None = None,
    provider: LLMProvider | None = None,
    settings: LLMSettings | None = None,
    on_tier_done: Callable[[Design], Awaitable[None]] | None = None,
) -> DesignSet:
    """3안을 병렬로 생성. 일부 실패는 failures에 담고, 전부 실패하면 AllTiersFailedError.

    안 하나의 실패(LLM 오류, on_tier_done 오류, 예상 못 한 오류)는 그 안만 실패로 처리한다.
    설정 오류(LLMConfigError)와 취소(CancelledError)만 전체로 올린다.
    tiers로 일부 안만 만들 수 있다(1안 축소, #14). 생략하면 LLM_TIERS 설정(기본 3안 전체).
    failures에는 시도한 안만 들어간다.
    세 안은 LLM_GENERATION_DEADLINE_S 기한을 함께 쓰고, 기한 안에 못 끝낸 안은 LLM_TIMEOUT 실패다.

    on_tier_done: 안 하나가 끝날 때마다 호출된다. 비용 계산·저장을 여기서 하면
    프론트가 끝난 안부터 볼 수 있다(고도화 문서 4장 ④-2).
    """
    provider, settings = _resolve(provider, settings)
    if tiers is None:
        tiers = settings.tiers
    deadline = Deadline(settings.generation_deadline_s)
    results = await asyncio.gather(
        *(_generate_tier(req, t, provider, settings, on_tier_done, deadline) for t in tiers),
        return_exceptions=True,
    )

    designs: list[Design] = []
    failures: list[TierFailure] = []
    causes: list[LLMError] = []
    for tier, result in zip(tiers, results, strict=True):
        if isinstance(result, LLMConfigError):
            raise result  # 설정 문제는 안별 실패가 아니라 전체 실패
        if isinstance(result, Design):
            designs.append(result)
            continue
        if not isinstance(result, Exception):
            raise result  # CancelledError 등 BaseException은 그대로 올린다

        if isinstance(result, LLMError):
            err = result
        else:
            err = TierInternalError(f"{tier.value}: 예상 못 한 오류 — {result!r}")
            err.__cause__ = result
        logger.error(
            "llm.tier_failed tier=%s code=%s err=%s",
            tier.value,
            err.code,
            err,
            # 콜백·예상 못 한 오류는 원인 추적용 스택 기록
            exc_info=result if isinstance(err, TierInternalError) else None,
        )
        failures.append(TierFailure(tier=tier, reason=err.code))
        causes.append(err)

    if not designs:
        raise AllTiersFailedError(failures=failures, causes=causes)
    return DesignSet(designs=designs, failures=failures)


async def generate_operations_guide(
    req: Requirements,
    design: Design,
    *,
    provider: LLMProvider | None = None,
    settings: LLMSettings | None = None,
    deadline: Deadline | None = None,
) -> OperationsGuide:
    provider, settings = _resolve(provider, settings)
    return await _generate_validated(
        provider,
        settings,
        system=prompts.operations_system_prompt(),
        user=prompts.operations_user_prompt(req, design),
        output_model=OperationsGuide,
        check=lambda g: check_operations_guide(g, design.architecture),
        label=f"operations:{design.tier.value}",
        deadline=deadline or Deadline(settings.generation_deadline_s),
    )


async def explain_node(
    req: Requirements,
    design: Design,
    node_id: str,
    *,
    provider: LLMProvider | None = None,
    settings: LLMSettings | None = None,
) -> NodeExplanation:
    """노드 클릭 시 설명(D). 캐시 확인·저장과 monthlyCost는 호출하는 쪽(핸들러)이 한다.

    없는 노드나 사용자 노드면 ValueError (API에서 404 NOT_FOUND).
    전체 기한은 LLM_EXPLAIN_DEADLINE_S (API Gateway 29초 안에서 항상 우리 에러 형식으로 응답).
    """
    node = next((n for n in design.architecture.nodes if n.id == node_id), None)
    if node is None or node.service_type == AwsService.USERS:
        raise ValueError(f"설명할 수 있는 노드가 아닙니다: {node_id}")

    if settings is None:
        settings = get_settings()
        provider = provider or get_explain_provider()
    else:
        provider = provider or build_provider(settings, model=settings.resolved_explain_model)

    draft = await _generate_validated(
        provider,
        settings,
        system=prompts.explain_system_prompt(),
        user=prompts.explain_user_prompt(req, design, node),
        output_model=ExplanationDraft,
        check=lambda e: check_explanation(e, node.service_type),
        label=f"explain:{design.tier.value}:{node.id}",
        deadline=Deadline(settings.explain_deadline_s),
        allow_fallback=False,  # 기한 20초가 짧고, 실패해도 사용자가 다시 누르면 된다
    )
    name, category = SERVICE_INFO[node.service_type]
    return NodeExplanation(
        node_id=node.id,
        service_type=node.service_type,
        name=name,
        category=category,
        role=node.role,
        reason=draft.reason,
        alternatives=[
            Alternative(
                service_type=a.service_type,
                name=SERVICE_INFO[a.service_type][0],
                comparison=a.comparison,
            )
            for a in draft.alternatives
        ],
        cautions=draft.cautions,
    )
