"""월 비용 계산 — 노드별 사용량(Usage) × 단가(PriceBook) → MonthlyCost (Issue #10 C안).

규칙
- 금액은 '항상 무료' 한도를 뺀 실제 청구 예상액이다. 같은 서비스 노드가 여럿이면 한도를 나눠 쓴다.
  2025년 7월 이후 가입 계정은 12개월 무료 대신 가입 크레딧을 받으므로, 12개월 무료·크레딧은 반영하지 않는다.
- 노드별 USD를 원으로 바꿔 원 단위로 반올림한 뒤 더해 total을 만든다 → total == items[].amount 합.
- 계산 대상이 아닌 서비스, 사용량이 빠진 노드, 단가가 없는 인스턴스 유형은 amount=null + note.
"""

from collections.abc import Callable
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal

from archimate.pricing.prices import PriceBook, load_price_book
from archimate.schemas import (
    PRICED_SERVICES,
    REQUIRED_USAGE_FIELDS,
    AwsService,
    CostItem,
    Design,
    MonthlyCost,
    Usage,
)

FREE_TIER_LABELS = {
    AwsService.LAMBDA: "Lambda",
    AwsService.CLOUDFRONT: "CloudFront",
    AwsService.DYNAMODB: "DynamoDB",
}

NOTE_UNPRICED = "비용 계산 대상이 아니에요"
NOTE_NO_USAGE = "사용량 정보가 없어 계산하지 못했어요"


class _CannotPrice(Exception):
    def __init__(self, note: str) -> None:
        super().__init__(note)
        self.note = note


@dataclass
class _FreeAllowance:
    """설계 1안 안에서 서비스별로 남은 '항상 무료' 한도."""

    remaining: dict[str, float]
    used_by: set[AwsService] = field(default_factory=set)

    @classmethod
    def from_prices(cls, p: PriceBook) -> "_FreeAllowance":
        return cls(
            remaining={
                "lambda_requests": p.aws_lambda.free_requests,
                "lambda_gb_seconds": p.aws_lambda.free_gb_seconds,
                "cloudfront_gb": p.cloudfront.free_gb_transfer,
                "cloudfront_requests": p.cloudfront.free_requests,
                "dynamodb_gb": p.dynamodb.free_storage_gb,
            }
        )

    def billable(self, key: str, amount: float, service: AwsService) -> float:
        """무료 한도를 먼저 쓰고, 과금되는 나머지 양을 돌려준다."""
        free = min(self.remaining[key], amount)
        if free > 0:
            self.remaining[key] -= free
            self.used_by.add(service)
        return amount - free


@dataclass
class _NodeCost:
    usd: float
    note: str
    free_applied: bool = False


# ── 표시용 숫자 ────────────────────────────────────────


def _count(n: float) -> str:
    """300000 → '30만', 1500000 → '150만', 12000 → '1.2만', 800 → '800'."""
    if n >= 10_000:
        man = n / 10_000
        return f"{man:,.0f}만" if man == int(man) else f"{man:,.1f}만"
    return f"{n:,.0f}"


def _num(x: float) -> str:
    return f"{x:g}"


def _round(value: float, places: int) -> Decimal:
    return Decimal(str(value)).quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_UP)


# ── 서비스별 계산 (Usage 필수 항목은 REQUIRED_USAGE_FIELDS로 미리 확인) ──────


def _lambda(u: Usage, p: PriceBook, free: _FreeAllowance) -> _NodeCost:
    lp = p.aws_lambda
    requests = float(u.monthly_requests)
    gb_seconds = requests * (u.avg_duration_ms / 1000) * (u.memory_mb / 1024)
    req = free.billable("lambda_requests", requests, AwsService.LAMBDA)
    gbs = free.billable("lambda_gb_seconds", gb_seconds, AwsService.LAMBDA)
    usd = req / 1e6 * lp.per_million_requests + gbs * lp.per_gb_second
    note = f"요청 월 {_count(requests)} 건, {u.memory_mb}MB·{u.avg_duration_ms}ms 기준"
    return _NodeCost(usd, note, free_applied=req < requests or gbs < gb_seconds)


