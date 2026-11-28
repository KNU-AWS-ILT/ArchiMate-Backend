"""설정값으로 제공자 인스턴스를 만든다. SDK import는 필요할 때만 한다."""

from archimate.llm.errors import LLMConfigError
from archimate.llm.providers.base import LLMProvider
from archimate.llm.settings import LLMSettings


def build_provider(settings: LLMSettings, *, model: str | None = None) -> LLMProvider:
    """model을 주면 설정의 설계용 모델 대신 그 모델을 쓴다 (노드 설명용 소형 모델 등)."""
    model = model or settings.resolved_model
    match settings.provider:
        case "mock":
            from archimate.llm.providers.mock import MockProvider

            return MockProvider()
        case "gemini":
            from archimate.llm.providers.gemini import GeminiProvider

            key = settings.gemini_api_key.get_secret_value() if settings.gemini_api_key else None
            return GeminiProvider(
                api_key=key,
                model=model,
                max_output_tokens=settings.max_output_tokens,
                timeout_s=settings.request_timeout_s,
            )
        case "bedrock":
            from archimate.llm.providers.claude import ClaudeProvider

            return ClaudeProvider.for_bedrock(
                model=model,
                aws_region=settings.aws_region,
                effort=settings.claude_effort,
                max_output_tokens=settings.max_output_tokens,
                timeout_s=settings.request_timeout_s,
                prompt_cache=settings.prompt_cache,
            )
        case "bedrock_converse":
            from archimate.llm.providers.converse import ConverseProvider

            return ConverseProvider(
                model=model,
                aws_region=settings.aws_region,
                max_output_tokens=settings.max_output_tokens,
                timeout_s=settings.request_timeout_s,
            )
        case "anthropic":
            from archimate.llm.providers.claude import ClaudeProvider

            return ClaudeProvider.for_anthropic(
                model=model,
                effort=settings.claude_effort,
                max_output_tokens=settings.max_output_tokens,
                timeout_s=settings.request_timeout_s,
                prompt_cache=settings.prompt_cache,
            )
    raise LLMConfigError(f"알 수 없는 LLM_PROVIDER: {settings.provider}")
