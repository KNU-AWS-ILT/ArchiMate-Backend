"""LLM 모듈 공개 API — 백엔드 API 계층은 여기 있는 이름만 import 한다.

사용 예 (설계 생성 Lambda의 비동기 작업 부분):

    from archimate.llm import build_requirements, generate_designs

    req = build_requirements(answers)                 # ValidationError → 400 INVALID_REQUEST

    async def save(design):                           # 안 하나가 끝날 때마다 호출
        cost = calculate_monthly_cost(design)         # 비용 계산 (API·비용 담당)
        await repo.save_tier(job_id, design, cost)    # 저장 → 프론트가 끝난 안부터 조회

    await generate_designs(req, on_tier_done=save)    # LLMError → errors.py 매핑표 참고

노드 설명 Lambda:  await explain_node(req, design, node_id)  # 캐시는 호출하는 쪽에서
"""

from archimate.llm.errors import (
    AllTiersFailedError,
    LLMConfigError,
    LLMError,
    LLMOutputError,
    LLMProviderError,
    LLMRateLimitError,
    LLMTimeoutError,
)
from archimate.llm.interview import INTERVIEW_QUESTIONS, InterviewQuestion, build_requirements
from archimate.llm.pipeline import (
    explain_node,
    generate_design,
    generate_designs,
    generate_operations_guide,
    get_default_provider,
    get_explain_provider,
)

__all__ = [
    "INTERVIEW_QUESTIONS",
    "AllTiersFailedError",
    "InterviewQuestion",
    "LLMConfigError",
    "LLMError",
    "LLMOutputError",
    "LLMProviderError",
    "LLMRateLimitError",
    "LLMTimeoutError",
    "build_requirements",
    "explain_node",
    "generate_design",
    "generate_designs",
    "generate_operations_guide",
    "get_default_provider",
    "get_explain_provider",
]
