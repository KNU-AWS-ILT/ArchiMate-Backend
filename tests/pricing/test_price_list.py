"""Price List Query API 단가 가져오기 (#55). 실제 AWS를 부르지 않고 GetProducts 응답 모양을 흉내 낸다."""

import asyncio
import json
from datetime import date

import pytest

from archimate.pricing import load_price_book, sync_prices
from archimate.pricing.price_list import PriceListError, PriceListFetcher, filters_for
from archimate.pricing.verify import EC2_LINUX_ATTRS, specs

TODAY = date(2026, 11, 25)


def product(sku: str, attributes: dict[str, str], usd: float) -> dict:
    """GetProducts PriceList 항목 하나 (aws_v1 형식)."""
    return {
        "product": {"sku": sku, "attributes": attributes},
        "terms": {
            "OnDemand": {
                f"{sku}.JRTCKXETXF": {
                    "priceDimensions": {
                        f"{sku}.JRTCKXETXF.6YS6EN2CT7": {
                            "beginRange": "0",
                            "pricePerUnit": {"USD": f"{usd:.10f}"},
                        }
                    }
                }
            }
        },
    }


class FakePricingClient:
    """TERM_MATCH 필터로 상품을 골라 JSON 문자열 목록으로 돌려준다. page_size로 NextToken을 흉내 낸다."""

    def __init__(self, catalog: dict[str, list[dict]], page_size: int = 100) -> None:
        self.catalog = catalog  # 서비스 코드 → 상품 목록
        self.page_size = page_size
        self.calls: list[dict] = []

    @classmethod
    def matching_snapshot(cls, overrides: dict[str, float] | None = None, **kwargs):
        """스냅샷 단가와 같은 값의 상품 + 조건이 다른 미끼 상품. overrides: usagetype → 바꿀 단가(Price List 단위)."""
        book = load_price_book()
        catalog: dict[str, list[dict]] = {}
        for i, spec in enumerate(specs(book, include_ec2=True)):
            usd = spec.ours / spec.scale
            if overrides and spec.usagetype in overrides:
                usd = overrides[spec.usagetype]
            attrs = {"usagetype": spec.usagetype, **spec.attrs}
            items = catalog.setdefault(spec.code, [])
            items.append(product(f"SKU{i:03d}", attrs, usd))
            # 같은 usagetype이지만 속성 하나가 다른 상품 (예: EC2 전용 테넌시). 필터가 걸러야 한다
            if spec.attrs:
                key = next(iter(spec.attrs))
                decoy = {**attrs, key: "Other"}
                items.append(product(f"DECOY{i:03d}", decoy, usd * 10))
        return cls(catalog, **kwargs)

    def get_products(self, **kwargs):
        self.calls.append(kwargs)
        wanted = {f["Field"]: f["Value"] for f in kwargs["Filters"]}
        assert all(f["Type"] == "TERM_MATCH" for f in kwargs["Filters"])
        matches = [
            p
            for p in self.catalog.get(kwargs["ServiceCode"], [])
            if all(p["product"]["attributes"].get(k) == v for k, v in wanted.items())
        ]
        start = int(kwargs.get("NextToken") or 0)
        page = matches[start : start + self.page_size]
        out = {"PriceList": [json.dumps(p) for p in page], "FormatVersion": "aws_v1"}
        if start + self.page_size < len(matches):
            out["NextToken"] = str(start + self.page_size)
        return out


def fetch(client, **kwargs):
    return asyncio.run(PriceListFetcher(client, today=TODAY, **kwargs).fetch("ap-northeast-2"))


def test_fetch_builds_book_from_price_list():
    snapshot = load_price_book()
    client = FakePricingClient.matching_snapshot()
    book = fetch(client)

    assert book.price_date == TODAY
    assert book.source.startswith("AWS Price List API")
    # 가격은 스냅샷과 같고(같은 값을 넣었으므로), Price List에 없는 값은 스냅샷 그대로
    assert _same_prices(book, snapshot)
    assert book.exchange_rate == snapshot.exchange_rate
    assert book.aws_lambda.free_requests == snapshot.aws_lambda.free_requests
    assert book.cloudfront.free_gb_transfer == snapshot.cloudfront.free_gb_transfer
    assert book.dynamodb.free_storage_gb == snapshot.dynamodb.free_storage_gb
    # EC2·NAT Gateway까지 모두 조회한다
    assert len(client.calls) == len(specs(snapshot, include_ec2=True))
    assert {c["ServiceCode"] for c in client.calls} >= {"AmazonEC2", "AmazonRDS", "AWSLambda"}


