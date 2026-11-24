"""의미 검증(validation.py) — 스키마로 못 잡는 규칙. 메시지는 LLM 재시도 피드백으로 쓰인다 (#24)."""

import json

import pytest

from archimate.llm.providers.mock import load_fixture
from archimate.llm.validation import check_design, check_explanation, check_operations_guide
from archimate.schemas import AwsService, DesignDraft, ExplanationDraft, OperationsGuide


def design(mutate=None) -> DesignDraft:
    data = json.loads(load_fixture("design_minimal_cost.json"))
    if mutate:
        mutate(data["architecture"])
    return DesignDraft.model_validate(data)


def guide(mutate=None) -> OperationsGuide:
    data = json.loads(load_fixture("operations_guide.json"))
    if mutate:
        mutate(data)
    return OperationsGuide.model_validate(data)


def explanation(mutate=None) -> ExplanationDraft:
    data = json.loads(load_fixture("explanation.json"))
    if mutate:
        mutate(data)
    return ExplanationDraft.model_validate(data)


def node(arch: dict, node_id: str) -> dict:
    return next(n for n in arch["nodes"] if n["id"] == node_id)


def errors_of(d: DesignDraft) -> str:
    return "\n".join(check_design(d))


# ── 고정 응답은 모두 통과 ──


@pytest.mark.parametrize("tier", ["minimal_cost", "balanced", "scalable"])
def test_fixture_designs_are_valid(tier):
    d = DesignDraft.model_validate_json(load_fixture(f"design_{tier}.json"))
    assert check_design(d) == []
    assert check_operations_guide(guide(), d.architecture) == []


def test_fixture_explanation_is_valid():
    assert check_explanation(explanation(), AwsService.DYNAMODB) == []


# ── 설계 ──


def test_empty_nodes():
    assert check_design(design(lambda a: a.update(nodes=[], edges=[]))) == [
        "architecture.nodes가 비어 있습니다."
    ]


def test_id_format_and_duplicates():
    def mutate(a):
        node(a, "logs")["id"] = "Logs-1"
        a["groups"].append({**a["groups"][0], "id": "cdn"})

    errors = errors_of(design(mutate))
    assert "id 'Logs-1'는 영문 소문자·숫자·밑줄만" in errors
    assert "id 'cdn'가 중복됩니다." in errors


@pytest.mark.parametrize("count", [0, 2])
def test_exactly_one_users_node(count):
    def mutate(a):
        if count == 0:
            node(a, "users")["serviceType"] = "cloudfront"
        else:
            a["nodes"].append({**node(a, "users"), "id": "users2"})
            a["edges"].append({"from": "users2", "to": "cdn"})

    assert f"정확히 1개 있어야 합니다(현재 {count}개)" in errors_of(design(mutate))


def test_users_node_must_be_outside_groups():
    def mutate(a):
        node(a, "users")["groupId"] = "region_seoul"

    assert "사용자 노드 'users'의 groupId는 null" in errors_of(design(mutate))


def test_unknown_parent_and_cycle():
    def mutate(a):
        a["groups"] = [
            {**a["groups"][0], "id": "region_seoul", "parentId": "vpc"},
            {**a["groups"][0], "id": "vpc", "kind": "vpc", "parentId": "region_seoul"},
            {**a["groups"][0], "id": "subnet", "kind": "public_subnet", "parentId": "nowhere"},
        ]

    errors = errors_of(design(mutate))
    assert "parentId가 순환합니다" in errors
    assert "그룹 'subnet'의 parentId 'nowhere'가 존재하지 않습니다." in errors


def test_node_in_unknown_group():
    def mutate(a):
        node(a, "app_db")["groupId"] = "vpc_x"

    assert "노드 'app_db'의 groupId 'vpc_x'가 존재하지 않습니다." in errors_of(design(mutate))


