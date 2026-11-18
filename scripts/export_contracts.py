"""팀 공유 계약 내보내기 → contracts/

    python scripts/export_contracts.py

- contracts/*.schema.json : JSON Schema (프론트 타입 생성, 요청·응답 검증용)
- contracts/examples/*.json : 데모 시나리오 실제 예시 (프론트 화면 개발용)

src/archimate/schemas 또는 인터뷰 질문·고정 응답을 바꾸면 실행해서 같은 PR에 포함한다.
예시는 Mock 제공자로 실제 파이프라인을 돌려 만들므로 항상 스키마·검증을 통과한 데이터다.
"""

import asyncio
import json
from pathlib import Path

from pydantic import BaseModel, TypeAdapter

from archimate.llm import INTERVIEW_QUESTIONS, build_requirements
from archimate.llm.interview import InterviewQuestion
from archimate.llm.pipeline import generate_designs, generate_operations_guide
from archimate.llm.providers.mock import MockProvider, load_fixture
from archimate.llm.settings import LLMSettings
from archimate.schemas import DesignSet, OperationsGuide, Requirements, Tier

ROOT = Path(__file__).resolve().parents[1] / "contracts"
SCHEMAS: dict[str, type[BaseModel]] = {
    "requirements": Requirements,
    "interview_question": InterviewQuestion,
    "design_set": DesignSet,
    "operations_guide": OperationsGuide,
}


def write_json(path: Path, data: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", "utf-8")
    print(f"wrote {path.relative_to(ROOT.parent)}")


async def build_examples() -> dict[str, object]:
    answers = json.loads(load_fixture("demo_answers.json"))
    req = build_requirements(answers)
    provider, settings = MockProvider(), LLMSettings(provider="mock")
    design_set = await generate_designs(req, provider=provider, settings=settings)
    minimal = next(d for d in design_set.designs if d.tier == Tier.MINIMAL_COST)
    guide = await generate_operations_guide(req, minimal, provider=provider, settings=settings)
    return {
        "interview_questions": TypeAdapter(list[InterviewQuestion]).dump_python(
            INTERVIEW_QUESTIONS, mode="json"
        ),
        "interview_answers": answers,
        "design_set": design_set.model_dump(mode="json"),
        "operations_guide_minimal_cost": guide.model_dump(mode="json"),
    }


if __name__ == "__main__":
    for name, model in SCHEMAS.items():
        write_json(ROOT / f"{name}.schema.json", model.model_json_schema())
    for name, data in asyncio.run(build_examples()).items():
        write_json(ROOT / "examples" / f"{name}.json", data)
