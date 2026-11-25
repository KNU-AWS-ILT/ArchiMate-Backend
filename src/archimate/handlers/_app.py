"""Lambda 핸들러가 함께 쓰는 FastAPI 앱 조립 — 환경 변수로 저장소를 고른다 (cold start에 한 번만).

필요한 환경 변수 (Issue #15에서 확정)
    DESIGNS_TABLE, EXPLANATIONS_TABLE   설계 생성·노드 설명 Lambda
    PRICES_TABLE                        설계 생성 Lambda(읽기)·단가 동기화 Lambda(쓰기). 없으면 패키지 스냅샷 단가
선택: API_CORS_ORIGINS (JSON 배열, 예: '["https://archimate.example"]')
"""

import asyncio
import os
from collections.abc import Coroutine
from functools import lru_cache
from typing import Any, TypeVar

from fastapi import FastAPI

from archimate.api.app import create_app
from archimate.api.runner import JobRunner
from archimate.api.settings import ApiSettings
from archimate.pricing import PriceSource, PriceStore, StoredPriceSource
from archimate.storage import DesignRepository
from archimate.storage.dynamodb import DynamoDBRepository
from archimate.storage.prices import PRICES_TABLE_ENV, DynamoDBPriceStore


@lru_cache
def get_repo() -> DesignRepository:
    return DynamoDBRepository.from_env()


@lru_cache
def get_price_store() -> PriceStore:
    return DynamoDBPriceStore.from_env()


@lru_cache
def get_price_source() -> PriceSource | None:
    """PRICES_TABLE이 설정돼 있으면 테이블에서 읽는 단가 출처, 아니면 None(= 스냅샷)."""
    if not os.environ.get(PRICES_TABLE_ENV):
        return None
    return StoredPriceSource(get_price_store())


def build_app(runner: JobRunner | None = None) -> FastAPI:
    # Lambda 앞에는 API Gateway가 있어 /docs 등은 라우팅되지 않지만, 문서 엔드포인트는 꺼 둔다
    return create_app(settings=ApiSettings(docs_enabled=False), repo=get_repo(), runner=runner)


T = TypeVar("T")


@lru_cache
def _loop() -> asyncio.AbstractEventLoop:
    return asyncio.new_event_loop()


def use_event_loop() -> asyncio.AbstractEventLoop:
    """호출마다 같은 이벤트 루프를 현재 루프로 둔다.

    `asyncio.run()`은 끝나면 루프를 닫고 지운다. 같은 Lambda 컨테이너가 자기 호출 이벤트(run_job)를
    처리한 뒤 API Gateway 이벤트를 받으면, Mangum이 현재 루프를 찾지 못해 실패하므로 루프를 재사용한다.
    """
    loop = _loop()
    asyncio.set_event_loop(loop)
    return loop


def run_async(coro: Coroutine[Any, Any, T]) -> T:
    return use_event_loop().run_until_complete(coro)
