"""단가 검증 — 스냅샷(PriceBook)의 단가를 AWS 공개 Price List와 서비스별로 대조한다 (Issue #25).

기획서 목표는 "AWS Pricing Calculator 대비 ±20%"다. Pricing Calculator도 같은 Price List 데이터를 쓰므로,
사람이 일일이 입력하기 전에 단가가 공식 값과 얼마나 다른지부터 자동으로 확인한다.
(무료 한도·사용량 단위 같은 계산 방식은 Calculator와 직접 비교해야 한다. 이 모듈은 단가만 본다.)

Price List는 `https://pricing.us-east-1.amazonaws.com/offers/v1.0/aws/{서비스}/current/{리전}/index.json`
공개 파일이고, AWS 계정·인증이 필요 없다. 이 모듈의 `find_price`·`first_paid_price`는 나중에
Price List API 연동(`PriceFetcher`, pricing/sync.py)을 만들 때 그대로 쓸 수 있다.

범위: Lambda, API Gateway, S3, CloudFront, DynamoDB, RDS, ElastiCache, ALB, ECS Fargate.
EC2 인스턴스와 NAT Gateway는 `AmazonEC2` 파일이 약 290MB라서 확인하지 않는다 (report에 '미확인'으로 표시).
"""

import json
import tempfile
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from archimate.pricing.prices import DEFAULT_REGION, PriceBook

BASE_URL = "https://pricing.us-east-1.amazonaws.com/offers/v1.0/aws"
DEFAULT_THRESHOLD_PCT = 20.0  # 기획서: Pricing Calculator 대비 ±20%
EXACT_TOLERANCE_PCT = 0.5  # 이 안이면 '일치'로 본다 (반올림 차이)
STATUSES = ("일치", "차이", "초과", "미확인")

# 서비스 코드 → 리전별 파일인지 (CloudFront는 리전 없이 전 세계 한 파일)
OFFERS: dict[str, bool] = {
    "AWSLambda": True,
    "AmazonApiGateway": True,
    "AmazonS3": True,
    "AmazonCloudFront": False,
    "AmazonDynamoDB": True,
    "AmazonRDS": True,
    "AmazonElastiCache": True,
    "AWSELB": True,
    "AmazonECS": True,
}

PER_MILLION = 1_000_000.0  # Price List는 요청 1건 가격이라, 스냅샷의 '100만 건당' 단위로 바꾼다

NOT_CHECKED = (
    "EC2 인스턴스·NAT Gateway (AmazonEC2 Price List가 약 290MB라 확인하지 않음)",
    "환율 (개발용 고정값)",
)


class PriceNotFoundError(LookupError):
    """조건에 맞는 단가를 Price List에서 찾지 못했다."""


class AmbiguousPriceError(LookupError):
    """조건에 맞는 항목이 여럿이고 단가가 서로 다르다. 조건을 더 좁혀야 한다."""


# ── Price List 읽기 ────────────────────────────────────


def first_paid_price(offer: dict[str, Any], sku: str) -> float:
    """OnDemand 단가. 단계별(tier) 요금이면 0원이 아닌 첫 단계의 USD 단가를 돌려준다.

    예) API Gateway는 요청 수에 따라 4단계(첫 3.33억 건 $3.50/백만 건 ...)라서 첫 유료 단계를 쓴다.
    '무료 한도'가 첫 단계(0원)로 들어 있는 서비스도 그 뒤의 첫 유료 단계를 돌려준다.
    """
    terms = offer.get("terms", {}).get("OnDemand", {}).get(sku)
    if not terms:
        raise PriceNotFoundError(f"OnDemand 단가가 없어요: {sku}")
    prices: list[float] = []
    for term in terms.values():
        dimensions = sorted(
            term["priceDimensions"].values(), key=lambda d: float(d.get("beginRange") or 0)
        )
        prices += [float(d["pricePerUnit"]["USD"]) for d in dimensions]
    paid = [p for p in prices if p > 0]
    return paid[0] if paid else 0.0


def find_price(offer: dict[str, Any], usagetype: str, **attributes: str) -> float:
    """usagetype과 속성이 모두 맞는 상품의 단가 (예: databaseEngine="MySQL").

    같은 조건의 상품이 여럿이면 단가가 모두 같을 때만 돌려주고, 다르면 AmbiguousPriceError.
    """
    prices: set[float] = set()
    for sku, product in offer.get("products", {}).items():
        attrs = product.get("attributes", {})
        if attrs.get("usagetype") != usagetype:
            continue
        if any(attrs.get(key) != value for key, value in attributes.items()):
            continue
        try:
            prices.add(first_paid_price(offer, sku))
        except PriceNotFoundError:
            continue
    if not prices:
        raise PriceNotFoundError(f"{usagetype} {attributes}")
    if len(prices) > 1:
        raise AmbiguousPriceError(f"{usagetype} {attributes}: 단가가 여러 개예요 {sorted(prices)}")
    return prices.pop()


