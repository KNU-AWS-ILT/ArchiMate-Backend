"""단가표(PriceBook) — 서울 리전 USD 단가와 환율.

개발 중에는 패키지에 포함된 스냅샷(snapshots/{region}.json)을 쓴다.
단가 동기화 Lambda(handlers/sync_prices.py)가 연결되면 prices 테이블에서 같은 모양으로 읽는다.
"""

from datetime import date
from functools import lru_cache
from importlib import resources

from pydantic import Field

from archimate.schemas import CamelModel

DEFAULT_REGION = "ap-northeast-2"


class ExchangeRate(CamelModel):
    usd_krw: float = Field(gt=0, description="1 USD당 원")
    rate_date: date = Field(alias="date")
    source: str


class LambdaPrice(CamelModel):
    per_million_requests: float
    per_gb_second: float
    free_requests: float = Field(description="항상 무료: 월 요청 수")
    free_gb_seconds: float = Field(description="항상 무료: 월 GB-초")


class ApiGatewayPrice(CamelModel):
    per_million_requests: float = Field(description="REST API 기준")


class S3Price(CamelModel):
    per_gb_month: float = Field(description="Standard 스토리지")


class CloudFrontPrice(CamelModel):
    per_gb_transfer: float
    per_million_requests: float
    free_gb_transfer: float = Field(description="항상 무료: 월 전송량(GB)")
    free_requests: float = Field(description="항상 무료: 월 요청 수")


class DynamoDbPrice(CamelModel):
    per_million_reads: float = Field(description="온디맨드 읽기 요청 100만 건")
    per_million_writes: float = Field(description="온디맨드 쓰기 요청 100만 건")
    per_gb_month: float
    free_storage_gb: float = Field(description="항상 무료: 저장 용량(GB)")


class RdsPrice(CamelModel):
    per_gb_month: float = Field(description="gp3 스토리지")
    instance_hourly: dict[str, float] = Field(description="Single-AZ 시간당 단가")


class Ec2Price(CamelModel):
    instance_hourly: dict[str, float] = Field(description="Linux 온디맨드 시간당 단가")


class AlbPrice(CamelModel):
    per_hour: float = Field(description="시간 요금만 (LCU 제외)")


class EcsFargatePrice(CamelModel):
    vcpu_hourly: float = Field(description="Linux/x86 vCPU 1개 시간당 단가")
    gb_hourly: float = Field(
        description="Linux/x86 메모리 1GB 시간당 단가 (임시 저장소 20GB 초과분 제외)"
    )


class NatGatewayPrice(CamelModel):
    per_hour: float = Field(description="게이트웨이 1개 시간당 단가")
    per_gb_processed: float = Field(description="처리한 데이터 1GB당 단가")


class ElastiCachePrice(CamelModel):
    instance_hourly: dict[str, float] = Field(description="Redis·Valkey 온디맨드 시간당 단가")


class PriceBook(CamelModel):
    region: str
    price_date: date
    source: str
    exchange_rate: ExchangeRate
    aws_lambda: LambdaPrice
    api_gateway: ApiGatewayPrice
    s3: S3Price
    cloudfront: CloudFrontPrice
    dynamodb: DynamoDbPrice
    rds: RdsPrice
    ec2: Ec2Price
    alb: AlbPrice
    ecs_fargate: EcsFargatePrice
    nat_gateway: NatGatewayPrice
    elasticache: ElastiCachePrice


@lru_cache
def load_price_book(region: str = DEFAULT_REGION) -> PriceBook:
    path = resources.files("archimate.pricing").joinpath("snapshots", f"{region}.json")
    return PriceBook.model_validate_json(path.read_text("utf-8"))
