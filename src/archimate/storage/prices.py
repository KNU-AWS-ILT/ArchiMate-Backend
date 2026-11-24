"""prices 테이블 — DynamoDB 단가 저장소 (Issue #15에서 확정한 키 설계).

    prices   PK service (S), SK regionUsageType (S)   TTL 없음 (매일 덮어쓴다)

단가표(PriceBook)는 작아서(수 KB) 리전마다 한 항목으로 통째로 저장한다.
    service = "priceBook", regionUsageType = "{region}#all", data = PriceBook JSON
한 번의 쓰기로 바뀌어서, 읽는 쪽이 일부만 새 값인 단가표를 보는 일이 없다.
서비스별·사용 유형별로 항목을 나누는 방식(`ap-northeast-2#Requests` 등)은 단가가 커지면 그때 바꾼다.
"""

import asyncio
import os
from datetime import UTC, datetime
from typing import Any

from archimate.pricing import PriceBook

PRICES_TABLE_ENV = "PRICES_TABLE"
PRICE_BOOK_SERVICE = "priceBook"


def _sort_key(region: str) -> str:
    return f"{region}#all"


class DynamoDBPriceStore:
    def __init__(self, table: Any) -> None:
        """테이블 객체(boto3 `Table`)를 받는다. 실행 환경에서는 `from_env()`를 쓴다."""
        self._table = table

    @classmethod
    def from_env(cls) -> "DynamoDBPriceStore":
        import boto3

        return cls(boto3.resource("dynamodb").Table(os.environ[PRICES_TABLE_ENV]))

    async def get(self, region: str) -> PriceBook | None:
        res = await asyncio.to_thread(
            self._table.get_item,
            Key={"service": PRICE_BOOK_SERVICE, "regionUsageType": _sort_key(region)},
            ConsistentRead=True,
        )
        item = res.get("Item")
        if item is None:
            return None
        return PriceBook.model_validate_json(item["data"])

    async def put(self, book: PriceBook) -> None:
        await asyncio.to_thread(
            self._table.put_item,
            Item={
                "service": PRICE_BOOK_SERVICE,
                "regionUsageType": _sort_key(book.region),
                "data": book.model_dump_json(),
                "priceDate": book.price_date.isoformat(),
                "updatedAt": datetime.now(UTC).isoformat(),
            },
        )
