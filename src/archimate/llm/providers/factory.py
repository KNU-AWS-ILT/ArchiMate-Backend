"""설정값으로 제공자 인스턴스를 만든다. SDK import는 필요할 때만 한다."""

from archimate.llm.errors import LLMConfigError
from archimate.llm.providers.base import LLMProvider
from archimate.llm.settings import LLMSettings


def build_provider(settings: LLMSettings) -> LLMProvider:
    model = settings.resolved_model
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
            )
        case "anthropic":
            from archimate.llm.providers.claude import ClaudeProvider

            return ClaudeProvider.for_anthropic(
                model=model,
                effort=settings.claude_effort,
                max_output_tokens=settings.max_output_tokens,
                timeout_s=settings.request_timeout_s,
            )
    raise LLMConfigError(f"알 수 없는 LLM_PROVIDER: {settings.provider}")
