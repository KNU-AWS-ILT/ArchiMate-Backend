"""인스턴스 유형 허용 목록과 NAT·ElastiCache 사용량 (#5). 단가가 없는 유형은 월 비용이 비어서 막는다."""

import json

from archimate.llm import prompts
from archimate.llm.providers.mock import load_fixture
from archimate.llm.validation import allowed_instance_types, check_design, fill_usage_defaults
from archimate.pricing import load_price_book
from archimate.schemas import REQUIRED_USAGE_FIELDS, AwsService, DesignDraft


def scalable(mutate) -> DesignDraft:
    data = json.loads(load_fixture("design_scalable.json"))
    mutate({n["id"]: n for n in data["architecture"]["nodes"]})
    return DesignDraft.model_validate(data)


def test_allowed_types_come_from_price_snapshot():
    p = load_price_book()
    allowed = allowed_instance_types()
    assert set(allowed[AwsService.RDS]) == set(p.rds.instance_hourly)
    assert set(allowed[AwsService.ELASTICACHE]) == set(p.elasticache.instance_hourly)
    assert "db.t4g.micro" in prompts.design_system_prompt()


def test_unpriced_instance_type_is_rejected():
    def mutate(nodes):
        nodes["app_db"]["usage"]["instanceType"] = "db.r6g.xlarge"

    errors = check_design(scalable(mutate))
    assert len(errors) == 1
    assert "instanceType 'db.r6g.xlarge'은 쓸 수 없습니다" in errors[0]
    assert "db.t4g.micro" in errors[0]  # 고를 수 있는 목록을 알려 준다


def test_unpriced_service_instance_type_is_not_checked():
    def mutate(nodes):
        nodes["app"]["usage"]["instanceType"] = "vCPU 0.5, 1GB"  # Fargate는 단가 없음

    assert check_design(scalable(mutate)) == []


def test_nat_and_elasticache_usage_is_required_and_defaulted():
    assert REQUIRED_USAGE_FIELDS[AwsService.NAT_GATEWAY] == (
        "hours_per_month",
        "data_transfer_out_gb",
    )

    def mutate(nodes):
        nodes["nat"]["usage"] = None
        del nodes["cache"]["usage"]["hoursPerMonth"]
        del nodes["cache"]["usage"]["instanceCount"]

    d = scalable(mutate)
    filled = fill_usage_defaults(d)
    assert set(filled) >= {
        "nat.hoursPerMonth",
        "nat.dataTransferOutGb",
        "cache.hoursPerMonth",
        "cache.instanceCount",
    }
    assert check_design(d) == []
