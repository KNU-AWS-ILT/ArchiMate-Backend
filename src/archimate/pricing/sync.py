"""단가 동기화 — 단가를 가져와 prices 테이블에 쓴다 (Issue #15, 고도화 아키텍처 문서 4장 ⑥).

가져오는 쪽(PriceFetcher)과 쓰는 쪽(PriceStore)을 나눠서, AWS Price List API 연동을 나중에
PriceFetcher 구현만 추가해서 붙일 수 있게 한다. 가져오기가 실패하면 쓰지 않아서 이전 단가가 남는다.

PriceFetcher 구현:
- `PriceListFetcher` (pricing/price_list.py, #55): AWS Price List Query API로 실제 단가를 가져온다. Lambda 기본값
- `SnapshotPriceFetcher`: 패키지 스냅샷을 그대로 돌려준다 (로컬·테스트, `PRICE_FETCHER=snapshot`)
"""

import logging
from typing import Protocol

from archimate.pricing.prices import DEFAULT_REGION, PriceBook, load_price_book
from archimate.pricing.source import PriceStore

logger = logging.getLogger(__name__)


class PriceFetcher(Protocol):
    async def fetch(self, region: str) -> PriceBook: ...


class SnapshotPriceFetcher:
    """패키지에 포함된 스냅샷(pricing/snapshots/{region}.json)을 그대로 돌려준다."""

    async def fetch(self, region: str) -> PriceBook:
        return load_price_book(region)


async def sync_prices(
    store: PriceStore, fetcher: PriceFetcher, *, region: str = DEFAULT_REGION
) -> PriceBook:
    """단가를 가져와 검증한 뒤 테이블에 쓴다. 가져오기·검증이 실패하면 예외를 내고 아무것도 쓰지 않는다."""
    book = await fetcher.fetch(region)
    if book.region != region:
        raise ValueError(f"요청한 리전({region})과 가져온 단가의 리전({book.region})이 달라요")
    await store.put(book)
    logger.info(
        "prices.synced region=%s price_date=%s source=%s",
        book.region,
        book.price_date,
        book.source,
    )
    return book
