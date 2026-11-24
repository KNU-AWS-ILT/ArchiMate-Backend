"""비용 오차 검증 스크립트(evals/pricing/run_cost_check.py) — 케이스·계산·판정 (#25). 네트워크를 쓰지 않는다."""

import importlib.util
import json
from pathlib import Path

import pytest

from archimate.pricing import load_price_book
from archimate.schemas import PRICED_SERVICES

ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location(
    "run_cost_check", ROOT / "evals/pricing/run_cost_check.py"
)
run_cost_check = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(run_cost_check)


@pytest.fixture(scope="module")
def results():
    prices = run_cost_check.without_free_tier(load_price_book())
    return {r.case.id: r for r in run_cost_check.compute(run_cost_check.load_cases(), prices)}


def test_cases_cover_every_priced_service_once_at_least():
    cases = run_cost_check.load_cases()
    assert len({c.id for c in cases}) == len(cases)
    assert {c.service for c in cases} == set(PRICED_SERVICES)
    assert all(c.calculator_input for c in cases)


def test_every_case_is_priced(results):
    assert all(r.ours_usd is not None and r.ours_usd > 0 for r in results.values())


@pytest.mark.parametrize(
    ("case_id", "expected_usd"),
    [
        # 100만 건 × 0.2/백만 + (100만 × 0.2초 × 0.5GB) × 0.0000166667  (무료 한도 미포함)
        ("lambda_small", 1.87),
        # (0.052 × 730 + 50GB × 0.131) × 2 (Multi-AZ는 인스턴스·스토리지 모두 2배)
        ("rds_multi_az", 89.02),
        # 572GB × 0.12 + 600만 건 × 1.2/백만 (CloudFront 무료 1TB·1천만 건 미포함)
        ("cloudfront_asia", 75.84),
        # (0.5 × 0.04656 + 1GB × 0.00511) × 730 × 2
        ("fargate_tasks", 41.45),
    ],
)
def test_ours_matches_hand_calculation(results, case_id, expected_usd):
    assert results[case_id].ours_usd == expected_usd


def test_free_tier_is_not_subtracted_for_the_comparison():
    book = run_cost_check.without_free_tier(load_price_book())
    assert book.aws_lambda.free_requests == book.aws_lambda.free_gb_seconds == 0
    assert book.cloudfront.free_gb_transfer == book.cloudfront.free_requests == 0
    assert book.dynamodb.free_storage_gb == 0
    assert book.aws_lambda.per_million_requests == load_price_book().aws_lambda.per_million_requests


def _result(ours, calculator):
    case = run_cost_check.load_cases()[0]
    case = run_cost_check.Case(**{**case.__dict__, "calculator_usd": calculator})
    return run_cost_check.Result(case, ours, "")


def test_verdict_by_threshold():
    assert _result(10.0, None).verdict() == "입력 필요"
    assert _result(10.0, 10.0).verdict() == "통과"
    assert _result(11.9, 10.0).verdict() == "통과"  # +19%
    assert _result(12.1, 10.0).verdict() == "초과"  # +21%
    assert _result(8.0, 10.0).verdict() == "통과"  # -20%
    assert _result(None, 10.0).verdict() == "계산 안 됨"
    assert _result(12.1, 10.0).verdict(threshold_pct=30) == "통과"


def test_entered_calculator_value_is_compared(tmp_path, results):
    data = json.loads((ROOT / "evals/pricing/cases.json").read_text("utf-8"))
    data["cases"][0]["calculator"] = {"usd": 1.80, "by": "test", "date": "2026-11-25"}
    path = tmp_path / "cases.json"
    path.write_text(json.dumps(data, ensure_ascii=False), "utf-8")

    prices = run_cost_check.without_free_tier(load_price_book())
    out = run_cost_check.compute(run_cost_check.load_cases(path), prices)
    first = out[0]
    assert first.diff_pct == pytest.approx((1.87 - 1.80) / 1.80 * 100)
    report = run_cost_check.render(out, prices=prices)
    assert "| $1.87 | $1.80 | +3.9% | 통과 |" in report
    assert "Calculator 값 입력 1개" in report
    assert "입력한 사람: test" in report