def offer_url(code: str, region: str) -> str:
    if OFFERS[code]:
        return f"{BASE_URL}/{code}/current/{region}/index.json"
    return f"{BASE_URL}/{code}/current/index.json"


def download_offer(code: str, region: str, cache_dir: Path, *, refresh: bool = False) -> dict:
    """Price List 파일을 내려받아 cache_dir에 저장하고 읽는다. 이미 있으면 다시 받지 않는다."""
    cache_dir.mkdir(parents=True, exist_ok=True)
    path = cache_dir / f"{code}-{region if OFFERS[code] else 'global'}.json"
    if refresh or not path.exists():
        with tempfile.NamedTemporaryFile(dir=cache_dir, delete=False, suffix=".part") as tmp:
            tmp_path = Path(tmp.name)
        try:
            # 공개 파일이라 인증이 없다. 고정된 https 주소만 쓰고 사용자 입력으로 만들지 않는다
            urllib.request.urlretrieve(offer_url(code, region), tmp_path)
            tmp_path.replace(path)
        finally:
            tmp_path.unlink(missing_ok=True)
    return json.loads(path.read_text("utf-8"))


# ── 대조 ──────────────────────────────────────────────


@dataclass(frozen=True)
class Spec:
    """스냅샷의 단가 하나와, 그에 해당하는 Price List 항목을 찾는 조건."""

    service: str
    item: str  # 예: "요청 (100만 건당)"
    ours: float
    code: str  # Price List 서비스 코드
    usagetype: str
    scale: float = 1.0  # Price List 값 × scale = 스냅샷 단위
    attrs: dict[str, str] = field(default_factory=dict)


def specs(book: PriceBook) -> list[Spec]:
    """대조할 단가 목록. PriceBook에 단가를 추가하면 여기에도 같이 추가한다."""
    out = [
        Spec(
            "Lambda",
            "요청 (100만 건당)",
            book.aws_lambda.per_million_requests,
            "AWSLambda",
            "APN2-Request",
            PER_MILLION,
        ),
        Spec(
            "Lambda",
            "실행 시간 (GB-초당)",
            book.aws_lambda.per_gb_second,
            "AWSLambda",
            "APN2-Lambda-GB-Second",
        ),
        Spec(
            "API Gateway",
            "REST 요청 (100만 건당)",
            book.api_gateway.per_million_requests,
            "AmazonApiGateway",
            "APN2-ApiGatewayRequest",
            PER_MILLION,
        ),
        Spec(
            "S3",
            "Standard 저장 (GB-월당)",
            book.s3.per_gb_month,
            "AmazonS3",
            "APN2-TimedStorage-ByteHrs",
            attrs={"volumeType": "Standard"},
        ),
        Spec(
            "CloudFront",
            "데이터 전송 (GB당, 아시아 태평양)",
            book.cloudfront.per_gb_transfer,
            "AmazonCloudFront",
            "AP-DataTransfer-Out-Bytes",
        ),
        Spec(
            "CloudFront",
            "HTTPS 요청 (100만 건당)",
            book.cloudfront.per_million_requests,
            "AmazonCloudFront",
            "AP-Requests-Tier2-HTTPS",
            PER_MILLION,
        ),
        Spec(
            "DynamoDB",
            "온디맨드 읽기 (100만 건당)",
            book.dynamodb.per_million_reads,
            "AmazonDynamoDB",
            "APN2-ReadRequestUnits",
            PER_MILLION,
        ),
        Spec(
            "DynamoDB",
            "온디맨드 쓰기 (100만 건당)",
            book.dynamodb.per_million_writes,
            "AmazonDynamoDB",
            "APN2-WriteRequestUnits",
            PER_MILLION,
        ),
        Spec(
            "DynamoDB",
            "저장 (GB-월당)",
            book.dynamodb.per_gb_month,
            "AmazonDynamoDB",
            "APN2-TimedStorage-ByteHrs",
            attrs={"volumeType": "Amazon DynamoDB - Indexed DataStore"},
        ),
        Spec(
            "RDS",
            "gp3 스토리지 (GB-월당)",
            book.rds.per_gb_month,
            "AmazonRDS",
            "APN2-RDS:GP3-Storage",
            attrs={"databaseEngine": "Any", "deploymentOption": "Single-AZ"},
        ),
        Spec(
            "ALB",
            "로드 밸런서 (시간당)",
            book.alb.per_hour,
            "AWSELB",
            "APN2-LoadBalancerUsage",
            attrs={"operation": "LoadBalancing:Application"},
        ),
        Spec(
            "ECS Fargate",
            "vCPU (시간당, Linux/x86)",
            book.ecs_fargate.vcpu_hourly,
            "AmazonECS",
            "APN2-Fargate-vCPU-Hours:perCPU",
        ),
        Spec(
            "ECS Fargate",
            "메모리 (GB-시간당, Linux/x86)",
            book.ecs_fargate.gb_hourly,
            "AmazonECS",
            "APN2-Fargate-GB-Hours",
        ),
    ]
    for instance_type, hourly in book.rds.instance_hourly.items():
        out.append(
            Spec(
                "RDS",
                f"{instance_type} (MySQL Single-AZ, 시간당)",
                hourly,
                "AmazonRDS",
                f"APN2-InstanceUsage:{instance_type}",
                attrs={"databaseEngine": "MySQL", "deploymentOption": "Single-AZ"},
            )
        )
    for instance_type, hourly in book.elasticache.instance_hourly.items():
        out.append(
            Spec(
                "ElastiCache",
                f"{instance_type} (Redis, 시간당)",
                hourly,
                "AmazonElastiCache",
                f"APN2-NodeUsage:{instance_type}",
                attrs={"cacheEngine": "Redis"},
            )
        )
    return out