def test_edges_to_unknown_or_self():
    def mutate(a):
        a["edges"] += [{"from": "api_lambda", "to": "ghost"}, {"from": "cdn", "to": "cdn"}]

    errors = errors_of(design(mutate))
    assert "'ghost'는 없는 노드입니다." in errors
    assert "edge cdn→cdn: 자기 자신을 가리킵니다." in errors


def test_unconnected_node_and_missing_entry():
    def mutate(a):
        a["edges"] = [e for e in a["edges"] if e["to"] != "logs" and e["from"] != "users"]

    errors = errors_of(design(mutate))
    assert "노드 'logs'가 어떤 edge에도 연결되지 않았습니다." in errors
    assert "사용자 진입점이 없습니다" in errors


def test_required_usage_uses_json_field_names():
    def mutate(a):
        node(a, "api_lambda")["usage"]["memoryMb"] = None
        node(a, "app_db")["usage"] = None

    errors = errors_of(design(mutate))
    assert "노드 'api_lambda'(lambda)의 usage.memoryMb가 필요합니다." in errors
    for field in ("storageGb", "monthlyReads", "monthlyWrites"):
        assert f"노드 'app_db'(dynamodb)의 usage.{field}가 필요합니다." in errors


# ── 운영 절차 ──


def test_guide_node_ids_must_exist_in_design():
    def mutate(g):
        g["phases"][0]["steps"][0]["nodeIds"] = ["app_db", "ghost"]

    errors = check_operations_guide(guide(mutate), design().architecture)
    assert errors == ["절차 'account_security'의 nodeIds에 없는 노드 'ghost'가 있습니다."]


def test_guide_needs_budget_step_and_checklists():
    def mutate(g):
        g["phases"] = [p for p in g["phases"] if p["key"] != "account"]
        g["checklists"] = [c for c in g["checklists"] if c["key"] == "cost"]

    errors = check_operations_guide(guide(mutate))
    assert "비용·예산 알람을 설정하는 절차가 없습니다. 최소 1개 추가하세요." in errors
    assert "checklists에 key가 'incident'인 체크리스트가 필요합니다." in errors


def test_guide_empty_phase_and_bad_ids():
    def mutate(g):
        g["phases"][1]["steps"] = []
        g["phases"][2]["key"] = "Web Deploy"

    errors = check_operations_guide(guide(mutate))
    assert "단계 'data'에 steps가 없습니다." in errors
    assert "key/id 'Web Deploy'는 영문 소문자·숫자·밑줄만" in "\n".join(errors)


# ── 노드 설명 ──


@pytest.mark.parametrize("service", ["dynamodb", "users"])
def test_alternative_cannot_be_same_service_or_users(service):
    def mutate(e):
        e["alternatives"][0]["serviceType"] = service

    errors = check_explanation(explanation(mutate), AwsService.DYNAMODB)
    assert errors == [f"대안 '{service}'는 쓸 수 없습니다. 다른 서비스를 고르세요."]


def test_explanation_needs_alternatives_and_cautions():
    def mutate(e):
        e["alternatives"] = []
        e["cautions"] = []

    errors = check_explanation(explanation(mutate), AwsService.DYNAMODB)
    assert "alternatives가 비어 있습니다. 대안을 1~2개 쓰세요." in errors
    assert "cautions가 비어 있습니다." in errors


@pytest.mark.parametrize(
    "text", ["월 $3 정도예요", "한 달에 4만 원쯤", "1,000원 이하", "5 USD", "약 2천원"]
)
def test_price_numbers_are_rejected(text):
    def mutate(e):
        e["cautions"].append(text)

    errors = check_explanation(explanation(mutate), AwsService.DYNAMODB)
    assert len(errors) == 1 and errors[0].startswith("요금 숫자를 쓰면 안 됩니다")


@pytest.mark.parametrize("text", ["요청 100만 건까지", "25GB 저장", "3초 안에 응답", "2025년 7월"])
def test_non_price_numbers_are_allowed(text):
    def mutate(e):
        e["cautions"].append(text)

    assert check_explanation(explanation(mutate), AwsService.DYNAMODB) == []
