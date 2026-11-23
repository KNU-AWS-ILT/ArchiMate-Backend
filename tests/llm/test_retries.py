"""일시 오류 재시도(지수 백오프)와 대체 모델 (#23)."""

import json
import time
from types import SimpleNamespace

import anthropic
import httpx
import pytest
from google.genai import errors as genai_errors

from archimate.llm import (
    AllTiersFailedError,
    LLMProviderError,
    LLMRateLimitError,
    LLMTimeoutError,
    build_requirements,
    explain_node,
    generate_designs,
    pipeline,
)
from archimate.llm.providers.claude import ClaudeProvider
from archimate.llm.providers.gemini import GeminiProvider
from archimate.llm.providers.mock import MockProvider, load_fixture
from archimate.llm.settings import LLMSettings
from archimate.schemas import Tier


class FlakyProvider(MockProvider):
    """처음 몇 번은 준비한 예외를 던지고, 그 뒤로는 고정 응답을 준다."""

    def __init__(self, errors: list[Exception], *, model: str = "primary") -> None:
        super().__init__()
        self.model = model
        self._errors = list(errors)
        self.attempts = 0

    async def generate_json(self, **kwargs):
        self.attempts += 1
        if self._errors:
            raise self._errors.pop(0)
        return await super().generate_json(**kwargs)


@pytest.fixture(autouse=True)
def short_min_attempt(monkeypatch):
    monkeypatch.setattr(pipeline, "MIN_ATTEMPT_S", 0.05)


@pytest.fixture
def req():
    return build_requirements(json.loads(load_fixture("demo_answers.json")))


def settings(**kwargs) -> LLMSettings:
    kwargs.setdefault("retry_base_delay_s", 0.01)
    return LLMSettings(_env_file=None, provider="mock", **kwargs)


def overloaded() -> LLMProviderError:
    return LLMProviderError("503 과부하", transient=True)


async def run_one(req, provider, s):
    return await generate_designs(req, tiers=[Tier.MINIMAL_COST], provider=provider, settings=s)


# ── 일시 오류 재시도 ──


async def test_transient_error_is_retried_then_succeeds(req):
    provider = FlakyProvider([overloaded(), LLMRateLimitError("429")])
    result = await run_one(req, provider, settings(max_transient_retries=3))
    assert [d.tier for d in result.designs] == [Tier.MINIMAL_COST]
    assert provider.attempts == 4  # 설계 3번(실패 2 + 성공) + 운영 절차 1번


async def test_non_transient_error_is_not_retried(req):
    provider = FlakyProvider([LLMProviderError("400 잘못된 요청")])
    with pytest.raises(AllTiersFailedError) as e:
        await run_one(req, provider, settings(max_transient_retries=3))
    assert [f.reason for f in e.value.failures] == ["LLM_PROVIDER_ERROR"]
    assert provider.attempts == 1


async def test_gives_up_after_max_retries(req):
    provider = FlakyProvider([overloaded() for _ in range(10)])
    with pytest.raises(AllTiersFailedError):
        await run_one(req, provider, settings(max_transient_retries=2))
    assert provider.attempts == 3


async def test_backoff_grows_and_honors_retry_after(req, monkeypatch):
    delays: list[float] = []

    async def fake_sleep(seconds):
        delays.append(seconds)

    monkeypatch.setattr(pipeline.asyncio, "sleep", fake_sleep)
    provider = FlakyProvider(
        [overloaded(), overloaded(), LLMRateLimitError("429", retry_after_s=30)]
    )
    await run_one(req, provider, settings(max_transient_retries=3, retry_base_delay_s=1))
    assert 1 <= delays[0] < 2 and 2 <= delays[1] < 3  # base × 2^n + 무작위(0~base)
    assert delays[2] == 30


async def test_no_retry_when_backoff_would_pass_deadline(req):
    provider = FlakyProvider([overloaded() for _ in range(10)])
    s = settings(max_transient_retries=3, retry_base_delay_s=5, generation_deadline_s=1)
    started = time.monotonic()
    with pytest.raises(AllTiersFailedError):
        await run_one(req, provider, s)
    assert provider.attempts == 1
    assert time.monotonic() - started < 0.5


