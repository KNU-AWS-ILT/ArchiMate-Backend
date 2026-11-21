import json
import os

# .env에 다른 제공자가 있어도 API 테스트는 항상 고정 응답(비용 0)으로 돌린다
os.environ["LLM_PROVIDER"] = "mock"

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from archimate.api.app import create_app  # noqa: E402
from archimate.api.settings import ApiSettings  # noqa: E402
from archimate.llm.providers.mock import load_fixture  # noqa: E402
from archimate.storage import InMemoryRepository  # noqa: E402


@pytest.fixture
def demo_answers() -> dict:
    return json.loads(load_fixture("demo_answers.json"))


@pytest.fixture
def repo() -> InMemoryRepository:
    return InMemoryRepository()


@pytest.fixture
def client(repo: InMemoryRepository):
    app = create_app(settings=ApiSettings(docs_enabled=True), repo=repo)
    # with: 요청 사이에 이벤트 루프가 유지돼 뒤에서 도는 생성 작업이 끝까지 실행된다
    with TestClient(app) as c:
        yield c
