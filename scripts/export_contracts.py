"""팀 공유 계약 내보내기 → contracts/

    python scripts/export_contracts.py

- contracts/*.schema.json : JSON Schema (프론트 타입 생성, 요청·응답 검증용). 필드명 camelCase
- contracts/examples/*.json : 데모 시나리오 실제 예시 (프론트 화면 개발용, API A~D 응답 모양)
  monthlyCost는 개발용 단가 스냅샷(src/archimate/pricing/snapshots/)으로 계산한 값이다.
  design_job_partial_failed·design_job_failed는 실패 응답 형식(#14) 예시다.

src/archimate/schemas 또는 인터뷰 질문·고정 응답을 바꾸면 실행해서 같은 PR에 포함한다.
예시는 Mock 제공자로 실제 파이프라인을 돌려 만들므로 항상 스키마·검증을 통과한 데이터다.
"""

import asyncio
import json
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, TypeAdapter

from archimate.api import service
from archimate.api.service import node_cost
from archimate.llm import (
    INTERVIEW_QUESTIONS,
    AllTiersFailedError,
    LLMRateLimitError,
    build_requirements,
)
from archimate.llm.interview import InterviewQuestion
from archimate.llm.pipeline import explain_node, generate_designs
from archimate.llm.providers.mock import MockProvider, load_fixture
from archimate.llm.settings import LLMSettings
from archimate.pricing import estimate_monthly_cost
from archimate.schemas import (
    CreateDesignRequest,
    DesignJob,
    DesignJobAccepted,
    DesignResult,
    DesignSet,
    ErrorCode,
    ErrorResponse,
    JobStatus,
    NodeExplanation,
    OperationsGuide,
    Requirements,
    Tier,
    TierFailure,
)
from archimate.storage import InMemoryRepository, JobRecord

ROOT = Path(__file__).resolve().parents[1] / "contracts"
# (모델, 모드) — 요청은 validation, 응답은 serialization 기준 스키마
SCHEMAS: dict[str, tuple[type[BaseModel], Literal["validation", "serialization"]]] = {
    "requirements": (Requirements, "validation"),
    "create_design_request": (CreateDesignRequest, "validation"),
    "interview_question": (InterviewQuestion, "serialization"),
    "design_job_accepted": (DesignJobAccepted, "serialization"),
    "design_job": (DesignJob, "serialization"),
    "operations_guide": (OperationsGuide, "serialization"),
    "node_explanation": (NodeExplanation, "serialization"),
    "error_response": (ErrorResponse, "serialization"),
}
JOB_ID = "job_demo"  # 예시용. 실제 id 형식은 API·비용 담당이 정한다


def write_json(path: Path, data: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", "utf-8")
    print(f"wrote {path.relative_to(ROOT.parent)}")


async def run_job_with(req: Requirements, generate) -> DesignJob:
    """실제 service.run_design_job으로 돌려 실패 응답 예시를 만든다 (#14 형식)."""
    repo = InMemoryRepository()
    await repo.create_job(JobRecord(job_id=JOB_ID, user_id="demo", requirements=req))
    await service.run_design_job(repo, JOB_ID, generate=generate)
    return await service.get_job(repo, JOB_ID, "demo")


async def build_examples() -> dict[str, object]:
    answers = json.loads(load_fixture("demo_answers.json"))
    req = build_requirements(answers)
    provider, settings = MockProvider(), LLMSettings(provider="mock")
    design_set = await generate_designs(req, provider=provider, settings=settings)
    results = [
        DesignResult(
            id=f"{JOB_ID}_{d.tier.value}",
            monthly_cost=estimate_monthly_cost(d, monthly_budget=req.monthly_budget),
            **d.model_dump(by_alias=False),
        )
        for d in design_set.designs
    ]
    minimal = next(r for r in results if r.tier == Tier.MINIMAL_COST)
    explanation = await explain_node(req, minimal, "app_db", provider=provider, settings=settings)
    explanation = explanation.model_copy(
        update={"monthly_cost": node_cost(minimal, explanation.node_id)}
    )
    dump = lambda m: m.model_dump(mode="json")  # noqa: E731

    async def partial_failed(_req, *, on_tier_done):
        # 최소비용·균형은 성공, 확장형은 LLM 출력 검증 실패
        ok = [d for d in design_set.designs if d.tier != Tier.SCALABLE]
        for d in ok:
            await on_tier_done(d)
        failures = [TierFailure(tier=Tier.SCALABLE, reason="LLM_INVALID_OUTPUT")]
        return DesignSet(designs=ok, failures=failures)

    async def all_failed(_req, *, on_tier_done):
        # 세 안 모두 한도 초과: 안별 이유(LLM_RATE_LIMITED)와 공통 원인(error.code=RATE_LIMITED)이 함께 내려온다
        raise AllTiersFailedError(
            failures=[TierFailure(tier=t, reason="LLM_RATE_LIMITED") for t in Tier],
            causes=[LLMRateLimitError("한도 초과") for _ in Tier],
        )

    return {
        "interview_questions": {
            "questions": TypeAdapter(list[InterviewQuestion]).dump_python(
                INTERVIEW_QUESTIONS, mode="json", by_alias=True
            )
        },
        "create_design_request": {"answers": answers},
        "design_job_accepted": dump(DesignJobAccepted(job_id=JOB_ID)),
        "design_job_running": dump(
            DesignJob(job_id=JOB_ID, status=JobStatus.RUNNING, designs=[minimal])
        ),
        "design_job_completed": dump(
            DesignJob(job_id=JOB_ID, status=JobStatus.COMPLETED, designs=results)
        ),
        "design_job_partial_failed": dump(await run_job_with(req, partial_failed)),
        "design_job_failed": dump(await run_job_with(req, all_failed)),
        "operations_guide_minimal_cost": dump(minimal.operations_guide),
        "node_explanation_app_db": dump(explanation),
        "error_response": dump(
            ErrorResponse(
                code=ErrorCode.DESIGN_GENERATION_FAILED,
                message="구성도를 만들지 못했어요. 잠시 후 다시 시도해 주세요.",
                retryable=True,
                request_id="req_demo",
            )
        ),
    }


if __name__ == "__main__":
    # 이름이 바뀐 파일이 남지 않게 매번 새로 만든다 (openapi.json은 export_openapi.py 담당)
    for old in [*ROOT.glob("*.schema.json"), *ROOT.glob("examples/*.json")]:
        old.unlink()
    for name, (model, mode) in SCHEMAS.items():
        write_json(ROOT / f"{name}.schema.json", model.model_json_schema(mode=mode))
    for name, data in asyncio.run(build_examples()).items():
        write_json(ROOT / "examples" / f"{name}.json", data)
