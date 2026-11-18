"""LLM 모듈 공개 API — 백엔드 API 계층은 여기 있는 이름만 import 한다.

사용 예 (FastAPI 라우터, 백엔드 담당):

    from archimate.llm import INTERVIEW_QUESTIONS, build_requirements, generate_designs

    @router.get("/interview/questions")
    async def questions():
        return INTERVIEW_QUESTIONS

    @router.post("/designs")
    async def create_designs(answers: dict):
        req = build_requirements(answers)          # ValidationError → 400
        design_set = await generate_designs(req)   # LLMError → errors.py 매핑표 참고
        ...비용 계산 결과를 붙여 반환...
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
    generate_design,
    generate_designs,
    generate_operations_guide,
    get_default_provider,
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
    "generate_design",
    "generate_designs",
    "generate_operations_guide",
    "get_default_provider",
]