def _same_prices(a, b) -> bool:
    for spec in specs(b, include_ec2=True):
        value = a.model_dump(by_alias=False)
        for key in spec.path:
            value = value[key]
        if value != spec.ours:  # 근사가 아니라 정확히 같아야 한다 (부동소수 오차 제거, PR #56 리뷰)
            return False
    return True


def test_per_million_prices_have_no_float_noise():
    # Price List는 건당 가격(0.0000002)이라 × 100만 하면 0.19999999999999998이 된다
    assert 0.0000002 * 1_000_000 != 0.2
    book = fetch(FakePricingClient.matching_snapshot({"APN2-Request": 0.0000002}))
    assert book.aws_lambda.per_million_requests == 0.2
    assert book.dynamodb.per_million_reads == load_price_book().dynamodb.per_million_reads


def test_changed_prices_are_applied():
    client = FakePricingClient.matching_snapshot(
        {"APN2-InstanceUsage:db.t3.small": 0.055, "APN2-NatGateway-Hours": 0.062}
    )
    book = fetch(client)
    assert book.rds.instance_hourly["db.t3.small"] == pytest.approx(0.055)
    assert book.nat_gateway.per_hour == pytest.approx(0.062)
    # 100만 건당으로 바꾸는 항목도 단위가 맞다 (Price List는 요청 1건 가격)
    assert book.api_gateway.per_million_requests == pytest.approx(3.5)


def test_filters_use_usagetype_and_attributes():
    ec2 = next(s for s in specs(load_price_book(), include_ec2=True) if s.code == "AmazonEC2")
    fields = {f["Field"]: f["Value"] for f in filters_for(ec2)}
    assert fields == {"usagetype": ec2.usagetype, **EC2_LINUX_ATTRS}

    client = FakePricingClient.matching_snapshot()
    fetch(client)
    call = client.calls[0]
    assert call["FormatVersion"] == "aws_v1" and call["MaxResults"] == 100


def test_follows_next_token():
    # 같은 조건·같은 단가의 상품이 여러 페이지에 걸쳐 있어도 끝까지 읽는다
    client = FakePricingClient.matching_snapshot(page_size=1)
    spec = specs(load_price_book())[0]
    client.catalog[spec.code].append(
        product("DUP", {"usagetype": spec.usagetype, **spec.attrs}, spec.ours / spec.scale)
    )
    fetch(client)
    tokens = [c.get("NextToken") for c in client.calls if c["Filters"] == filters_for(spec)]
    assert tokens == [None, "1"]


def test_missing_price_fails_without_writing():
    client = FakePricingClient.matching_snapshot()
    client.catalog["AmazonElastiCache"] = []

    class Store:
        def __init__(self):
            self.puts = []

        async def get(self, region):
            return None

        async def put(self, book):
            self.puts.append(book)

    store = Store()
    fetcher = PriceListFetcher(client, today=TODAY)
    with pytest.raises(PriceListError) as e:
        asyncio.run(sync_prices(store, fetcher))
    assert store.puts == []  # 테이블은 그대로라 전날 단가가 남는다
    assert len(e.value.problems) == len(load_price_book().elasticache.instance_hourly)
    assert all(p.startswith("ElastiCache") for p in e.value.problems)


def test_ambiguous_price_fails():
    client = FakePricingClient.matching_snapshot()
    spec = specs(load_price_book())[0]
    client.catalog[spec.code].append(
        product("OTHER", {"usagetype": spec.usagetype, **spec.attrs}, 999.0)
    )
    with pytest.raises(PriceListError, match="여러 개"):
        fetch(client)


def test_large_change_is_treated_as_unit_mistake():
    # 100만 건당 값을 1건 가격 자리에 넣은 것 같은 실수: 스냅샷 대비 3배 넘게 차이 나면 멈춘다
    client = FakePricingClient.matching_snapshot({"APN2-Request": 0.2})
    with pytest.raises(PriceListError, match="3배"):
        fetch(client)
    # 기준을 넓히면 통과한다
    book = fetch(client, max_change_ratio=1e9)
    assert book.aws_lambda.per_million_requests == pytest.approx(200_000)


def test_zero_price_fails():
    client = FakePricingClient.matching_snapshot({"APN2-LoadBalancerUsage": 0.0})
    with pytest.raises(PriceListError, match="ALB"):
        fetch(client)


def test_only_seoul_region():
    with pytest.raises(ValueError, match="ap-northeast-2"):
        asyncio.run(PriceListFetcher(FakePricingClient({})).fetch("us-east-1"))
