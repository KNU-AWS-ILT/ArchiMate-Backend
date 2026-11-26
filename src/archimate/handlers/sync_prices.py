"""단가 동기화 Lambda — `archimate.handlers.sync_prices.handler` (고도화 아키텍처 문서 4장 ⑥).

EventBridge(매일 새벽)가 호출해서 단가를 가져와 prices 테이블에 쓴다.
필요한 환경 변수: PRICES_TABLE (쓰기 권한). 이벤트 내용은 쓰지 않는다.
필요한 IAM 권한: `pricing:GetProducts` (Price List API, #55)

선택: PRICE_FETCHER
    price_list (기본)  AWS Price List Query API(us-east-1)로 실제 단가를 가져온다 (#55)
    snapshot           패키지 스냅샷(pricing/snapshots/)을 그대로 채운다 (#39의 동작)
가져오기가 실패하면 예외로 끝나고(CloudWatch 오류로 보인다) 테이블은 바뀌지 않아서 전날 단가가 남는다.
"""

import os
from typing import Any

from archimate.handlers._app import get_price_store, run_async
from archimate.pricing import PriceFetcher, SnapshotPriceFetcher, sync_prices
from archimate.pricing.price_list import PriceListFetcher

PRICE_FETCHER_ENV = "PRICE_FETCHER"
FETCHERS = ("price_list", "snapshot")


def get_fetcher() -> PriceFetcher:
    name = os.environ.get(PRICE_FETCHER_ENV, "price_list").strip() or "price_list"
    if name == "snapshot":
        return SnapshotPriceFetcher()
    if name == "price_list":
        return PriceListFetcher()
    raise ValueError(f"{PRICE_FETCHER_ENV}={name!r}는 {' 또는 '.join(FETCHERS)}만 쓸 수 있어요")


def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    book = run_async(sync_prices(get_price_store(), get_fetcher()))
    return {
        "ok": True,
        "region": book.region,
        "priceDate": book.price_date.isoformat(),
        "source": book.source,
    }
