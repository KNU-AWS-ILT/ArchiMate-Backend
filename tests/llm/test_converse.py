"""Bedrock Converse 제공자 (#15: 배포 계정에서 Claude를 못 써서 Qwen·Nova 등으로 생성).

실제 AWS는 부르지 않는다. boto3 클라이언트 대신 요청을 기록하고 준비한 응답·예외를 돌려주는 가짜를 넣는다.
"""

import json

import pytest
from botocore.exceptions import ClientError, EndpointConnectionError, ReadTimeoutError

from archimate.llm import LLMProviderError, LLMRateLimitError, LLMTimeoutError
from archimate.llm.errors import LLMConfigError, LLMOutputError
from archimate.llm.providers import ChatMessage, build_provider
from archimate.llm.providers.converse import TOOL_NAME, ConverseProvider
from archimate.llm.providers.mock import load_fixture
from archimate.llm.settings import LLMSettings
from archimate.schemas import DesignDraft, ExplanationDraft


class FakeClient:
    def __init__(self, *results: object) -> None:
        self._results = list(results)
        self.requests: list[dict] = []

    def converse(self, **request):
        self.requests.append(request)
        result = self._results.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


def tool_response(tool_input: object, stop: str = "tool_use") -> dict:
    return {
        "output": {
            "message": {
                "role": "assistant",
                "content": [
                    {"toolUse": {"toolUseId": "t1", "name": TOOL_NAME, "input": tool_input}}
                ],
            }
        },
        "stopReason": stop,
        "usage": {"inputTokens": 1200, "outputTokens": 800},
    }


def text_response(text: str, stop: str = "end_turn") -> dict:
    return {
        "output": {"message": {"role": "assistant", "content": [{"text": text}]}},
        "stopReason": stop,
    }


def client_error(code: str, message: str = "boom") -> ClientError:
    return ClientError({"Error": {"Code": code, "Message": message}}, "Converse")


def make(client: FakeClient) -> ConverseProvider:
    return ConverseProvider(
        model="qwen.test",
        aws_region="ap-southeast-2",
        max_output_tokens=4000,
        timeout_s=30,
        client=client,
    )


async def call(provider: ConverseProvider, output_model=ExplanationDraft) -> str:
    return await provider.generate_json(
        system="시스템", messages=[ChatMessage("user", "질문")], output_model=output_model
    )


EXPLANATION = json.loads(load_fixture("explanation.json"))


async def test_request_forces_the_output_schema_as_a_tool():
    client = FakeClient(tool_response(EXPLANATION))
    await call(make(client))

    req = client.requests[0]
    assert req["modelId"] == "qwen.test"
    assert req["system"] == [{"text": "시스템"}]
    assert req["messages"] == [{"role": "user", "content": [{"text": "질문"}]}]
    assert req["inferenceConfig"] == {"maxTokens": 4000}
    tool = req["toolConfig"]["tools"][0]["toolSpec"]
    assert tool["name"] == TOOL_NAME
    assert req["toolConfig"]["toolChoice"] == {"tool": {"name": TOOL_NAME}}
    # $ref는 모델마다 지원이 달라서 펼쳐서 보낸다
    assert "$ref" not in json.dumps(tool["inputSchema"]["json"])
    assert "$defs" not in tool["inputSchema"]["json"]


async def test_design_schema_has_no_refs_and_tool_input_round_trips():
    design = json.loads(load_fixture("design_minimal_cost.json"))
    client = FakeClient(tool_response(design))
    raw = await call(make(client), DesignDraft)

    schema = client.requests[0]["toolConfig"]["tools"][0]["toolSpec"]["inputSchema"]["json"]
    assert "$ref" not in json.dumps(schema)
    assert DesignDraft.model_validate_json(raw) == DesignDraft.model_validate(design)
    assert "\\u" not in raw  # 한글을 \uXXXX로 바꾸지 않는다 (로그에서 읽을 수 있게)


async def test_tool_input_given_as_string_is_returned_as_is():
    raw = json.dumps(EXPLANATION, ensure_ascii=False)
    assert await call(make(FakeClient(tool_response(raw)))) == raw


async def test_text_answer_without_tool_is_left_to_pipeline_validation():
    body = json.dumps(EXPLANATION, ensure_ascii=False)
    raw = await call(make(FakeClient(text_response(f"```json\n{body}\n```"))))
    assert json.loads(raw) == EXPLANATION


async def test_empty_answer_is_an_output_error():
    with pytest.raises(LLMOutputError):
        await call(make(FakeClient(text_response(""))))


@pytest.mark.parametrize("stop", ["max_tokens", "guardrail_intervened", "content_filtered"])
async def test_cut_or_blocked_answer_is_an_output_error(stop):
    with pytest.raises(LLMOutputError):
        await call(make(FakeClient(tool_response(EXPLANATION, stop=stop))))


async def test_model_without_tool_choice_support_is_called_again_without_forcing():
    client = FakeClient(
        client_error("ValidationException", "This model doesn't support the toolChoice field."),
        tool_response(EXPLANATION),
        tool_response(EXPLANATION),
    )
    provider = make(client)
    assert json.loads(await call(provider)) == EXPLANATION
    assert "toolChoice" in client.requests[0]["toolConfig"]
    assert "toolChoice" not in client.requests[1]["toolConfig"]

    await call(provider)  # 한 번 알게 된 뒤로는 처음부터 강제하지 않는다
    assert len(client.requests) == 3
    assert "toolChoice" not in client.requests[2]["toolConfig"]


@pytest.mark.parametrize(
    ("code", "expected", "transient", "overloaded"),
    [
        ("ThrottlingException", LLMRateLimitError, True, False),
        ("ServiceUnavailableException", LLMProviderError, True, True),
        ("InternalServerException", LLMProviderError, True, False),
        ("ModelNotReadyException", LLMProviderError, True, False),
        ("ValidationException", LLMProviderError, False, False),
        ("ResourceNotFoundException", LLMProviderError, False, False),
    ],
)
async def test_client_errors_map_to_llm_errors(code, expected, transient, overloaded):
    with pytest.raises(expected) as info:
        await call(make(FakeClient(client_error(code))))
    assert type(info.value) is expected
    assert info.value.transient is transient
    assert info.value.overloaded is overloaded


@pytest.mark.parametrize("code", ["AccessDeniedException", "UnrecognizedClientException"])
async def test_permission_errors_are_config_errors(code):
    with pytest.raises(LLMConfigError):
        await call(make(FakeClient(client_error(code))))


async def test_timeouts_and_network_errors():
    with pytest.raises(LLMTimeoutError):
        await call(make(FakeClient(client_error("ModelTimeoutException"))))
    with pytest.raises(LLMTimeoutError):
        await call(make(FakeClient(ReadTimeoutError(endpoint_url="https://bedrock"))))
    with pytest.raises(LLMProviderError) as info:
        await call(make(FakeClient(EndpointConnectionError(endpoint_url="https://bedrock"))))
    assert info.value.transient is True


def test_factory_builds_converse_provider_with_default_models(monkeypatch):
    monkeypatch.setenv("AWS_REGION", "ap-southeast-2")
    settings = LLMSettings(_env_file=None, provider="bedrock_converse")

    design = build_provider(settings)
    explain = build_provider(settings, model=settings.resolved_explain_model)

    assert isinstance(design, ConverseProvider)
    assert design.model == "qwen.qwen3-235b-a22b-2507-v1:0"
    assert explain.model == "amazon.nova-pro-v1:0"
    assert design._client.meta.region_name == "ap-southeast-2"