def _api_gateway(u: Usage, p: PriceBook, _free: _FreeAllowance) -> _NodeCost:
    usd = u.monthly_requests / 1e6 * p.api_gateway.per_million_requests
    return _NodeCost(usd, f"REST API 요청 월 {_count(u.monthly_requests)} 건 기준")


def _s3(u: Usage, p: PriceBook, _free: _FreeAllowance) -> _NodeCost:
    usd = u.storage_gb * p.s3.per_gb_month
    return _NodeCost(usd, f"저장 {_num(u.storage_gb)}GB 기준 (요청 요금 제외)")


def _cloudfront(u: Usage, p: PriceBook, free: _FreeAllowance) -> _NodeCost:
    cp = p.cloudfront
    gb = free.billable("cloudfront_gb", u.data_transfer_out_gb, AwsService.CLOUDFRONT)
    req = free.billable("cloudfront_requests", float(u.monthly_requests), AwsService.CLOUDFRONT)
    usd = gb * cp.per_gb_transfer + req / 1e6 * cp.per_million_requests
    note = f"요청 월 {_count(u.monthly_requests)} 건, 전송 {_num(u.data_transfer_out_gb)}GB 기준"
    applied = gb < u.data_transfer_out_gb or req < u.monthly_requests
    return _NodeCost(usd, note, free_applied=applied)


def _dynamodb(u: Usage, p: PriceBook, free: _FreeAllowance) -> _NodeCost:
    dp = p.dynamodb
    gb = free.billable("dynamodb_gb", u.storage_gb, AwsService.DYNAMODB)
    usd = (
        u.monthly_reads / 1e6 * dp.per_million_reads
        + u.monthly_writes / 1e6 * dp.per_million_writes
        + gb * dp.per_gb_month
    )
    note = (
        f"온디맨드 읽기 월 {_count(u.monthly_reads)} 건, 쓰기 {_count(u.monthly_writes)} 건, "
        f"저장 {_num(u.storage_gb)}GB 기준"
    )
    return _NodeCost(usd, note, free_applied=gb < u.storage_gb)


def _hourly(table: dict[str, float], instance_type: str) -> float:
    if instance_type not in table:
        raise _CannotPrice(f"단가 정보가 없는 인스턴스 유형이에요 ({instance_type})")
    return table[instance_type]


def _rds(u: Usage, p: PriceBook, _free: _FreeAllowance) -> _NodeCost:
    rp = p.rds
    az = 2 if u.multi_az else 1
    hourly = _hourly(rp.instance_hourly, u.instance_type)
    usd = (hourly * u.hours_per_month * u.instance_count + u.storage_gb * rp.per_gb_month) * az
    note = (
        f"{u.instance_type} {u.instance_count}대, 월 {u.hours_per_month}시간, "
        f"스토리지 {_num(u.storage_gb)}GB 기준"
    )
    if u.multi_az:
        note += " (Multi-AZ)"
    return _NodeCost(usd, note)


def _ec2(u: Usage, p: PriceBook, _free: _FreeAllowance) -> _NodeCost:
    hourly = _hourly(p.ec2.instance_hourly, u.instance_type)
    usd = hourly * u.hours_per_month * u.instance_count
    note = f"{u.instance_type} {u.instance_count}대, 월 {u.hours_per_month}시간 기준 (EBS 제외)"
    return _NodeCost(usd, note)


def _alb(u: Usage, p: PriceBook, _free: _FreeAllowance) -> _NodeCost:
    usd = p.alb.per_hour * u.hours_per_month
    return _NodeCost(usd, f"월 {u.hours_per_month}시간 기준 (처리량(LCU) 요금 제외)")


def _nat_gateway(u: Usage, p: PriceBook, _free: _FreeAllowance) -> _NodeCost:
    npp = p.nat_gateway
    # instance_count는 필수가 아니다. 없으면 1개(가용 영역 1곳)로 본다
    count = u.instance_count or 1
    usd = npp.per_hour * u.hours_per_month * count + u.data_transfer_out_gb * npp.per_gb_processed
    note = f"{count}개, 월 {u.hours_per_month}시간, 처리 {_num(u.data_transfer_out_gb)}GB 기준"
    return _NodeCost(usd, note)


