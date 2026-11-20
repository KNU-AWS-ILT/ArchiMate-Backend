"""`/api/v1` 라우트 — 요청 변환만 하고 로직은 service.py에 둔다.

operation_id는 프론트가 OpenAPI로 타입·클라이언트를 생성할 때 함수 이름이 된다.
"""

import json
from typing import Annotated

from fastapi import APIRouter, Body, Path, status

from archimate.api import service
from archimate.api.deps import Repo, Runner, UserId
from archimate.api.errors import error_responses
from archimate.llm.providers.mock import load_fixture
from archimate.schemas import (
    CreateDesignRequest,
    DesignJob,
    DesignJobAccepted,
    NodeExplanation,
    OperationsGuide,
)

router = APIRouter(prefix="/api/v1")

JobIdPath = Annotated[
    str, Path(alias="jobId", description="생성 작업 id (POST 응답의 jobId)", max_length=64)
]
DesignIdPath = Annotated[
    str,
    Path(
        alias="designId",
        description="설계안 id (`designs[].id`, 예: job_..._minimal_cost)",
        max_length=96,
    ),
]
NodeIdPath = Annotated[
    str, Path(alias="nodeId", description="노드 id (`architecture.nodes[].id`)", max_length=64)
]

# Swagger "Try it out" 기본 요청 = 데모 시나리오 (동아리 모집 서비스, 월 5만 원)
CREATE_DESIGN_EXAMPLES = {
    "demo": {
        "summary": "데모 시나리오: 동아리 모집 서비스",
        "value": {"answers": json.loads(load_fixture("demo_answers.json"))},
    }
}


@router.get("/health", tags=["system"], operation_id="getHealth", summary="헬스 체크")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@router.post(
    "/designs",
    tags=["designs"],
    operation_id="createDesign",
    summary="3안 생성 요청",
    description=(
        "인터뷰 답변으로 최소비용·균형·확장형 3안 생성을 시작하고 `jobId`를 바로 돌려준다.\n\n"
        "생성은 뒤에서 진행되므로(최대 약 3분) `GET /designs/jobs/{jobId}`로 상태를 조회한다."
    ),
    status_code=status.HTTP_202_ACCEPTED,
    response_model=DesignJobAccepted,
    responses=error_responses(400, 500),
)
async def create_design(
    body: Annotated[CreateDesignRequest, Body(openapi_examples=CREATE_DESIGN_EXAMPLES)],
    repo: Repo,
    runner: Runner,
    user_id: UserId,
) -> DesignJobAccepted:
    job_id = await service.create_job(repo, user_id, body.answers)
    runner.submit(job_id)
    return DesignJobAccepted(job_id=job_id)


@router.get(
    "/designs/jobs/{jobId}",
    tags=["designs"],
    operation_id="getDesignJob",
    summary="생성 작업 상태 조회",
    description=(
        "2~3초마다 호출한다. `status`가 `completed` 또는 `failed`가 되면 조회를 멈춘다.\n\n"
        "- `designs`: 끝난 안부터 채워진다\n"
        "- 일부 안만 실패: `status=completed`, 실패한 안은 `failures`\n"
        "- 모두 실패: `status=failed`, `error`에 사유 (HTTP는 200)"
    ),
    response_model=DesignJob,
    responses=error_responses(404),
)
async def get_design_job(job_id: JobIdPath, repo: Repo, user_id: UserId) -> DesignJob:
    return await service.get_job(repo, job_id, user_id)


@router.get(
    "/designs/{designId}/guide",
    tags=["designs"],
    operation_id="getOperationsGuide",
    summary="배포·운영 절차 조회",
    description="3안 생성 때 함께 만든 절차를 조회만 한다. 생성 응답의 `designs[].operationsGuide`와 같은 값.",
    response_model=OperationsGuide,
    responses=error_responses(404),
)
async def get_operations_guide(
    design_id: DesignIdPath, repo: Repo, user_id: UserId
) -> OperationsGuide:
    return await service.get_guide(repo, design_id, user_id)


@router.get(
    "/designs/{designId}/nodes/{nodeId}/explanation",
    tags=["designs"],
    operation_id="getNodeExplanation",
    summary="노드 설명 조회",
    description=(
        "노드를 클릭하면 '왜 이 서비스인가 / 대안 / 주의점'을 돌려준다. "
        "처음 한 번은 AI가 생성하고(수 초) 이후에는 캐시를 쓴다."
    ),
    response_model=NodeExplanation,
    responses=error_responses(404, 429, 502, 504),
)
async def get_node_explanation(
    design_id: DesignIdPath, node_id: NodeIdPath, repo: Repo, user_id: UserId
) -> NodeExplanation:
    return await service.get_explanation(repo, design_id, node_id, user_id)