# ── 대체 모델 ──


@pytest.fixture
def fallback(monkeypatch):
    """build_provider가 대체 모델 제공자를 돌려주게 바꾼다."""
    built: list[FlakyProvider] = []

    def build(errors=()):
        def fake_build_provider(_settings, *, model=None):
            provider = FlakyProvider(list(errors), model=model)
            built.append(provider)
            return provider

        monkeypatch.setattr(pipeline, "build_provider", fake_build_provider)
        return built

    return build


async def test_rate_limit_switches_to_fallback_model(req, fallback):
    built = fallback()
    primary = FlakyProvider([LLMRateLimitError("429") for _ in range(10)])
    s = settings(max_transient_retries=1, fallback_model="backup")
    result = await run_one(req, primary, s)
    assert result.designs
    assert primary.attempts == 4  # 설계 2번(첫 시도 + 재시도 1), 운영 절차 2번
    assert [p.model for p in built] == ["backup", "backup"]  # 설계·운영 절차 각각 대체 모델로


async def test_fallback_is_tried_only_once(req, fallback):
    built = fallback(errors=[LLMRateLimitError("대체 모델도 429") for _ in range(10)])
    primary = FlakyProvider([LLMRateLimitError("429") for _ in range(10)])
    s = settings(max_transient_retries=1, fallback_model="backup")
    with pytest.raises(AllTiersFailedError) as e:
        await run_one(req, primary, s)
    assert [f.reason for f in e.value.failures] == ["LLM_RATE_LIMITED"]
    assert [p.attempts for p in built] == [1]


async def test_overload_switches_to_fallback_model(req, fallback):
    # 503 과부하는 모델별이라 다른 모델이면 될 수 있다 (개발 중 Gemini flash-lite에서 자주 발생)
    built = fallback()
    primary = FlakyProvider(
        [LLMProviderError("503", transient=True, overloaded=True) for _ in range(10)]
    )
    result = await run_one(req, primary, settings(max_transient_retries=1, fallback_model="backup"))
    assert result.designs and [p.model for p in built] == ["backup", "backup"]


async def test_network_error_does_not_switch_model(req, fallback):
    # 네트워크 끊김·500은 모델을 바꿔도 같으므로 대체 모델로 넘어가지 않는다
    built = fallback()
    primary = FlakyProvider([overloaded() for _ in range(10)])
    s = settings(max_transient_retries=1, fallback_model="backup")
    with pytest.raises(AllTiersFailedError):
        await run_one(req, primary, s)
    assert built == []


async def test_explain_does_not_use_fallback(req, fallback):
    built = fallback()
    design = (await run_one(req, MockProvider(), settings())).designs[0]
    primary = FlakyProvider([LLMRateLimitError("429") for _ in range(10)])
    s = settings(max_transient_retries=1, fallback_model="backup")
    with pytest.raises(LLMRateLimitError):
        await explain_node(req, design, "app_db", provider=primary, settings=s)
    assert built == []


# ── 제공자 오류 분류 ──


def _response(status: int, headers: dict | None = None) -> httpx.Response:
    return httpx.Response(status, headers=headers, request=httpx.Request("POST", "https://x"))


def _claude_raising(exc: Exception) -> ClaudeProvider:
    async def create(**_kwargs):
        raise exc

    client = SimpleNamespace(messages=SimpleNamespace(create=create))
    return ClaudeProvider(client, name="bedrock", model="m", effort="low", max_output_tokens=10)


async def _claude_error(exc: Exception) -> Exception:
    with pytest.raises(Exception) as e:
        await _claude_raising(exc).generate_json(system="", messages=[], output_model=type(None))
    return e.value