def _elasticache(u: Usage, p: PriceBook, _free: _FreeAllowance) -> _NodeCost:
    hourly = _hourly(p.elasticache.instance_hourly, u.instance_type)
    usd = hourly * u.hours_per_month * u.instance_count
    note = f"{u.instance_type} {u.instance_count}대, 월 {u.hours_per_month}시간 기준"
    return _NodeCost(usd, note)


CALCULATORS: dict[AwsService, Callable[[Usage, PriceBook, _FreeAllowance], _NodeCost]] = {
    AwsService.LAMBDA: _lambda,
    AwsService.API_GATEWAY: _api_gateway,
    AwsService.S3: _s3,
    AwsService.CLOUDFRONT: _cloudfront,
    AwsService.DYNAMODB: _dynamodb,
    AwsService.RDS: _rds,
    AwsService.EC2: _ec2,
    AwsService.ALB: _alb,
    AwsService.NAT_GATEWAY: _nat_gateway,
    AwsService.ELASTICACHE: _elasticache,
}
assert set(CALCULATORS) == PRICED_SERVICES, "비용 계산 지원 서비스와 계산 함수가 어긋남"


def _price_node(
    service: AwsService, usage: Usage | None, p: PriceBook, free: _FreeAllowance
) -> _NodeCost:
    if service not in CALCULATORS:
        raise _CannotPrice(NOTE_UNPRICED)
    required = REQUIRED_USAGE_FIELDS.get(service, ())
    if usage is None or any(getattr(usage, f) is None for f in required):
        raise _CannotPrice(NOTE_NO_USAGE)
    return CALCULATORS[service](usage, p, free)


def _free_tier_note(used_by: set[AwsService], total: int, *, excluded: bool) -> str | None:
    if not used_by:
        return None
    names = "·".join(FREE_TIER_LABELS[s] for s in FREE_TIER_LABELS if s in used_by)
    if excluded:
        # 계산에서 빠진 노드가 있으면 합계가 실제보다 작다. 월 0원이라고 단정하지 않는다
        return (
            f"{names} 항상 무료 한도를 빼고 계산했어요. "
            "계산에서 빠진 항목은 합계에 포함되지 않았어요. (가입 크레딧은 반영하지 않았어요)"
        )
    if total == 0:
        return f"{names} 항상 무료 한도 안이라 월 0원이에요. (가입 크레딧은 반영하지 않았어요)"
    return f"{names} 항상 무료 한도를 빼고 계산했어요. (가입 크레딧은 반영하지 않았어요)"


def estimate_monthly_cost(
    design: Design, *, monthly_budget: int, prices: PriceBook | None = None
) -> MonthlyCost:
    """1안의 월 비용. 사용자 노드는 items에서 뺀다."""
    p = prices or load_price_book()
    rate = p.exchange_rate.usd_krw
    free = _FreeAllowance.from_prices(p)

    items: list[CostItem] = []
    total_usd = 0.0
    for node in design.architecture.nodes:
        if node.service_type == AwsService.USERS:
            continue
        try:
            cost = _price_node(node.service_type, node.usage, p, free)
        except _CannotPrice as e:
            items.append(CostItem(node_id=node.id, amount=None, amount_usd=None, note=e.note))
            continue
        total_usd += cost.usd
        note = f"{cost.note} · 무료 한도 적용" if cost.free_applied else cost.note
        items.append(
            CostItem(
                node_id=node.id,
                amount=int(_round(cost.usd * rate, 0)),
                amount_usd=float(_round(cost.usd, 2)),
                note=note,
            )
        )

    total = sum(i.amount for i in items if i.amount is not None)
    excluded = any(i.amount is None for i in items)
    return MonthlyCost(
        total=total,
        total_usd=float(_round(total_usd, 2)),
        exchange_rate=rate,
        exchange_rate_date=p.exchange_rate.rate_date,
        price_date=p.price_date,
        free_tier=total == 0 and bool(free.used_by) and not excluded,
        free_tier_note=_free_tier_note(free.used_by, total, excluded=excluded),
        over_budget=total > monthly_budget,
        items=items,
    )
