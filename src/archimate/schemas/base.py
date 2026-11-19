"""모든 계약 모델의 기반 클래스.

JSON 필드명은 camelCase, 파이썬 속성명은 snake_case를 쓴다 (Issue #6 제안 A안).
    Node(service_type=...)           # 파이썬 코드
    {"serviceType": "lambda"}        # API·LLM·contracts/ JSON
열거형 값은 소문자 snake_case(예: "minimal_cost"), 에러 코드는 UPPER_SNAKE_CASE를 쓴다.
"""

from pydantic import BaseModel, ConfigDict
from pydantic.alias_generators import to_camel


class CamelModel(BaseModel):
    model_config = ConfigDict(
        alias_generator=to_camel,
        validate_by_name=True,  # 파이썬 코드에서는 snake_case 이름으로도 생성 가능
        validate_by_alias=True,
        serialize_by_alias=True,  # model_dump / model_dump_json 결과는 camelCase
    )
