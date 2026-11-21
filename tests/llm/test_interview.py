"""가이드형 인터뷰: 질문 정의와 답변 검증."""

import json

import pytest
from pydantic import ValidationError

from archimate.llm import INTERVIEW_QUESTIONS, build_requirements
from archimate.llm.interview import option_label
from archimate.llm.providers.mock import load_fixture
from archimate.schemas import Requirements
from archimate.schemas.requirements import (
    AvailabilityNeed,
    AwsExperience,
    DataStorageNeed,
    ServiceType,
    TrafficPattern,
    UserScale,
)


@pytest.fixture
def answers() -> dict:
    return json.loads(load_fixture("demo_answers.json"))


def errors_of(answers: dict) -> str:
    with pytest.raises(ValidationError) as e:
        build_requirements(answers)
    return str(e.value)


# ── 질문 정의 ──


def test_question_count_is_within_product_range():
    # 기획서: 5~7개 질문 + 자유 서술
    choice_questions = [q for q in INTERVIEW_QUESTIONS if q.id != "freeText"]
    assert 5 <= len(choice_questions) <= 7
    assert INTERVIEW_QUESTIONS[-1].id == "freeText"
    assert INTERVIEW_QUESTIONS[-1].required is False


def test_question_ids_match_requirements_json_fields():
    aliases = [f.alias for f in Requirements.model_fields.values()]
    assert [q.id for q in INTERVIEW_QUESTIONS] == aliases


def test_required_flags_follow_schema_defaults():
    # 기본값이 없는 필드 = 필수 질문
    for q in INTERVIEW_QUESTIONS:
        field = next(f for f in Requirements.model_fields.values() if f.alias == q.id)
        assert q.required == field.is_required(), q.id


@pytest.mark.parametrize(
    ("qid", "enum"),
    [
        ("serviceType", ServiceType),
        ("concurrentUsers", UserScale),
        ("awsExperience", AwsExperience),
        ("dataStorage", DataStorageNeed),
        ("trafficPattern", TrafficPattern),
        ("availability", AvailabilityNeed),
    ],
)
def test_choice_options_cover_enum(qid, enum):
    q = next(q for q in INTERVIEW_QUESTIONS if q.id == qid)
    assert [o.value for o in q.options] == [e.value for e in enum]
    assert all(o.label for o in q.options)


def test_questions_serialize_as_camel_case():
    data = INTERVIEW_QUESTIONS[0].model_dump(mode="json")
    assert "inputType" in data and "input_type" not in data


def test_option_label_returns_korean_label():
    assert option_label("awsExperience", "none").startswith("처음이에요")
    assert option_label("unknownQuestion", "x") == "x"


# ── 답변 검증 ──


def test_demo_answers_are_valid(answers):
    req = build_requirements(answers)
    assert req.monthly_budget == 50000
    assert req.data_storage == [DataStorageNeed.KEY_VALUE]


def test_optional_answers_use_defaults(answers):
    for key in ("trafficPattern", "availability", "freeText"):
        answers.pop(key)
    req = build_requirements(answers)
    assert req.traffic_pattern == TrafficPattern.UNKNOWN
    assert req.availability == AvailabilityNeed.STANDARD
    assert req.free_text is None


@pytest.mark.parametrize(
    "key", ["serviceType", "concurrentUsers", "monthlyBudget", "awsExperience", "dataStorage"]
)
def test_required_answers_missing(answers, key):
    answers.pop(key)
    assert key in errors_of(answers)


@pytest.mark.parametrize("budget", [-1, 100_000_001])
def test_budget_out_of_range(answers, budget):
    answers["monthlyBudget"] = budget
    assert "monthlyBudget" in errors_of(answers)


def test_budget_zero_is_allowed(answers):
    answers["monthlyBudget"] = 0
    assert build_requirements(answers).monthly_budget == 0


def test_unknown_option_value(answers):
    answers["serviceType"] = "blockchain"
    assert "serviceType" in errors_of(answers)


def test_data_storage_none_must_be_alone(answers):
    answers["dataStorage"] = ["none", "file_storage"]
    assert "'없음'은 다른 항목과 함께 고를 수 없습니다" in errors_of(answers)


def test_data_storage_no_duplicates(answers):
    answers["dataStorage"] = ["key_value", "key_value"]
    assert "중복" in errors_of(answers)


def test_data_storage_empty(answers):
    answers["dataStorage"] = []
    assert "dataStorage" in errors_of(answers)


def test_free_text_blank_becomes_none(answers):
    answers["freeText"] = "   \n "
    assert build_requirements(answers).free_text is None


def test_free_text_is_trimmed(answers):
    answers["freeText"] = "  동아리 모집 사이트  "
    assert build_requirements(answers).free_text == "동아리 모집 사이트"


def test_free_text_too_long(answers):
    answers["freeText"] = "가" * 1001
    assert "freeText" in errors_of(answers)


def test_snake_case_names_also_accepted_in_python():
    req = Requirements(
        service_type="web_app",
        concurrent_users="under_100",
        monthly_budget=0,
        aws_experience="none",
        data_storage=["none"],
    )
    assert req.model_dump()["serviceType"] == "web_app"
