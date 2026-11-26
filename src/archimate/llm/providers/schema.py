"""제공자가 함께 쓰는 JSON 스키마 변환. 외부 SDK를 import하지 않는다 (Lambda 패키지에도 들어간다)."""

from typing import Any


def inline_refs(schema: dict[str, Any]) -> dict[str, Any]:
    """$defs/$ref를 펼친다.

    Gemini는 $ref 옆에 description 같은 다른 키가 있으면 거부하는데, Pydantic은 그런 스키마를 만든다.
    Bedrock Converse 도구 스키마도 모델마다 $ref 지원이 달라서 펼쳐서 보낸다.
    우리 스키마는 재귀가 없으므로 펼쳐도 끝난다.
    """
    defs = schema.get("$defs", {})

    def resolve(node: Any) -> Any:
        if isinstance(node, dict):
            if "$ref" in node:
                name = node["$ref"].split("/")[-1]
                merged = {**resolve(defs[name]), **{k: v for k, v in node.items() if k != "$ref"}}
                return resolve(merged)
            return {k: resolve(v) for k, v in node.items() if k not in ("$defs", "default")}
        if isinstance(node, list):
            return [resolve(v) for v in node]
        return node

    return resolve(schema)