@pytest.mark.parametrize(
    ("exc", "kind", "transient", "retry_after"),
    [
        (
            anthropic.RateLimitError(
                "429", response=_response(429, {"retry-after": "3"}), body=None
            ),
            LLMRateLimitError,
            True,
            3.0,
        ),
        (
            anthropic.InternalServerError("529", response=_response(529), body=None),
            LLMProviderError,
            True,
            None,
        ),
        (
            anthropic.BadRequestError("400", response=_response(400), body=None),
            LLMProviderError,
            False,
            None,
        ),
        (
            anthropic.APIConnectionError(request=httpx.Request("POST", "https://x")),
            LLMProviderError,
            True,
            None,
        ),
    ],
)
async def test_claude_error_classification(exc, kind, transient, retry_after, monkeypatch):
    monkeypatch.setattr(anthropic, "transform_schema", lambda _m: {})
    err = await _claude_error(exc)
    assert type(err) is kind
    assert err.transient is transient and err.retry_after_s == retry_after


async def test_claude_timeout_is_not_transient(monkeypatch):
    monkeypatch.setattr(anthropic, "transform_schema", lambda _m: {})
    err = await _claude_error(anthropic.APITimeoutError(request=httpx.Request("POST", "https://x")))
    assert isinstance(err, LLMTimeoutError) and err.transient is False


@pytest.mark.parametrize(
    ("exc", "kind", "transient"),
    [
        (genai_errors.APIError(429, {}), LLMRateLimitError, True),
        (genai_errors.APIError(503, {}), LLMProviderError, True),
        (genai_errors.APIError(400, {}), LLMProviderError, False),
        (httpx.ConnectError("끊김"), LLMProviderError, True),
        (httpx.ReadTimeout("느림"), LLMTimeoutError, False),
    ],
)
async def test_gemini_error_classification(exc, kind, transient):
    async def generate_content(**_kwargs):
        raise exc

    provider = GeminiProvider(api_key="test", model="m", max_output_tokens=10, timeout_s=5)
    provider._client = SimpleNamespace(
        aio=SimpleNamespace(models=SimpleNamespace(generate_content=generate_content))
    )
    with pytest.raises(LLMProviderError) as e:
        await provider.generate_json(system="", messages=[], output_model=LLMSettings)
    assert type(e.value) is kind and e.value.transient is transient


async def test_overload_flag_by_provider(monkeypatch):
    monkeypatch.setattr(anthropic, "transform_schema", lambda _m: {})
    err = await _claude_error(
        anthropic.InternalServerError("529", response=_response(529), body=None)
    )
    assert err.overloaded is True
    err = await _claude_error(
        anthropic.InternalServerError("500", response=_response(500), body=None)
    )
    assert err.overloaded is False

    async def generate_content(**_kwargs):
        raise genai_errors.APIError(503, {})

    provider = GeminiProvider(api_key="test", model="m", max_output_tokens=10, timeout_s=5)
    provider._client = SimpleNamespace(
        aio=SimpleNamespace(models=SimpleNamespace(generate_content=generate_content))
    )
    with pytest.raises(LLMProviderError) as e:
        await provider.generate_json(system="", messages=[], output_model=LLMSettings)
    assert e.value.overloaded is True


async def test_claude_caches_system_prompt(monkeypatch, caplog):
    monkeypatch.setattr(anthropic, "transform_schema", lambda _m: {})
    sent: dict = {}

    async def create(**kwargs):
        sent.update(kwargs)
        return SimpleNamespace(
            stop_reason="end_turn",
            content=[SimpleNamespace(type="text", text="{}")],
            usage=SimpleNamespace(
                input_tokens=10,
                cache_read_input_tokens=3000,
                cache_creation_input_tokens=0,
                output_tokens=50,
            ),
        )

    client = SimpleNamespace(messages=SimpleNamespace(create=create))
    provider = ClaudeProvider(client, name="bedrock", model="m", effort="low", max_output_tokens=10)
    with caplog.at_level("INFO", logger="archimate.llm.providers.claude"):
        assert (
            await provider.generate_json(system="설계 지시", messages=[], output_model=type(None))
            == "{}"
        )
    assert sent["system"] == [
        {"type": "text", "text": "설계 지시", "cache_control": {"type": "ephemeral"}}
    ]
    assert "cache_read=3000" in caplog.text
