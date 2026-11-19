"""LLM 파이프라인 수동 실행 (프롬프트 튜닝용).

    python scripts/llm/try_generate.py                       # .env 설정(기본 mock)으로 데모 시나리오 3안 + 운영 절차 생성
    LLM_PROVIDER=gemini python scripts/llm/try_generate.py --tier minimal_cost
    python scripts/llm/try_generate.py --explain app_db      # 첫 번째 안의 노드 설명까지 생성

결과는 out/ 아래 JSON으로 저장된다 (out/은 gitignore).
"""

import argparse
import asyncio
import json
import logging
import time
from pathlib import Path

from archimate.llm import build_requirements, explain_node, generate_designs
from archimate.llm.providers.mock import load_fixture
from archimate.llm.settings import get_settings
from archimate.schemas import Tier

# 기획서 9장 데모 시나리오: 동아리 모집 서비스, 1인 메이커, 월 5만 원
DEMO_ANSWERS = json.loads(load_fixture("demo_answers.json"))


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--answers", type=Path, help="인터뷰 답변 JSON 파일 (생략 시 데모)")
    parser.add_argument("--tier", choices=[t.value for t in Tier], action="append")
    parser.add_argument("--explain", metavar="NODE_ID", help="첫 번째 안에서 이 노드의 설명도 생성")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    s = get_settings()
    print(f"provider={s.provider} model={s.resolved_model}")

    answers = json.loads(args.answers.read_text("utf-8")) if args.answers else DEMO_ANSWERS
    req = build_requirements(answers)
    tiers = [Tier(t) for t in args.tier] if args.tier else list(Tier)

    out = Path("out")
    out.mkdir(exist_ok=True)
    started = time.perf_counter()
    result = await generate_designs(req, tiers=tiers)
    print(
        f"3안(+운영 절차) 생성 {time.perf_counter() - started:.1f}s, 성공 {len(result.designs)}, "
        f"실패 {[f.tier.value for f in result.failures]}"
    )
    (out / "designs.json").write_text(result.model_dump_json(indent=2), "utf-8")

    if args.explain and result.designs:
        started = time.perf_counter()
        exp = await explain_node(req, result.designs[0], args.explain)
        print(f"노드 설명 {time.perf_counter() - started:.1f}s")
        (out / "explanation.json").write_text(exp.model_dump_json(indent=2), "utf-8")
    print("결과: out/")


if __name__ == "__main__":
    asyncio.run(main())
