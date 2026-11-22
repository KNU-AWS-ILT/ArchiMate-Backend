"""월 비용 계산 — 고정 단가표로 금액·무료 한도·합계 규칙을 확인한다."""

from datetime import date

import pytest

from archimate.llm.providers.mock import load_fixture
from archimate.pricing import PriceBook, estimate_monthly_cost, load_price_book
from archimate.schemas import (
    Architecture,
    AwsService,
    Design,
    DesignDraft,
    Difficulty,
    Node,
    Tier,
    Usage,
)

# 계산을 손으로 확인하기 쉬운 단가 (환율 1000원)
PRICES = PriceBook.model_validate(
    {
        "region": "test",
        "priceDate": "2026-11-01",
        "source": "test",
        "exchangeRate": {"usdKrw": 1000.0, "date": "2026-11-02", "source": "test"},
        "awsLambda": {
            "perMillionRequests": 0.2,
            "perGbSecond": 0.00001,
            "freeRequests": 1_000_000,
            "freeGbSeconds": 400_000,
        },
        "apiGateway": {"perMillionRequests": 3.5},
        "s3": {"perGbMonth": 0.025},
        "cloudfront": {
            "perGbTransfer": 0.1,
            "perMillionRequests": 1.0,
            "freeGbTransfer": 1024,
            "freeRequests": 10_000_000,
        },
        "dynamodb": {
            "perMillionReads": 0.125,
            "perMillionWrites": 0.625,
            "perGbMonth": 0.25,
            "freeStorageGb": 25,
        },
        "rds": {"perGbMonth": 0.1, "instanceHourly": {"db.t4g.micro": 0.02}},
        "ec2": {"instanceHourly": {"t4g.micro": 0.01}},
        "alb": {"perHour": 0.02},
        "natGateway": {"perHour": 0.05, "perGbProcessed": 0.05},
        "elasticache": {"instanceHourly": {"cache.t4g.micro": 0.02}},
    }
)


def make_design(*nodes: Node) -> Design:
    users = Node(id="users", service_type=AwsService.USERS, label="사용자", role="r", reason="r")
    return Design(
        tier=Tier.MINIMAL_COST,
        title="t",
        summary="s",
        difficulty=Difficulty(level=1, reason="r"),
        architecture=Architecture(nodes=[users, *nodes], edges=[], groups=[]),
        pros=["p"],
        cons=["c"],
        budget_note="b",
        scaling_path=["s"],
    )


def node(node_id: str, service: AwsService, **usage) -> Node:
    return Node(
        id=node_id,
        service_type=service,
        label=node_id,
        role="r",
        reason="r",
        usage=Usage(**usage) if usage else None,
    )


def cost_of(*nodes: Node, budget: int = 50_000):
    return estimate_monthly_cost(make_design(*nodes), monthly_budget=budget, prices=PRICES)


def test_rds_hourly_and_storage():
    c = cost_of(
        node(
            "db",
            AwsService.RDS,
            instance_type="db.t4g.micro",
            instance_count=1,
            storage_gb=20,
            hours_per_month=730,
            multi_az=False,
        )
    )
    # 0.02 × 730 + 20 × 0.1 = 16.6 USD → 16,600원
    assert c.items[0].amount_usd == 16.6
    assert c.items[0].amount == 16_600
    assert c.total == 16_600 and c.total_usd == 16.6
    assert c.currency == "KRW"
    assert c.exchange_rate == 1000.0
    assert c.exchange_rate_date == date(2026, 11, 2)
    assert c.price_date == date(2026, 11, 1)
    assert c.free_tier is False and c.free_tier_note is None


def test_multi_az_doubles_rds():
    single = dict(
        instance_type="db.t4g.micro", instance_count=1, storage_gb=20, hours_per_month=730
    )
    a = cost_of(node("db", AwsService.RDS, multi_az=False, **single))
    b = cost_of(node("db", AwsService.RDS, multi_az=True, **single))
    assert b.total == a.total * 2


def test_users_node_is_excluded():
    c = cost_of(node("site", AwsService.S3, storage_gb=4))
    assert [i.node_id for i in c.items] == ["site"]


def test_always_free_makes_zero():
    c = cost_of(
        node("fn", AwsService.LAMBDA, monthly_requests=200_000, avg_duration_ms=200, memory_mb=256),
        node("cdn", AwsService.CLOUDFRONT, monthly_requests=300_000, data_transfer_out_gb=5),
    )
    assert c.total == 0
    assert c.free_tier is True
    assert "Lambda·CloudFront" in c.free_tier_note and "0원" in c.free_tier_note
    assert all("무료 한도 적용" in i.note for i in c.items)


def test_free_allowance_is_shared_between_nodes():
    lam = dict(monthly_requests=800_000, avg_duration_ms=100, memory_mb=128)
    c = cost_of(node("a", AwsService.LAMBDA, **lam), node("b", AwsService.LAMBDA, **lam))
    a, b = c.items
    assert a.amount == 0
    # b: 무료 요청 20만 건만 남음 → 60만 건 × 0.2/100만 = 0.12 USD (GB-초는 한도 안)
    assert b.amount_usd == 0.12 and b.amount == 120


