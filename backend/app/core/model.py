"""Provider creation for Web; execution uses the shared Model interface."""
from repo_agent.models.base import Model
from repo_agent.models.factory import create_model
from repo_agent.schemas import ModelResponse


class MockModelAdapter(Model):
    @property
    def name(self):
        return "unconfigured"

    async def complete(self, *, system_prompt, messages, tools):
        return ModelResponse(content="Set ANTHROPIC_API_KEY and MODEL_ID in backend/.env to use a real model.")


def create_model_adapter(settings):
    provider = settings.model_provider.lower()
    api_key = (settings.siliconflow_api_key or settings.openai_api_key or settings.anthropic_api_key
               if provider in {"siliconflow", "openai"} else settings.anthropic_api_key)
    base_url = settings.openai_base_url
    if provider == "siliconflow" and not base_url:
        base_url = "https://api.siliconflow.cn/v1"
    elif provider == "anthropic":
        base_url = settings.anthropic_base_url
    if not api_key and not base_url:
        return MockModelAdapter()
    return create_model(provider=provider, model=settings.model_id,
                        api_key=api_key, base_url=base_url,
                        max_tokens=settings.max_tokens)
