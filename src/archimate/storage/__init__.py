"""[백엔드(API·비용)] 저장소 — 생성 작업(3안 결과)과 노드 설명 캐시.

    DesignRepository   인터페이스 (API 로직은 이것만 안다)
    InMemoryRepository 로컬·테스트용 구현
    (예정) DynamoDB 구현 — 테이블 이름·키 속성은 Issue #15에서 확정

`archimate.llm` 은 이 모듈을 import 하지 않는다. 저장은 API 계층에서 조합한다.
"""

from archimate.storage.base import DesignRepository, JobRecord
from archimate.storage.memory import InMemoryRepository

__all__ = ["DesignRepository", "InMemoryRepository", "JobRecord"]