def test_total_equals_sum_of_rounded_items():
    # 각 0.0006 USD × 1000 = 0.6원 → 1원씩 반올림, 합계도 항목 합과 같아야 한다
    nodes = [node(f"s{i}", AwsService.S3, storage_gb=0.024) for i in range(3)]
    c = cost_of(*nodes)
    assert [i.amount for i in c.items] == [1, 1, 1]
    assert c.total == 3


def test_unpriced_and_missing_usage_are_null():
    c = cost_of(
        node("auth", AwsService.COGNITO),
        node("fn", AwsService.LAMBDA, monthly_requests=10),  # memory·duration 빠짐
        node("vm", AwsService.EC2, instance_type="x9.huge", instance_count=1, hours_per_month=730),
        node("site", AwsService.S3, storage_gb=40),
    )
    by_id = {i.node_id: i for i in c.items}
    assert by_id["auth"].amount is None and by_id["auth"].note == "비용 계산 대상이 아니에요"
    assert by_id["fn"].amount is None and "사용량" in by_id["fn"].note
    assert by_id["vm"].amount is None and "x9.huge" in by_id["vm"].note
    assert c.total == by_id["site"].amount == 1_000


@pytest.mark.parametrize(("budget", "over"), [(16_600, False), (16_599, True)])
def test_over_budget(budget, over):
    c = cost_of(
        node(
            "db",
            AwsService.RDS,
            instance_type="db.t4g.micro",
            instance_count=1,
            storage_gb=20,
            hours_per_month=730,
            multi_az=False,
        ),
        budget=budget,
    )
    assert c.over_budget is over


def test_json_is_camel_case():
    body = cost_of(node("site", AwsService.S3, storage_gb=40)).model_dump(mode="json")
    assert set(body) >= {"totalUsd", "exchangeRate", "exchangeRateDate", "overBudget", "freeTier"}
    assert set(body["items"][0]) == {"nodeId", "amount", "amountUsd", "note"}


@pytest.mark.parametrize("tier", list(Tier))
def test_snapshot_prices_all_fixture_designs(tier):
    draft = DesignDraft.model_validate_json(load_fixture(f"design_{tier.value}.json"))
    design = Design(tier=tier, **draft.model_dump(by_alias=False))
    c = estimate_monthly_cost(design, monthly_budget=50_000, prices=load_price_book())
    assert c.total == sum(i.amount for i in c.items if i.amount is not None)
    priced = [i for i in c.items if i.amount is not None]
    assert priced, "고정 응답 설계에서 계산된 노드가 하나도 없음"


def test_nat_gateway_hourly_and_processed_data():
    # 0.05 × 730시간 + 0.05 × 100GB = 36.5 + 5 = 41.5 USD → 41,500원
    c = cost_of(node("nat", AwsService.NAT_GATEWAY, hours_per_month=730, data_transfer_out_gb=100))
    assert c.items[0].amount_usd == 41.5 and c.items[0].amount == 41_500
    assert "처리 100GB" in c.items[0].note


def test_nat_gateway_count_multiplies_hourly_only():
    c = cost_of(
        node(
            "nat",
            AwsService.NAT_GATEWAY,
            instance_count=2,
            hours_per_month=730,
            data_transfer_out_gb=100,
        )
    )
    assert c.items[0].amount_usd == 78.0  # 0.05 × 730 × 2 + 0.05 × 100


def test_elasticache_hourly():
    c = cost_of(
        node(
            "cache",
            AwsService.ELASTICACHE,
            instance_type="cache.t4g.micro",
            instance_count=2,
            hours_per_month=730,
        )
    )
    assert c.items[0].amount_usd == 29.2 and c.items[0].amount == 29_200


def test_new_services_without_usage_or_price_are_null():
    c = cost_of(
        node("nat", AwsService.NAT_GATEWAY, hours_per_month=730),  # 처리량 빠짐
        node(
            "cache",
            AwsService.ELASTICACHE,
            instance_type="cache.r9.huge",
            instance_count=1,
            hours_per_month=730,
        ),
    )
    by_id = {i.node_id: i for i in c.items}
    assert by_id["nat"].amount is None and "사용량" in by_id["nat"].note
    assert by_id["cache"].amount is None and "cache.r9.huge" in by_id["cache"].note


def test_excluded_node_prevents_zero_won_claim():
    # 무료 한도 안이라 합계는 0원이어도, 계산에서 빠진 노드가 있으면 "월 0원"이라고 말하면 안 된다
    c = cost_of(
        node("fn", AwsService.LAMBDA, monthly_requests=200_000, avg_duration_ms=200, memory_mb=256),
        node("auth", AwsService.COGNITO),
    )
    assert c.total == 0
    assert c.free_tier is False
    assert "0원" not in c.free_tier_note
    assert "빠진 항목" in c.free_tier_note
