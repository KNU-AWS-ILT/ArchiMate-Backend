"""단가 검증(pricing/verify.py) — 작은 가짜 Price List로 확인한다. 네트워크를 쓰지 않는다 (#25)."""

import pytest

from archimate.pricing import load_price_book
from archimate.pricing.verify import (
    AmbiguousPriceError,
    Check,
    PriceNotFoundError,
    build_checks,
    find_price,
    first_paid_price,
    render_report,
    specs,
)


def offer(*products: tuple[str, dict, list[tuple[float, float]]]) -> dict:
    """(sku, attributes, [(beginRange, USD), ...]) 로 Price List 모양의 dict를 만든다."""
    data: dict = {"products": {}, "terms": {"OnDemand": {}}}
    for sku, attrs, tiers in products:
        data["products"][sku] = {"sku": sku, "attributes": attrs}
        dims = {
            f"{sku}.d{i}": {"beginRange": str(begin), "pricePerUnit": {"USD": str(usd)}}
            for i, (begin, usd) in enumerate(tiers)
        }
        data["terms"]["OnDemand"][sku] = {f"{sku}.t": {"priceDimensions": dims}}
    return data


def test_first_paid_price_skips_free_tier_and_uses_first_paid_step():
    o = offer(("A", {"usagetype": "X"}, [(1_000_000, 0.0000002), (0, 0.0), (5_000_000, 0.0000001)]))
    assert first_paid_price(o, "A") == 0.0000002


def test_find_price_filters_by_attributes():
    o = offer(
        ("mysql", {"usagetype": "DB", "databaseEngine": "MySQL"}, [(0, 0.026)]),
        ("pg", {"usagetype": "DB", "databaseEngine": "PostgreSQL"}, [(0, 0.028)]),
    )
    assert find_price(o, "DB", databaseEngine="PostgreSQL") == 0.028


def test_find_price_rejects_ambiguous_and_missing():
    o = offer(
        ("a", {"usagetype": "DB", "databaseEngine": "MySQL"}, [(0, 0.026)]),
        ("b", {"usagetype": "DB", "databaseEngine": "PostgreSQL"}, [(0, 0.028)]),
    )
    with pytest.raises(AmbiguousPriceError):
        find_price(o, "DB")  # 엔진을 지정하지 않으면 값이 둘
    with pytest.raises(PriceNotFoundError):
        find_price(o, "DB", databaseEngine="Oracle")


def test_same_price_in_several_products_is_not_ambiguous():
    o = offer(
        ("a", {"usagetype": "S", "operation": "x"}, [(0, 0.131)]),
        ("b", {"usagetype": "S", "operation": "y"}, [(0, 0.131)]),
    )
    assert find_price(o, "S") == 0.131


def test_check_status_by_threshold():
    assert Check("s", "i", 1.0, 1.0).status() == "일치"
    assert Check("s", "i", 1.1, 1.0).status() == "차이"  # +10%
    assert Check("s", "i", 1.3, 1.0).status() == "초과"  # +30%
    assert Check("s", "i", 1.3, 1.0).status(threshold_pct=50) == "차이"
    assert Check("s", "i", 1.0, None, "없음").status() == "미확인"


def test_build_checks_scales_per_request_prices_and_reports_missing():
    book = load_price_book()
    lambda_offer = offer(
        ("req", {"usagetype": "APN2-Request"}, [(0, 0.0000002)]),
        # 실행 시간(APN2-Lambda-GB-Second)은 일부러 빼서 '미확인'이 되게 한다
    )
    checks = build_checks(book, {"AWSLambda": lambda_offer})
    by_item = {c.item: c for c in checks}
    assert {c.service for c in checks} == {"Lambda"}  # 다른 서비스 파일은 없어서 건너뜀
    assert by_item["요청 (100만 건당)"].official == pytest.approx(0.2)  # 1건 가격 × 100만
    assert by_item["요청 (100만 건당)"].status() == "일치"
    assert by_item["실행 시간 (GB-초당)"].status() == "미확인"


def test_every_snapshot_price_has_a_spec():
    """PriceBook에 단가를 추가하면 대조 목록에도 넣어야 한다 (EC2·NAT·환율·무료 한도 제외)."""
    book = load_price_book()
    items = {(s.service, s.item) for s in specs(book)}
    instance_items = len(book.rds.instance_hourly) + len(book.elasticache.instance_hourly)
    assert len(items) == len(specs(book)) == 13 + instance_items


def test_price_list_specs_cover_every_price_with_a_unique_path():
    """Price List API로 바꿀 단가 위치(path)는 EC2·NAT Gateway까지 빠짐없고 겹치지 않는다 (#55)."""
    book = load_price_book()
    all_specs = specs(book, include_ec2=True)
    paths = [s.path for s in all_specs]
    assert len(set(paths)) == len(paths) and all(paths)
    data = book.model_dump(by_alias=False)
    for spec in all_specs:
        value = data
        for key in spec.path:
            value = value[key]
        assert value == spec.ours
    extra = len(book.ec2.instance_hourly) + 2  # EC2 인스턴스 + NAT Gateway 2개
    assert len(all_specs) == len(specs(book)) + extra == 33


def test_report_lists_counts_and_unchecked_items():
    report = render_report(
        [Check("Lambda", "요청", 0.2, 0.2), Check("S3", "저장", 0.03, 0.025)],
        price_date="2026-11-20",
    )
    assert "| Lambda | 요청 | 0.2 | 0.2 | +0.0% | 일치 |" in report
    assert "+20.0%" in report  # 0.03 vs 0.025
    assert "일치 1 · 차이 1 · 초과 0 · 미확인 0" in report
    assert "EC2 인스턴스·NAT Gateway" in report
