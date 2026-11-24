"""요구사항에서 꼭 필요한 서비스 (#5). 자유 서술의 로그인·회원가입을 모델이 자주 빠뜨렸다."""

import json

import pytest

from archimate.llm import build_requirements, generate_design, prompts
from archimate.llm.features import check_required_services, required_services
from archimate.llm.providers.mock import MockProvider, load_fixture
from archimate.llm.settings import LLMSettings
from archimate.schemas import AwsService, DesignDraft, Tier


def req(free_text: str, data_storage=("key_value",)):
    data = {**json.loads(load_fixture("demo_answers.json")), "freeText": free_text}
    data["dataStorage"] = list(data_storage)
    return build_requirements(data)


@pytest.mark.parametrize(
    ("text", "storage", "services"),
    [
        ("회원가입, 상품 사진 업로드가 있어요", ("relational", "file_storage"), ["cognito", "s3"]),
        ("로그인, 설계 결과 저장이 필요해요", ("key_value",), ["cognito"]),
        ("회원 가입 후 예약해요", ("key_value",), ["cognito"]),
        ("로그인 없이 쓸 수 있어요", ("key_value",), []),
        ("회원가입은 필요 없어요", ("key_value",), []),
        ("동아리 회원 모집 사이트예요", ("key_value",), []),
    ],
)
def test_required_services(text, storage, services):
    assert [r.service.value for r in required_services(req(text, storage))] == services


def test_prompt_lists_required_features_only_when_needed():
    assert "<required_features>" in prompts.design_user_prompt(req("로그인 필요"), Tier.BALANCED)
    assert "<required_features>" not in prompts.design_user_prompt(
        req("동아리 사이트"), Tier.BALANCED
    )


def test_check_required_services():
    d = DesignDraft.model_validate_json(load_fixture("design_minimal_cost.json"))  # cognito 없음
    required = required_services(req("로그인 필요"))
    (error,) = check_required_services(d, required)
    assert "'로그인'" in error and "cognito" in error


async def test_design_without_cognito_is_retried():
    r = req("회원가입이 필요해요")
    data = json.loads(load_fixture("design_minimal_cost.json"))
    nodes, edges = data["architecture"]["nodes"], data["architecture"]["edges"]
    nodes.append({**nodes[1], "id": "auth", "serviceType": "cognito", "usage": None})
    edges.append({"from": "users", "to": "auth"})
    provider = MockProvider(responses=[load_fixture("design_minimal_cost.json"), json.dumps(data)])
    design = await generate_design(
        r,
        Tier.MINIMAL_COST,
        provider=provider,
        settings=LLMSettings(_env_file=None, provider="mock"),
    )
    assert AwsService.COGNITO in {n.service_type for n in design.architecture.nodes}
    assert "cognito 노드가 필요합니다" in provider.calls[1][2].content
