"""저장소·핸들러 테스트 공용 fixture — moto가 메모리에서 DynamoDB를 흉내 내므로 AWS 호출·비용이 없다."""

import os

os.environ["LLM_PROVIDER"] = "mock"
os.environ.setdefault("AWS_DEFAULT_REGION", "ap-northeast-2")

import boto3  # noqa: E402
import pytest  # noqa: E402
from moto import mock_aws  # noqa: E402

from archimate.storage.dynamodb import DynamoDBRepository  # noqa: E402


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
        monkeypatch.setenv("DESIGNS_TABLE", "designs")
        monkeypatch.setenv("EXPLANATIONS_TABLE", "explanations")
        yield DynamoDBRepository.from_env()
