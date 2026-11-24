"""저장소·핸들러 테스트 공용 fixture — moto가 메모리에서 DynamoDB를 흉내 내므로 AWS 호출·비용이 없다."""

import json
import os

os.environ["LLM_PROVIDER"] = "mock"
os.environ.setdefault("AWS_DEFAULT_REGION", "ap-northeast-2")

import boto3  # noqa: E402
import pytest  # noqa: E402
from moto import mock_aws  # noqa: E402

from archimate.pricing import PriceBook, load_price_book  # noqa: E402
from archimate.storage.dynamodb import DynamoDBRepository  # noqa: E402
from archimate.storage.prices import DynamoDBPriceStore  # noqa: E402


@pytest.fixture
def repo(monkeypatch):
    for k in ("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY"):
        monkeypatch.setenv(k, "test")
    with mock_aws():
        db = boto3.resource("dynamodb")
        db.create_table(
            TableName="designs",
            KeySchema=[{"AttributeName": "jobId", "KeyType": "HASH"}],
            AttributeDefinitions=[{"AttributeName": "jobId", "AttributeType": "S"}],
            BillingMode="PAY_PER_REQUEST",
        )
        db.create_table(
            TableName="explanations",
            KeySchema=[
                {"AttributeName": "designId", "KeyType": "HASH"},
                {"AttributeName": "nodeId", "KeyType": "RANGE"},
            ],
            AttributeDefinitions=[
                {"AttributeName": "designId", "AttributeType": "S"},
                {"AttributeName": "nodeId", "AttributeType": "S"},
            ],
            BillingMode="PAY_PER_REQUEST",
        )
        db.create_table(
            TableName="prices",
            KeySchema=[
                {"AttributeName": "service", "KeyType": "HASH"},
                {"AttributeName": "regionUsageType", "KeyType": "RANGE"},
            ],
            AttributeDefinitions=[
                {"AttributeName": "service", "AttributeType": "S"},
                {"AttributeName": "regionUsageType", "AttributeType": "S"},
            ],
            BillingMode="PAY_PER_REQUEST",
        )
        monkeypatch.setenv("PRICES_TABLE", "prices")
        monkeypatch.setenv("DESIGNS_TABLE", "designs")
        monkeypatch.setenv("EXPLANATIONS_TABLE", "explanations")
        yield DynamoDBRepository.from_env()


@pytest.fixture
def price_store(repo):
    """repo와 같은 moto 컨텍스트의 prices 테이블."""
    return DynamoDBPriceStore.from_env()


@pytest.fixture
def book_with_rate():
    """스냅샷과 환율만 다른 단가표를 만든다 (어느 단가가 쓰였는지 환율로 구분하려고)."""

    def make(usd_krw: float) -> PriceBook:
        data = json.loads(load_price_book().model_dump_json())
        data["exchangeRate"]["usdKrw"] = usd_krw
        return PriceBook.model_validate(data)

    return make
