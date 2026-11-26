"""API 로직 — LLM 생성·비용 계산·저장을 조합한다. FastAPI·Lambda 어디서 불러도 같은 동작.

id 규칙 (Issue #12에서 확정 전 초안. contracts 예시 `job_demo_minimal_cost`와 같은 모양)
    jobId    = "job_" + 랜덤 32자
    designId = "{jobId}_{tier}"   → designId만으로 job을 찾을 수 있다
"""

import asyncio
import logging
import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta, timezone

from archimate.api.errors import ApiError, llm_error_to_api, not_found, to_error_response
from archimate.llm import AllTiersFailedError, LLMError, explain_node
from archimate.llm import generate_designs as llm_generate_designs
from archimate.llm.settings import get_settings
from archimate.pricing import PriceBook, PriceSource, estimate_monthly_cost
from archimate.schemas import (
    CostItem,
    Design,
    DesignJob,
    DesignResult,
    ErrorCode,
    JobStatus,
    MonthlyCost,
    NodeExplanation,
    OperationsGuide,
    Requirements,
    Tier,
    TierFailure,
)
from archimate.schemas.explanation import NodeCost
from archimate.storage import DesignRepository, JobRecord

logger = logging.getLogger(__name__)

GenerateFn = Callable[..., Awaitable[object]]

# 하루 생성 횟수는 한국 시간 자정에 초기화한다 (#52). 한국은 서머타임이 없어 고정 오프셋으로 충분하다
KST = timezone(timedelta(hours=9), "KST")

# 생성 Lambda 제한 시간(180초)보다 길다. 이 시간이 지나도 끝나지 않은 job은 Lambda가 강제 종료된 것으로 본다.
JOB_STALE_AFTER_S = 200


# ── 시도하는 안 ────────────────────────────────────────


def attempted_tiers() -> list[Tier]:
    """이 서버가 만드는 안 (LLM_TIERS, 기본 3안 전체).

    1안 모드(예: LLM_TIERS=minimal_cost)에서 작업이 취소·정체돼 실패 처리할 때, 만들려고 하지 않은 안까지
    failures에 넣으면 안 된다 (#14). 설정은 같은 Lambda 환경 변수라 생성할 때와 조회할 때 같다.
    """
    return list(get_settings().tiers)


# ── id ──────────────────────────────────────────────


def new_job_id() -> str:
    return f"job_{uuid.uuid4().hex}"


def make_design_id(job_id: str, tier: Tier) -> str:
    return f"{job_id}_{tier.value}"


def parse_design_id(design_id: str) -> tuple[str, Tier] | None:
    for tier in Tier:
        suffix = f"_{tier.value}"
        if design_id.endswith(suffix) and len(design_id) > len(suffix):
            return design_id[: -len(suffix)], tier
    return None


# ── 조회 공통 ──────────────────────────────────────────


async def _get_owned_job(repo: DesignRepository, job_id: str, user_id: str) -> JobRecord:
    job = await repo.get_job(job_id)
    # 남의 작업은 '없음'으로 보인다 (존재 여부도 노출하지 않음)
    if job is None or job.user_id != user_id:
        raise not_found("생성 작업을 찾을 수 없어요.")
    return job


async def _get_owned_design(
    repo: DesignRepository, design_id: str, user_id: str
) -> tuple[JobRecord, DesignResult]:
    parsed = parse_design_id(design_id)
    if parsed is None:
        raise not_found("설계안을 찾을 수 없어요.")
    job = await _get_owned_job(repo, parsed[0], user_id)
    design = next((d for d in job.designs if d.id == design_id), None)
    if design is None:
        raise not_found("설계안을 찾을 수 없어요. 아직 생성 중일 수 있어요.")
    return job, design


# ── ④-1 접수 / ④-3 조회 ────────────────────────────────


def generation_day(now: datetime | None = None) -> str:
    """하루 생성 횟수를 세는 날짜 (한국 시간 기준, 예: "2026-11-24")."""
    return (now or datetime.now(UTC)).astimezone(KST).date().isoformat()


async def create_job(
    repo: DesignRepository,
    user_id: str,
    req: Requirements,
    *,
    daily_limit: int = 0,
    now: datetime | None = None,
) -> str:
    """daily_limit이 0보다 크면 그날 생성 횟수를 먼저 확인한다. 넘으면 429 (#52).

    접수할 때 센다. 생성이 실패해도 LLM 비용은 이미 들기 때문이다.
    """
    if daily_limit > 0 and not await repo.reserve_generation(
        user_id, generation_day(now), daily_limit
    ):
        logger.info("job.daily_limit user_id=%s limit=%d", user_id, daily_limit)
        raise ApiError(
            429,
            ErrorCode.RATE_LIMITED,
            f"오늘 만들 수 있는 횟수({daily_limit}회)를 모두 썼어요. 내일 다시 시도해 주세요.",
            retryable=False,
        )
    job_id = new_job_id()
    await repo.create_job(JobRecord(job_id=job_id, user_id=user_id, requirements=req))
    return job_id


