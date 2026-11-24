"""단가 출처 — 설계 생성 Lambda가 단가를 읽는 방법 (Issue #15).

prices 테이블(단가 동기화 Lambda가 갱신)에서 읽는다. 테이블이 비었거나 읽기에 실패하면 패키지에
포함된 스냅샷으로 계속 동작한다 ("실패하면 전날 단가로 계속 동작", 고도화 아키텍처 문서 4장 ⑥).
비용 계산(calculator)은 PriceBook만 알고, 저장소 종류(DynamoDB 등)는 PriceStore 뒤에 둔다.
"""

import logging
import time
from collections.abc import Callable
from typing import Protocol

from archimate.pricing.prices import DEFAULT_REGION, PriceBook, load_price_book

logger = logging.getLogger(__name__)

DEFAULT_TTL_S = (
    3600.0  # 단가는 하루 한 번 바뀐다. 컨테이너가 재사용되는 동안 1시간은 같은 값을 쓴다
)


class PriceStore(Protocol):
    """prices 테이블 접근. 구현은 storage/prices.py (DynamoDB)."""

    async def get(self, region: str) -> PriceBook | None: ...

    async def put(self, book: PriceBook) -> None: ...


class PriceSource(Protocol):
    """비용 계산에 쓸 단가표를 돌려주는 쪽. 읽기 실패는 밖으로 내지 않는다."""

    async def get(self) -> PriceBook: ...


class SnapshotPriceSource:
    """패키지에 포함된 스냅샷만 쓴다 (로컬·테스트)."""

    def __init__(self, region: str = DEFAULT_REGION) -> None:
        self._region = region

    async def get(self) -> PriceBook:
        return load_price_book(self._region)


class StoredPriceSource:
    """prices 테이블에서 읽고, 안 되면 마지막으로 읽은 값, 그것도 없으면 스냅샷으로 대신한다."""

    def __init__(
        self,
        store: PriceStore,
        *,
        region: str = DEFAULT_REGION,
        ttl_s: float = DEFAULT_TTL_S,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._store = store
        self._region = region
        self._ttl_s = ttl_s
        self._clock = clock
        self._cached: PriceBook | None = None
        self._loaded_at = 0.0

    async def get(self) -> PriceBook:
        now = self._clock()
        if self._cached is not None and now - self._loaded_at < self._ttl_s:
            return self._cached

        book: PriceBook | None = None
        try:
            book = await self._store.get(self._region)
        except Exception:
            # 읽기 실패나 깨진 데이터로 비용 계산 전체가 멈추면 안 된다
            logger.exception("prices.read_failed region=%s", self._region)

        if book is not None:
            self._cached, self._loaded_at = book, now
            return book
        if self._cached is not None:
            logger.warning("prices.using_last_read region=%s", self._region)
            return self._cached
        logger.warning("prices.using_snapshot region=%s", self._region)
        return load_price_book(self._region)
