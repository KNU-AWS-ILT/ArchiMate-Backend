"""AWS Price List Query API로 단가를 가져온다 (Issue #55).

`pricing:GetProducts`(엔드포인트 us-east-1)에 필터를 걸어 필요한 상품만 받는다. 공개 Price List 파일
(RDS 약 21MB, EC2 약 290MB)을 내려받지 않아서 단가 동기화 Lambda(128MB·30초)에서도 돌아간다.

- 상품을 고르는 조건(usagetype·속성)은 단가 검증(verify.py)의 `specs`를 그대로 쓴다.
- Price List에 없는 값(무료 한도 수량, 환율, 인스턴스 유형 목록)은 패키지 스냅샷에서 가져온다.
- 하나라도 못 찾거나, 후보 단가가 여러 개거나, 스냅샷과 너무 크게 다르면(단위 실수 방지) 예외를 낸다.
  sync_prices는 예외가 나면 테이블에 쓰지 않으므로 전날 단가가 남는다.
- Price List API 호출 자체는 요금이 없다.
"""

import asyncio
import copy
import json
import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from typing import Any, Protocol

from archimate.pricing.prices import DEFAULT_REGION, PriceBook, load_price_book
from archimate.pricing.verify import (
    AmbiguousPriceError,
    PriceNotFoundError,
    Spec,
    find_price,
    specs,
)

logger = logging.getLogger(__name__)

PRICING_ENDPOINT_REGION = (
    "us-east-1"  # Price List API 엔드포인트 (ap-south-1도 있지만 us-east-1로 통일)
)
MAX_RESULTS = 100
MAX_WORKERS = 4  # 동시에 보내는 요청 수. Price List API 호출 한도에 걸리지 않게 작게 둔다
MAX_CHANGE_RATIO = 3.0  # 스냅샷 대비 3배 넘게 달라지면 단위 실수로 보고 멈춘다
KST = timezone(timedelta(hours=9))


class PricingClient(Protocol):
    """boto3 `pricing` 클라이언트에서 쓰는 메서드만."""

    def get_products(self, **kwargs: Any) -> dict[str, Any]: ...


class PriceListError(RuntimeError):
    """Price List에서 단가를 모두 가져오지 못했다. 테이블에는 쓰지 않는다."""

    def __init__(self, problems: list[str]) -> None:
        self.problems = problems
        super().__init__("Price List 단가를 가져오지 못했어요: " + "; ".join(problems))


def make_client() -> PricingClient:
    import boto3
    from botocore.config import Config

    config = Config(
        retries={"mode": "standard", "max_attempts": 5},
        connect_timeout=5,
        read_timeout=10,
    )
    return boto3.client("pricing", region_name=PRICING_ENDPOINT_REGION, config=config)


def filters_for(spec: Spec) -> list[dict[str, str]]:
    conditions = {"usagetype": spec.usagetype, **spec.attrs}
    return [{"Type": "TERM_MATCH", "Field": k, "Value": v} for k, v in conditions.items()]


def query_offer(client: PricingClient, spec: Spec) -> dict[str, Any]:
    """조건에 맞는 상품을 모두 받아 공개 Price List 파일과 같은 모양({products, terms})으로 만든다."""
    offer: dict[str, Any] = {"products": {}, "terms": {"OnDemand": {}}}
    kwargs: dict[str, Any] = {
        "ServiceCode": spec.code,
        "Filters": filters_for(spec),
        "FormatVersion": "aws_v1",
        "MaxResults": MAX_RESULTS,
    }
    while True:
        page = client.get_products(**kwargs)
        for raw in page.get("PriceList", []):
            item = json.loads(raw) if isinstance(raw, str) else raw
            sku = item["product"]["sku"]
            offer["products"][sku] = item["product"]
            on_demand = item.get("terms", {}).get("OnDemand")
            if on_demand:
                offer["terms"]["OnDemand"][sku] = on_demand
        token = page.get("NextToken")
        if not token:
            return offer
        kwargs["NextToken"] = token


def official_price(client: PricingClient, spec: Spec) -> float:
    return find_price(query_offer(client, spec), spec.usagetype, **spec.attrs) * spec.scale


def check_change(spec: Spec, new: float, max_ratio: float) -> str | None:
    """스냅샷 값과 비교해 이상하면 문제 설명을, 괜찮으면 None."""
    if new <= 0:
        return f"{spec.service} {spec.item}: 0 이하 ({new:g})"
    if spec.ours > 0 and max(new / spec.ours, spec.ours / new) > max_ratio:
        return f"{spec.service} {spec.item}: 스냅샷 {spec.ours:g} → {new:g} ({max_ratio:g}배 넘게 차이)"
    return None


def apply_prices(base: PriceBook, prices: dict[tuple[str, ...], float], **meta: Any) -> PriceBook:
    """base(스냅샷)에서 path 위치의 단가만 바꾼 새 PriceBook."""
    data = copy.deepcopy(base.model_dump(by_alias=False))
    for path, value in prices.items():
        target = data
        for key in path[:-1]:
            target = target[key]
        if path[-1] not in target:
            raise KeyError(f"PriceBook에 없는 위치예요: {path}")
        target[path[-1]] = value
    data.update(meta)
    return PriceBook.model_validate(data)


class PriceListFetcher:
    """Price List Query API로 서울 리전 단가를 가져온다 (sync.PriceFetcher 구현)."""

    def __init__(
        self,
        client: PricingClient | None = None,
        *,
        max_workers: int = MAX_WORKERS,
        max_change_ratio: float = MAX_CHANGE_RATIO,
        today: date | None = None,
    ) -> None:
        self._client = client
        self._max_workers = max_workers
        self._max_change_ratio = max_change_ratio
        self._today = today

    async def fetch(self, region: str) -> PriceBook:
        if region != DEFAULT_REGION:
            # usagetype 접두사(APN2-)가 서울 리전 기준이라 다른 리전은 조건을 따로 만들어야 한다
            raise ValueError(f"Price List 조회는 {DEFAULT_REGION}만 지원해요: {region}")
        return await asyncio.to_thread(self._fetch, load_price_book(region))

    def _fetch(self, base: PriceBook) -> PriceBook:
        client = self._client or make_client()
        items = specs(base, include_ec2=True)
        problems: list[str] = []
        prices: dict[tuple[str, ...], float] = {}

        def one(spec: Spec) -> tuple[Spec, float | None, str | None]:
            try:
                return spec, official_price(client, spec), None
            except (PriceNotFoundError, AmbiguousPriceError) as e:
                return spec, None, f"{spec.service} {spec.item}: {e}"

        with ThreadPoolExecutor(max_workers=self._max_workers) as pool:
            results = list(pool.map(one, items))

        for spec, value, problem in results:
            if problem is None and value is not None:
                problem = check_change(spec, value, self._max_change_ratio)
            if problem:
                problems.append(problem)
                continue
            assert value is not None
            prices[spec.path] = value
            if abs(value - spec.ours) > 1e-12:
                logger.info(
                    "prices.changed item=%s/%s old=%g new=%g",
                    spec.service,
                    spec.item,
                    spec.ours,
                    value,
                )
        if problems:
            raise PriceListError(problems)

        today = self._today or datetime.now(KST).date()
        return apply_prices(
            base,
            prices,
            price_date=today,
            source=(
                f"AWS Price List API(GetProducts) 서울 리전 온디맨드 단가 {len(prices)}개, {today} 동기화."
                " 무료 한도·환율·인스턴스 유형 목록은 패키지 스냅샷 값"
            ),
        )