def _expire_if_stale(job: JobRecord, now: datetime) -> JobRecord:
    """Lambda가 시간 초과·메모리 부족으로 강제 종료되면 job이 running으로 남아 프론트가 계속 폴링한다.
    그래서 조회할 때 JOB_STALE_AFTER_S가 지난 작업은 끝난 것으로 응답한다 (저장값은 바꾸지 않는다).

    끝난 안이 있으면 일부 실패(completed), 하나도 없으면 전체 실패(failed + 다시 시도 가능한 error).
    """
    if job.status not in (JobStatus.QUEUED, JobStatus.RUNNING):
        return job
    if (now - job.created_at).total_seconds() <= JOB_STALE_AFTER_S:
        return job
    logger.error("job.stale job_id=%s status=%s", job.job_id, job.status)
    done = {d.tier for d in job.designs}
    failures = [
        TierFailure(tier=t, reason="INTERNAL_ERROR") for t in attempted_tiers() if t not in done
    ]
    if done:
        return job.model_copy(update={"status": JobStatus.COMPLETED, "failures": failures})
    error = to_error_response(
        ApiError(
            500, ErrorCode.INTERNAL_ERROR, "작업이 중단됐어요. 다시 시도해 주세요.", retryable=True
        ),
        request_id=job.job_id,
    )
    return job.model_copy(update={"status": JobStatus.FAILED, "failures": failures, "error": error})


async def get_job(
    repo: DesignRepository, job_id: str, user_id: str, *, now: datetime | None = None
) -> DesignJob:
    job = _expire_if_stale(await _get_owned_job(repo, job_id, user_id), now or datetime.now(UTC))
    return DesignJob(
        job_id=job.job_id,
        status=job.status,
        designs=job.designs,
        failures=job.failures,
        error=job.error,
    )


async def get_guide(repo: DesignRepository, design_id: str, user_id: str) -> OperationsGuide:
    _, design = await _get_owned_design(repo, design_id, user_id)
    if design.operations_guide is None:
        raise not_found("이 설계안의 운영 절차를 만들지 못했어요.")
    return design.operations_guide


# ── ④-2 생성 (JobRunner가 뒤에서 실행) ───────────────────


def _safe_monthly_cost(
    job_id: str, design: Design, budget: int, prices: PriceBook | None = None
) -> MonthlyCost | None:
    """비용 계산이 실패해도 안 자체는 보여 준다 (그 안만 monthlyCost: null). prices가 없으면 스냅샷."""
    try:
        return estimate_monthly_cost(design, monthly_budget=budget, prices=prices)
    except Exception:
        logger.exception("cost.failed job_id=%s tier=%s", job_id, design.tier)
        return None


async def _missing_tiers(
    repo: DesignRepository, job_id: str, job: JobRecord, reason: str
) -> list[TierFailure]:
    """아직 저장되지 않은(= 실패·중단된) 안을 모두 같은 이유로 실패 처리한 목록."""
    done = {d.tier for d in (await repo.get_job(job_id) or job).designs}
    return [TierFailure(tier=t, reason=reason) for t in attempted_tiers() if t not in done]


