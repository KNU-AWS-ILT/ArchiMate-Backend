"""평가 스크립트(evals/llm/run_eval.py)가 고정 응답으로 끝까지 도는지 확인 (#25). 실제 LLM은 부르지 않는다."""

import importlib.util
import json
from pathlib import Path

from archimate.llm import LLMRateLimitError
from archimate.llm.providers.mock import MockProvider
from archimate.llm.settings import LLMSettings
from archimate.schemas import DesignDraft, Tier

ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location("run_eval", ROOT / "evals/llm/run_eval.py")
run_eval = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(run_eval)


def settings(**kwargs) -> LLMSettings:
    return LLMSettings(_env_file=None, provider="mock", retry_base_delay_s=0.01, **kwargs)


def test_scenarios_are_valid_answers():
    scenarios = run_eval.load_scenarios()
    assert len(scenarios) == 5
    assert len({s.id for s in scenarios}) == 5
    demo = json.loads(
        (ROOT / "src/archimate/llm/providers/fixtures/demo_answers.json").read_text("utf-8")
    )
    assert scenarios[0].requirements.model_dump(by_alias=True) == (
        run_eval.build_requirements(demo).model_dump(by_alias=True)
    )


async def test_eval_counts_success_and_retries(tmp_path):
    class FlakyOnce(MockProvider):
        """확장형 설계 첫 요청만 한도 초과."""

        failed = False

        async def generate_json(self, *, system, messages, output_model):
            if (
                issubclass(output_model, DesignDraft)
                and "<tier>scalable</tier>" in messages[0].content
                and not self.failed
            ):
                FlakyOnce.failed = True
                raise LLMRateLimitError("429")
            return await super().generate_json(
                system=system, messages=messages, output_model=output_model
            )

    scenarios = run_eval.load_scenarios(only=["demo_club_recruit"])
    runs, tiers, invalid = await run_eval.run_eval(
        scenarios, runs=2, provider=FlakyOnce(), settings=settings(), save_dir=tmp_path / "designs"
    )
    assert len(list((tmp_path / "designs").glob("demo_club_recruit_*_*.json"))) == 6
    assert [(r.tiers_ok, r.tiers_tried) for r in runs] == [(3, 3), (3, 3)]
    assert all(t.ok and t.guide_ok and t.validation_attempts == 1 for t in tiers)
    assert [t.transient_retries for t in tiers if t.tier == "scalable"] == [1, 0]
    # 월 비용도 기록한다 (데모 최소비용안은 예산 5만 원 안)
    minimal = [t for t in tiers if t.tier == "minimal_cost"]
    assert all(t.cost_krw is not None and t.over_budget is False for t in minimal)

    summary = run_eval.summarize(scenarios, runs, tiers, invalid, provider="mock", model="mock")
    assert "생성 성공률: 100% → 달성" in summary
    assert "## 월 비용 (단가 스냅샷 기준)" in summary
    run_eval.write_csv(tmp_path / "results.csv", tiers)
    assert (tmp_path / "results.csv").read_text("utf-8").count("\n") == 7  # 헤더 + 2회 × 3안


async def test_eval_records_failures():
    class AlwaysInvalid(MockProvider):
        async def generate_json(self, *, system, messages, output_model):
            if issubclass(output_model, DesignDraft):
                return "{}"
            return await super().generate_json(
                system=system, messages=messages, output_model=output_model
            )

    scenarios = run_eval.load_scenarios(only=["demo_club_recruit"])
    runs, tiers, invalid = await run_eval.run_eval(
        scenarios,
        runs=1,
        provider=AlwaysInvalid(),
        settings=settings(max_validation_retries=1, tiers=[Tier.MINIMAL_COST]),
    )
    assert runs[0].error == "DESIGN_GENERATION_FAILED"
    assert [(t.ok, t.reason, t.validation_attempts) for t in tiers] == [
        (False, "LLM_INVALID_OUTPUT", 2)
    ]
    assert invalid  # 검증 실패 메시지가 모인다
    summary = run_eval.summarize(scenarios, runs, tiers, invalid, provider="mock", model="mock")
    assert "미달" in summary and "`LLM_INVALID_OUTPUT`: 1회" in summary


async def test_eval_counts_filled_defaults():
    class MissingRdsHours(MockProvider):
        """균형안 RDS의 hoursPerMonth·storageGb를 빠뜨린다 (파이프라인이 기본값으로 채움)."""

        async def generate_json(self, *, system, messages, output_model):
            raw = await super().generate_json(
                system=system, messages=messages, output_model=output_model
            )
            if (
                issubclass(output_model, DesignDraft)
                and "<tier>balanced</tier>" in messages[0].content
            ):
                data = json.loads(raw)
                for n in data["architecture"]["nodes"]:
                    if n["serviceType"] == "rds":
                        n["usage"].pop("hoursPerMonth")
                        n["usage"].pop("storageGb")
                raw = json.dumps(data)
            return raw

    scenarios = run_eval.load_scenarios(only=["demo_club_recruit"])
    runs, tiers, invalid = await run_eval.run_eval(
        scenarios, runs=1, provider=MissingRdsHours(), settings=settings()
    )
    assert {t.tier: t.filled_defaults for t in tiers} == {
        "minimal_cost": 0,
        "balanced": 2,
        "scalable": 0,
    }
    summary = run_eval.summarize(scenarios, runs, tiers, invalid, provider="mock", model="mock")
    assert "| 기본값 채움 |" in summary
