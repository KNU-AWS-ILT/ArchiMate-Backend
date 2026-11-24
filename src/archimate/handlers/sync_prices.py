"""단가 동기화 Lambda — `archimate.handlers.sync_prices.handler` (고도화 아키텍처 문서 4장 ⑥).

EventBridge(매일 새벽)가 호출해서 단가를 가져와 prices 테이블에 쓴다.
필요한 환경 변수: PRICES_TABLE (쓰기 권한). 이벤트 내용은 쓰지 않는다.

⚠ 지금은 패키지 스냅샷(pricing/snapshots/)을 테이블에 채우는 일만 한다. AWS Price List API(us-east-1)로
실제 단가를 가져오는 PriceFetcher는 아직 없다 (archimate.pricing.sync 참고). 연동하면 이 파일에서
`SnapshotPriceFetcher()`만 바꾸면 된다.
가져오기가 실패하면 예외로 끝나고(CloudWatch 오류로 보인다) 테이블은 바뀌지 않아서 전날 단가가 남는다.
"""

from typing import Any

from archimate.handlers._app import get_price_store, run_async
from archimate.pricing import SnapshotPriceFetcher, sync_prices


def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    book = run_async(sync_prices(get_price_store(), SnapshotPriceFetcher()))
    return {
        "ok": True,
        "region": book.region,
        "priceDate": book.price_date.isoformat(),
        "source": book.source,
    }