@dataclass(frozen=True)
class Check:
    service: str
    item: str
    ours: float
    official: float | None  # None이면 공식 값을 찾지 못함
    error: str = ""

    @property
    def diff_pct(self) -> float | None:
        if self.official is None or self.official == 0:
            return None
        return (self.ours - self.official) / self.official * 100

    def status(self, threshold_pct: float = DEFAULT_THRESHOLD_PCT) -> str:
        diff = self.diff_pct
        if diff is None:
            return "미확인"
        if abs(diff) > threshold_pct:
            return "초과"
        return "일치" if abs(diff) <= EXACT_TOLERANCE_PCT else "차이"


def build_checks(book: PriceBook, offers: dict[str, dict]) -> list[Check]:
    """스냅샷의 단가마다 Price List의 공식 값을 찾아 대조 목록을 만든다.

    offers: 서비스 코드 → 읽어 둔 Price List. 파일이 없는 서비스는 건너뛴다.
    """
    checks: list[Check] = []
    for spec in specs(book):
        offer = offers.get(spec.code)
        if offer is None:
            continue
        try:
            official: float | None = find_price(offer, spec.usagetype, **spec.attrs) * spec.scale
            error = ""
        except (PriceNotFoundError, AmbiguousPriceError) as e:
            official, error = None, str(e)
        checks.append(Check(spec.service, spec.item, spec.ours, official, error))
    return checks


def load_offers(cache_dir: Path, *, region: str = DEFAULT_REGION, refresh: bool = False) -> dict:
    return {code: download_offer(code, region, cache_dir, refresh=refresh) for code in OFFERS}


def worst_diff_pct(checks: list[Check]) -> float:
    return max((abs(c.diff_pct) for c in checks if c.diff_pct is not None), default=0.0)


def render_report(
    checks: list[Check], *, threshold_pct: float = DEFAULT_THRESHOLD_PCT, price_date: str = ""
) -> str:
    """Markdown 표. 항목마다 우리 값, 공식 값, 차이(%), 판정."""
    lines = [
        "# 단가 검증 (스냅샷 vs AWS Price List)",
        "",
        f"- 기준: 서울 리전(`ap-northeast-2`) 온디맨드 USD. 스냅샷 단가일: {price_date or '-'}",
        f"- 목표: 공식 값 대비 ±{threshold_pct:g}% (기획서, Pricing Calculator 기준)",
        "",
        "| 서비스 | 항목 | 우리 값 | 공식 값 | 차이 | 판정 |",
        "| --- | --- | ---: | ---: | ---: | :---: |",
    ]
    for c in checks:
        diff = "-" if c.diff_pct is None else f"{c.diff_pct:+.1f}%"
        official = "-" if c.official is None else f"{c.official:g}"
        status = c.status(threshold_pct)
        lines.append(f"| {c.service} | {c.item} | {c.ours:g} | {official} | {diff} | {status} |")
    count = {s: sum(c.status(threshold_pct) == s for c in checks) for s in STATUSES}
    lines += [
        "",
        f"- 항목 {len(checks)}개: " + " · ".join(f"{s} {count[s]}" for s in STATUSES),
        f"- 가장 큰 차이: {worst_diff_pct(checks):.1f}%",
    ]
    errors = [c for c in checks if c.error]
    if errors:
        lines += ["", "공식 값을 찾지 못한 항목:"]
        lines += [f"- {c.service} {c.item}: {c.error}" for c in errors]
    lines += ["", "확인하지 않은 것:"] + [f"- {item}" for item in NOT_CHECKED]
    return "\n".join(lines) + "\n"