async def run_design_job(
    repo: DesignRepository,
    job_id: str,
    *,
    generate: GenerateFn = llm_generate_designs,
    mock_delay_s: float = 0.0,
    price_source: PriceSource | None = None,
) -> None:
    """3안 생성 → 안마다 비용 계산·저장 → 최종 상태 저장. 실패도 job에 기록하고 예외를 밖으로 내지 않는다.

    mock_delay_s: 프론트 개발용 지연(MOCK_DELAY_S). 고정 응답은 바로 끝나므로
    queued·running을 각각 이만큼 보여 주고, 안은 Tier 순서대로 이 간격을 두고 저장한다.

    price_source: 비용 계산에 쓸 단가 (AWS에서는 prices 테이블). 없으면 패키지 스냅샷을 쓴다.
    """
    job = await repo.get_job(job_id)
    if job is None:
        logger.error("job.missing job_id=%s", job_id)
        return
    if mock_delay_s:
        await asyncio.sleep(mock_delay_s)  # queued
    # 비동기 호출은 같은 이벤트가 두 번 올 수 있다. queued인 job만 실행해 LLM 비용이 두 번 들지 않게 한다.
    if not await repo.claim_job(job_id):
        logger.warning("job.already_started job_id=%s", job_id)
        return

    budget = job.requirements.monthly_budget
    tier_order = list(Tier)
    prices: PriceBook | None = None
    if price_source is not None:
        try:
            prices = await price_source.get()  # 한 작업의 세 안은 같은 단가표로 계산한다
        except Exception:
            logger.exception("prices.source_failed job_id=%s", job_id)  # 스냅샷으로 계속한다

    async def on_tier_done(design: Design) -> None:
        if mock_delay_s:
            # 안은 병렬로 끝나므로 순서만큼 기다리면 minimal_cost → balanced → scalable 순으로 채워진다
            await asyncio.sleep(mock_delay_s * (tier_order.index(design.tier) + 1))
        result = DesignResult(
            id=make_design_id(job_id, design.tier),
            monthly_cost=_safe_monthly_cost(job_id, design, budget, prices),
            **design.model_dump(by_alias=False),
        )
        await repo.add_design(job_id, result)

    try:
        design_set = await generate(job.requirements, on_tier_done=on_tier_done)
    except LLMError as e:
        # 전부 실패(#21): 안별 실패 이유는 LLM 계층이 준 그대로 쓰고(1안 모드면 1개),
        # 세 안의 원인이 같으면 그 원인으로 응답한다 (예: 셋 다 한도 초과 → RATE_LIMITED).
        cause: LLMError = e
        failures: list[TierFailure] = []
        if isinstance(e, AllTiersFailedError):
            cause = e.common_cause or e
            failures = e.failures
        if not failures:
            failures = await _missing_tiers(repo, job_id, job, e.code)
        logger.error("job.failed job_id=%s code=%s err=%s", job_id, cause.code, e)
        await repo.finish_job(
            job_id,
            JobStatus.FAILED,
            failures=failures,
            error=to_error_response(llm_error_to_api(cause), request_id=job_id),
        )
        return
    except asyncio.CancelledError:
        # 서버 종료 등으로 취소되면 running에 멈추지 않도록 재시도 가능한 실패로 저장한 뒤 취소를 계속 전파한다.
        logger.warning("job.cancelled job_id=%s", job_id)
        try:
            await repo.finish_job(
                job_id,
                JobStatus.FAILED,
                failures=await _missing_tiers(repo, job_id, job, ErrorCode.INTERNAL_ERROR.value),
                error=to_error_response(
                    ApiError(
                        503,
                        ErrorCode.INTERNAL_ERROR,
                        "서버가 재시작되어 작업이 중단됐어요. 다시 시도해 주세요.",
                        retryable=True,
                    ),
                    request_id=job_id,
                ),
            )
        except Exception:
            logger.exception("job.cancel_save_failed job_id=%s", job_id)
        raise
    except Exception:
        logger.exception("job.crashed job_id=%s", job_id)
        await repo.finish_job(
            job_id,
            JobStatus.FAILED,
            failures=[],
            error=to_error_response(
                ApiError(
                    500, ErrorCode.INTERNAL_ERROR, "일시적인 오류가 발생했어요.", retryable=True
                ),
                request_id=job_id,
            ),
        )
        return

    await repo.finish_job(job_id, JobStatus.COMPLETED, failures=design_set.failures)


# ── ⑤ 노드 설명 ──────────────────────────────────────


def node_cost(design: DesignResult, node_id: str) -> NodeCost | None:
    cost = design.monthly_cost
    if cost is None:
        return None
    item: CostItem | None = next((i for i in cost.items if i.node_id == node_id), None)
    if item is None or item.amount is None or item.amount_usd is None:
        return None  # 계산 대상이 아니거나 사용량이 없는 노드
    return NodeCost(amount=item.amount, amount_usd=item.amount_usd, note=item.note)


async def get_explanation(
    repo: DesignRepository,
    design_id: str,
    node_id: str,
    user_id: str,
    *,
    explain: Callable[..., Awaitable[NodeExplanation]] = explain_node,
) -> NodeExplanation:
    job, design = await _get_owned_design(repo, design_id, user_id)

    cached = await repo.get_explanation(design_id, node_id)
    if cached is None:
        try:
            cached = await explain(job.requirements, design, node_id)
        except ValueError as e:  # 없는 노드 또는 사용자 노드
            raise not_found("설명할 수 있는 노드가 아니에요.") from e
        except LLMError as e:
            raise llm_error_to_api(e, explanation=True) from e
        await repo.put_explanation(design_id, node_id, cached)

    # 금액은 캐시하지 않고 매번 비용 계산 결과로 채운다 (설명에는 요금 숫자를 넣지 않음)
    return cached.model_copy(update={"monthly_cost": node_cost(design, node_id)})
