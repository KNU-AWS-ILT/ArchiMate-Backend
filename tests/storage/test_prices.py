"""단가 저장·동기화·읽기 — moto가 메모리에서 DynamoDB를 흉내 내므로 AWS 호출·비용이 없다 (#15)."""

import json

import pytest

from archimate.api import service
from archimate.llm import build_requirements
from archimate.llm.providers.mock import load_fixture
from archimate.pricing import (
    SnapshotPriceFetcher,
    SnapshotPriceSource,
    StoredPriceSource,
    load_price_book,
    sync_prices,
)
from archimate.schemas import JobStatus
from archimate.storage import InMemoryRepository


class FakeStore:
    """읽은 횟수를 세고, 지정하면 읽기에서 예외를 내는 가짜 prices 저장소."""

    def __init__(self, book=None, error: Exception | None = None) -> None:
        self.book, self.error, self.reads = book, error, 0

    async def get(self, region):
        self.reads += 1
        if self.error:
            raise self.error
        return self.book

    async def put(self, book):
        self.book = book


class Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


# ── DynamoDB 저장소 ──


async def test_store_roundtrip_and_missing(price_store):
    assert await price_store.get("ap-northeast-2") is None
    book = load_price_book()
    await price_store.put(book)
    assert await price_store.get("ap-northeast-2") == book
    assert await price_store.get("us-east-1") is None


async def test_store_put_overwrites(price_store, book_with_rate):
    await price_store.put(book_with_rate(1500))
    await price_store.put(book_with_rate(1600))
    assert (await price_store.get("ap-northeast-2")).exchange_rate.usd_krw == 1600


# ── 동기화 ──


async def test_sync_fills_the_table_from_snapshot(price_store):
    book = await sync_prices(price_store, SnapshotPriceFetcher())
    assert await price_store.get("ap-northeast-2") == book == load_price_book()


async def test_failed_fetch_keeps_previous_prices(price_store, book_with_rate):
    await price_store.put(book_with_rate(1500))

    class Broken:
        async def fetch(self, region):
            raise RuntimeError("Price List API 오류")

    with pytest.raises(RuntimeError):
        await sync_prices(price_store, Broken())
    assert (await price_store.get("ap-northeast-2")).exchange_rate.usd_krw == 1500


async def test_sync_rejects_region_mismatch(price_store):
    class WrongRegion:
        async def fetch(self, region):
            return load_price_book()  # ap-northeast-2

    with pytest.raises(ValueError, match="리전"):
        await sync_prices(price_store, WrongRegion(), region="us-east-1")
    assert await price_store.get("us-east-1") is None


# ── 읽기: 캐시와 대체 ──


async def test_source_caches_within_ttl_and_refreshes_after(book_with_rate):
    store, clock = FakeStore(book_with_rate(1500)), Clock()
    source = StoredPriceSource(store, ttl_s=100, clock=clock)
    assert (await source.get()).exchange_rate.usd_krw == 1500
    store.book = book_with_rate(1600)
    clock.now = 99
    assert (await source.get()).exchange_rate.usd_krw == 1500  # 아직 캐시
    clock.now = 101
    assert (await source.get()).exchange_rate.usd_krw == 1600  # 다시 읽음
    assert store.reads == 2


async def test_source_falls_back_to_snapshot_when_table_is_empty():
    source = StoredPriceSource(FakeStore(None))
    assert await source.get() == load_price_book()


async def test_source_keeps_last_read_when_read_fails(book_with_rate):
    store, clock = FakeStore(book_with_rate(1500)), Clock()
    source = StoredPriceSource(store, ttl_s=10, clock=clock)
    await source.get()
    store.error, clock.now = RuntimeError("DynamoDB 장애"), 50
    assert (await source.get()).exchange_rate.usd_krw == 1500  # 마지막으로 읽은 값


async def test_source_uses_snapshot_when_first_read_fails():
    source = StoredPriceSource(FakeStore(error=RuntimeError("DynamoDB 장애")))
    assert await source.get() == load_price_book()


async def test_snapshot_source():
    assert await SnapshotPriceSource().get() == load_price_book()


# ── 설계 생성이 쓰는 단가 ──


@pytest.fixture
def req():
    return build_requirements(json.loads(load_fixture("demo_answers.json")))


async def run_and_rates(req, **kwargs) -> set[float]:
    repo = InMemoryRepository()
    job_id = await service.create_job(repo, "u1", req)
    await service.run_design_job(repo, job_id, **kwargs)
    job = await service.get_job(repo, job_id, "u1")
    assert job.status == JobStatus.COMPLETED
    return {d.monthly_cost.exchange_rate for d in job.designs}


async def test_job_costs_use_the_price_source(req, book_with_rate):
    source = StoredPriceSource(FakeStore(book_with_rate(2000)))
    assert await run_and_rates(req, price_source=source) == {2000.0}


async def test_job_without_price_source_uses_snapshot(req):
    assert await run_and_rates(req) == {load_price_book().exchange_rate.usd_krw}


async def test_job_continues_with_snapshot_if_price_source_raises(req):
    class Broken:
        async def get(self):
            raise RuntimeError("예상 못 한 오류")

    assert await run_and_rates(req, price_source=Broken()) == {
        load_price_book().exchange_rate.usd_krw
    }
